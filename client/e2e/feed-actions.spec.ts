import { readFileSync } from "node:fs";
import { expect, test, type Locator, type Page } from "@playwright/test";

// #217: open, dismiss and mute on the feed, and the Settings screen. Every screen is a full page
// load, so each test moves between screens the way the owner does and checks what survived.
// The feed is docs/feed.example.json; article HTML is invented.
test.use({ serviceWorkers: "block", viewport: { width: 360, height: 740 } });

type Item = { id: string; title: string; display: string; domain: string; source_id: string };
const feed = JSON.parse(
  readFileSync(new URL("../../docs/feed.example.json", import.meta.url), "utf-8"),
) as { items: Item[] } & Record<string, unknown>;
const article = feed.items.find((i) => i.display === "article")!;
const other = feed.items.find((i) => i.id !== article.id)!;

/** Stubs `/api/**` and returns the feedback bodies POSTed, in order. */
async function stubApi(page: Page): Promise<Record<string, unknown>[]> {
  const posted: Record<string, unknown>[] = [];
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/feed") return route.fulfill({ json: feed });
    if (path === "/api/feedback") {
      posted.push(route.request().postDataJSON() as Record<string, unknown>);
      return route.fulfill({ status: 204, body: "" });
    }
    if (path.startsWith("/api/articles/")) {
      return route.fulfill({
        contentType: "text/html; charset=utf-8",
        body: "<p>Invented paragraph for the feed-actions test.</p>",
      });
    }
    return route.fulfill({ status: 204, body: "" });
  });
  return posted;
}

function card(page: Page, item: Item): Locator {
  return page.getByTestId("feed-item").filter({ hasText: item.title });
}

/** Opens the card's menu by keyboard if the action isn't showing, then picks the action by keyboard. */
async function choose(page: Page, scope: Locator, name: RegExp): Promise<void> {
  const action = scope.getByRole("button", { name });
  if (!(await action.isVisible())) {
    await scope.locator("summary, button[aria-haspopup], button[aria-expanded]").first().focus();
    await page.keyboard.press("Enter");
  }
  await expect(action).toBeVisible();
  await action.focus();
  await page.keyboard.press("Enter");
}

/** The same rule as client/test/cards.ts `looksSeen`. */
function looksSeen(scope: Locator): Promise<boolean> {
  return scope.evaluate((el) =>
    [el, ...el.querySelectorAll("*")].some(
      (e) =>
        [...e.classList].some((c) => /(^|[-_])seen($|[-_])/i.test(c)) ||
        (e.getAttribute("data-seen") ?? "false") !== "false",
    ),
  );
}

function events(posted: Record<string, unknown>[], name: string) {
  return posted.filter((e) => e.event === name).map((e) => [e.article_id, e.value]);
}

test("a dismissed story is still hidden after a reload, with no Undo row", async ({ page }) => {
  const posted = await stubApi(page);
  await page.goto("/");
  await expect(card(page, article)).toBeVisible();

  await choose(page, card(page, article), /dismiss/i);
  await expect(page.getByText(article.title)).toHaveCount(0);
  await expect(page.getByRole("button", { name: /undo/i })).toBeVisible();
  await expect.poll(() => events(posted, "dismiss")).toEqual([[article.id, 1]]);

  await page.reload();
  await expect(card(page, other)).toBeVisible();
  await expect(page.getByText(article.title)).toHaveCount(0);
  await expect(page.getByRole("button", { name: /undo/i })).toHaveCount(0);
});

test("tapping an article sends open, and back on the feed its card shows as seen", async ({
  page,
}) => {
  const posted = await stubApi(page);
  await page.goto("/");
  expect(await looksSeen(card(page, article))).toBe(false);

  await card(page, article).getByRole("link", { name: article.title }).click();
  await expect(page).toHaveURL(new RegExp(`/article/${article.id}$`));
  await page.getByRole("link", { name: "Back to the feed" }).click();

  await expect(card(page, article)).toBeVisible();
  await expect.poll(() => events(posted, "open")).toEqual([[article.id, null]]);
  await expect.poll(() => looksSeen(card(page, article))).toBe(true);
  expect(await looksSeen(card(page, other))).toBe(false);
});

test("a source muted on the feed is unmuted in Settings, and its story comes back", async ({
  page,
}) => {
  const posted = await stubApi(page);
  await page.goto("/");
  await choose(page, card(page, article), new RegExp(`mute ${article.domain}`, "i"));
  await expect(page.getByText(article.title)).toHaveCount(0);

  await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: /settings/i }).click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(page.getByText(article.domain).first()).toBeVisible();
  await page.getByRole("button", { name: /unmute/i }).click();
  await expect(page.getByText(article.domain)).toHaveCount(0);

  await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: /feed/i }).click();
  await expect(card(page, article)).toBeVisible();
  await expect
    .poll(() => events(posted, "mute_source"))
    .toEqual([
      [article.id, 1],
      [article.id, 0],
    ]);
});
