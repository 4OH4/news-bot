import { readFileSync } from "node:fs";
import { expect, test, type Locator, type Page } from "@playwright/test";

// #254: every screen is a full page load, so a tap followed at once by leaving the screen ends
// the page before an IndexedDB write could finish. Each test taps and navigates in the same step,
// with no wait between, then expects the event among the POST /api/feedback bodies sent by the
// next page. The feed is docs/feed.example.json; the article HTML is invented.
test.use({ serviceWorkers: "block", viewport: { width: 360, height: 740 } });

type Item = { id: string; title: string; display: string; domain: string; source_id: string };
const feed = JSON.parse(
  readFileSync(new URL("../../docs/feed.example.json", import.meta.url), "utf-8"),
) as { items: Item[] } & Record<string, unknown>;
const article = feed.items.find((i) => i.display === "article")!;
const other = feed.items.find((i) => i.source_id !== article.source_id)!;
const bodyText = "Invented paragraph for the feedback-leave test.";

/** Stubs `/api/**` and returns the feedback bodies POSTed, in order. */
async function stubApi(page: Page): Promise<Record<string, unknown>[]> {
  const posted: Record<string, unknown>[] = [];
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/feed") return route.fulfill({ json: feed, headers: { ETag: '"v1"' } });
    if (path === "/api/feedback") {
      posted.push(route.request().postDataJSON() as Record<string, unknown>);
      return route.fulfill({ status: 204, body: "" });
    }
    if (path.startsWith("/api/articles/")) {
      return route.fulfill({ contentType: "text/html; charset=utf-8", body: `<p>${bodyText}</p>` });
    }
    return route.fulfill({ status: 204, body: "" });
  });
  return posted;
}

function card(page: Page, item: Item): Locator {
  return page.getByTestId("feed-item").filter({ hasText: item.title });
}

/** The card's menu action, with the menu opened if the action isn't showing. */
async function menuAction(scope: Locator, name: RegExp): Promise<Locator> {
  const action = scope.getByRole("button", { name });
  if (!(await action.isVisible())) {
    await scope.locator("summary, button[aria-haspopup], button[aria-expanded]").first().click();
  }
  await expect(action).toBeVisible();
  return action;
}

/**
 * Keeps a readwrite transaction on the feedback queue busy until the page goes, so no queue write
 * from this page can finish before it unloads. That makes the race the owner hits (a write a few
 * milliseconds too slow) certain instead of likely. Other stores are left free.
 */
async function holdQueueWrites(page: Page): Promise<void> {
  await page.evaluate(
    () =>
      new Promise<void>((resolve, reject) => {
        const req = indexedDB.open("newsfeed-feedback");
        req.onupgradeneeded = () => {
          req.transaction?.abort();
          reject(new Error("the app has not created its feedback database yet"));
        };
        req.onerror = () => reject(req.error);
        req.onsuccess = () => {
          const store = req.result.transaction("queue", "readwrite").objectStore("queue");
          const spin = () => {
            store.count().onsuccess = spin;
          };
          spin();
          resolve();
        };
      }),
  );
}

/**
 * Clicks `target` and starts a full page load to `path` in the same step, with no wait, while
 * this page's queue writes are held.
 */
async function tapAndLeave(target: Locator, path: string): Promise<void> {
  await holdQueueWrites(target.page());
  await target.evaluate((el, to) => {
    (el as HTMLElement).click();
    location.href = to;
  }, path);
}

function events(posted: Record<string, unknown>[], name: string): unknown[][] {
  return posted.filter((e) => e.event === name).map((e) => [e.article_id, e.value]);
}

async function feedLoaded(page: Page): Promise<void> {
  await expect(page).toHaveURL(/\/$/);
  await expect(card(page, other)).toBeVisible();
}

test("Like tapped on the reader and the page left at once still posts like 1", async ({ page }) => {
  const posted = await stubApi(page);
  await page.goto("/");
  await card(page, article).getByRole("link", { name: article.title }).click();
  await expect(page.getByText(bodyText)).toBeVisible();
  const like = page.getByRole("button", { name: /^like$/i });
  await expect(like).toHaveAttribute("aria-pressed", "false");

  await tapAndLeave(like, "/");

  await feedLoaded(page);
  await expect.poll(() => events(posted, "like")).toEqual([[article.id, 1]]);
});

for (const [label, name, event] of [
  ["Dismiss", /dismiss/i, "dismiss"],
  ["Mute", new RegExp(`mute ${article.domain}`, "i"), "mute_source"],
] as const) {
  test(`${label} tapped on a card and the page left at once still posts ${event} 1`, async ({
    page,
  }) => {
    const posted = await stubApi(page);
    await page.goto("/");
    const action = await menuAction(card(page, article), name);

    await tapAndLeave(action, "/settings");

    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
    await expect.poll(() => events(posted, event)).toEqual([[article.id, 1]]);
    await page.goto("/");
    await feedLoaded(page);
    await expect(page.getByText(article.title)).toHaveCount(0);
  });
}

test("Unmute tapped in Settings and the page left at once still posts mute_source 0", async ({
  page,
}) => {
  const posted = await stubApi(page);
  await page.goto("/");
  const mute = await menuAction(card(page, article), new RegExp(`mute ${article.domain}`, "i"));
  await mute.click();
  await expect.poll(() => events(posted, "mute_source")).toEqual([[article.id, 1]]);
  await page.goto("/settings");
  const unmute = page.getByRole("button", { name: /unmute/i });
  await expect(unmute).toBeVisible();

  await tapAndLeave(unmute, "/");

  await feedLoaded(page);
  await expect
    .poll(() => events(posted, "mute_source"))
    .toEqual([
      [article.id, 1],
      [article.id, 0],
    ]);
});
