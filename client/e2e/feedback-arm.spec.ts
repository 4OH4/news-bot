import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

// #327: the card's view and the item's A/B arm travel with the open, through the full page load
// into the reader, and come back on its `read` event. The feed is docs/feed.example.json with an
// arm set on the top article; the HTML is invented.

type Item = Record<string, unknown> & { id: string; title: string; display: string };
type FeedBody = Record<string, unknown> & { items: Item[] };

const example = JSON.parse(
  readFileSync(new URL("../../docs/feed.example.json", import.meta.url), "utf-8"),
) as FeedBody;

const top = { ...example.items.find((i) => i.display === "article")!, arm: "personal" };
const feed: FeedBody = { ...example, items: [top] };

test.use({ serviceWorkers: "block", viewport: { width: 360, height: 740 } });

async function stubApi(page: Page): Promise<Record<string, unknown>[]> {
  const posted: Record<string, unknown>[] = [];
  await page.route("**/api/feed?*", (route) =>
    route.fulfill({ json: feed, headers: { ETag: '"v1"' } }),
  );
  await page.route("**/api/articles/*", (route) =>
    route.fulfill({ contentType: "text/html", body: "<p>Invented paragraph.</p>" }),
  );
  await page.route("**/api/feedback", (route) => {
    posted.push(JSON.parse(route.request().postData() ?? "{}") as Record<string, unknown>);
    return route.fulfill({ status: 204, body: "" });
  });
  return posted;
}

test("open and read carry the Feed view and the item's arm across the reader and back", async ({
  page,
}) => {
  const posted = await stubApi(page);
  await page.clock.install();
  await page.goto("/");
  await page.getByRole("link", { name: top.title }).first().click();
  await expect(page.getByText("Invented paragraph.")).toBeVisible();
  await page.clock.fastForward(7_000);
  await page.goBack();
  await expect(page.locator("[data-testid='feed-item']").first()).toBeVisible();

  await expect.poll(() => posted.map((e) => e.event).sort()).toEqual(["open", "read"]);
  for (const event of posted) {
    expect(event).toMatchObject({ article_id: top.id, view: "feed", arm: "personal" });
  }
});

test("a reader opened by address, with no card tap, sends neither field", async ({ page }) => {
  const posted = await stubApi(page);
  await page.clock.install();
  await page.goto(`/article/${top.id}`);
  await expect(page.getByText("Invented paragraph.")).toBeVisible();
  // The item arrives separately from the text; reading starts only once it is known.
  await expect(page.getByRole("heading", { name: top.title })).toBeVisible();
  await page.clock.fastForward(7_000);
  await page.goto("/");

  await expect.poll(() => posted.filter((e) => e.event === "read")).toHaveLength(1);
  const read = posted.find((e) => e.event === "read")!;
  expect("view" in read).toBe(false);
  expect("arm" in read).toBe(false);
});
