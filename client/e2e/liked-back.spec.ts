import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

// #258: the reader's Back link follows the list the article was opened from, and that memory
// belongs to one history entry. Every screen is a full page load under the production headers
// (Referrer-Policy: no-referrer), so these tap the real link and read `history.length` to tell
// "went back" (length unchanged) from "followed the link" (one more entry).

type Item = Record<string, unknown> & { id: string; title: string; display: string };
type FeedBody = Record<string, unknown> & { items: Item[] };

const example = JSON.parse(
  readFileSync(new URL("../../docs/feed.example.json", import.meta.url), "utf-8"),
) as FeedBody;

const [first, second] = example.items.filter((i) => i.display === "article") as [Item, Item];
const bodyText = (id: string) => `Invented paragraph for back-link test ${id}.`;

test.use({ viewport: { width: 360, height: 740 } });

async function stubApi(page: Page): Promise<void> {
  await page.route("**/api/feed", (route) =>
    route.fulfill({ json: example, headers: { ETag: '"v1"' } }),
  );
  await page.route("**/api/articles/*", (route) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split("/").pop()!);
    return route.fulfill({
      status: 200,
      contentType: "text/html; charset=utf-8",
      body: `<p>${bodyText(id)}</p>`,
    });
  });
  await page.route("**/api/feedback", (route) => route.fulfill({ status: 204, body: "" }));
}

const historyLength = (page: Page) => page.evaluate(() => history.length);
const feedItems = (page: Page) => page.locator("[data-testid='feed-item']");

/** Taps the story's link with a DOM click, so the page is not scrolled first. */
async function openStory(page: Page, item: Item): Promise<void> {
  await page
    .getByRole("link", { name: item.title })
    .first()
    .evaluate((el) => (el as HTMLElement).click());
  await expect(page.getByText(bodyText(item.id))).toBeVisible();
}

test("an article opened from Liked says Back to Liked and returns to Liked at its own scroll position", async ({
  page,
}) => {
  await stubApi(page);
  await page.goto("/");
  await expect(feedItems(page)).toHaveCount(5);
  await page.evaluate(() => window.scrollTo(0, 150));
  await openStory(page, first);
  await page.getByRole("button", { name: /^like$/i }).click();
  await expect(page.getByRole("button", { name: /^liked$/i })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await page.getByRole("link", { name: "Back to the feed" }).click();
  await expect(feedItems(page)).toHaveCount(5);

  await page.getByRole("link", { name: "Liked", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Liked", level: 1 })).toBeVisible();
  await page.getByRole("link", { name: first.title }).click();
  await expect(page.getByText(bodyText(first.id))).toBeVisible();

  const back = page.getByRole("link", { name: "Back to Liked" });
  await expect(back).toHaveAttribute("href", "/liked");
  await expect(page.getByRole("link", { name: "Back to the feed" })).toHaveCount(0);
  await back.click();

  await expect(page).toHaveURL(/\/liked$/);
  await expect(page.getByRole("heading", { name: "Liked", level: 1 })).toBeVisible();
  // The feed was left at 150 and Liked at 0: Liked must not take the feed's position.
  await page.waitForTimeout(300);
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
});

test("an article opened directly after returning to the feed goes to the feed by the link, not by history", async ({
  page,
}) => {
  await stubApi(page);
  await page.goto("/");
  await expect(feedItems(page)).toHaveCount(5);
  await openStory(page, first);
  await page.getByRole("link", { name: "Back to the feed" }).click();
  await expect(feedItems(page)).toHaveCount(5);

  await page.goto(`/article/${second.id}`);
  await expect(page.getByText(bodyText(second.id))).toBeVisible();
  const before = await historyLength(page);
  const back = page.getByRole("link", { name: "Back to the feed" });
  await expect(back).toHaveAttribute("href", "/");
  await back.click();

  await expect(page).toHaveURL(/\/$/);
  await expect(feedItems(page)).toHaveCount(5);
  expect(await historyLength(page)).toBe(before + 1);
});

test("an article opened directly in a new tab goes to / by the link", async ({ context }) => {
  const page = await context.newPage();
  await stubApi(page);
  await page.goto(`/article/${first.id}`);
  await expect(page.getByText(bodyText(first.id))).toBeVisible();
  const before = await historyLength(page);
  await page.getByRole("link", { name: "Back to the feed" }).click();

  await expect(page).toHaveURL(/\/$/);
  await expect(feedItems(page)).toHaveCount(5);
  expect(await historyLength(page)).toBe(before + 1);
});

test("after a reload of an article opened from the feed, Back still goes back in history", async ({
  page,
}) => {
  await stubApi(page);
  await page.goto("/");
  await expect(feedItems(page)).toHaveCount(5);
  await openStory(page, first);

  await page.reload();
  await expect(page.getByText(bodyText(first.id))).toBeVisible();
  const before = await historyLength(page);
  await page.getByRole("link", { name: "Back to the feed" }).click();

  await expect(page).toHaveURL(/\/$/);
  await expect(feedItems(page)).toHaveCount(5);
  expect(await historyLength(page)).toBe(before);
});
