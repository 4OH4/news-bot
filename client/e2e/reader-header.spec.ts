import { readFileSync } from "node:fs";
import { expect, test, type Locator, type Page } from "@playwright/test";

// #300: the reader has no top bar; Like is a small outline heart in a right column beside the
// meta line, filled with --like-fill when liked. Runs against the production build and headers.

type Item = Record<string, unknown> & { id: string; title: string };
type FeedBody = Record<string, unknown> & { items: Item[] };

const example = JSON.parse(
  readFileSync(new URL("../../docs/feed.example.json", import.meta.url), "utf-8"),
) as FeedBody;
const item = example.items[0]!;
const LONG_AUTHOR = "Bartholomew Featherstonehaugh-Cholmondeley-Marjoribanks-Wolstenholme";
const longAuthorFeed = { ...example, items: [{ ...item, author: LONG_AUTHOR }, ...example.items.slice(1)] };

async function stubApi(page: Page, feed: FeedBody = example): Promise<Record<string, unknown>[]> {
  const posted: Record<string, unknown>[] = [];
  await page.route("**/api/feed?*", (route) => route.fulfill({ json: feed }));
  await page.route(`**/api/articles/${item.id}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html; charset=utf-8",
      body: "<p>Invented paragraph for the header test.</p>",
    }),
  );
  await page.route("**/api/feedback", (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown> | null;
    if (body) posted.push(body);
    return route.fulfill({ status: 204, body: "" });
  });
  return posted;
}

async function openReader(page: Page): Promise<void> {
  await page.goto(`/article/${item.id}`);
  await expect(page.getByText("Invented paragraph for the header test.")).toBeVisible();
  await expect(like(page)).toBeVisible();
}

const like = (page: Page) => page.getByRole("button", { name: "Like" });
const heart = (page: Page) => like(page).locator("svg");
const style = (el: Locator, prop: string) =>
  el.evaluate((e, p) => getComputedStyle(e).getPropertyValue(p), prop);

/** "#rrggbb" custom property as the "rgb(r, g, b)" a computed colour reports. */
async function token(page: Page, name: string): Promise<string> {
  const hex = await page.evaluate(
    (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim(),
    name,
  );
  const n = (i: number) => parseInt(hex.slice(i, i + 2), 16);
  return `rgb(${n(1)}, ${n(3)}, ${n(5)})`;
}

test.use({ viewport: { width: 390, height: 844 } });

test("the reader has no nav, back link or theme button, and the first heading is the title", async ({
  page,
}) => {
  await stubApi(page);
  await openReader(page);
  await expect(page.locator("nav")).toHaveCount(0);
  await expect(page.getByRole("link", { name: /^back to/i })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /theme/i })).toHaveCount(0);
  await expect(page.getByRole("heading").first()).toHaveText(item.title);
});

test("Like sits right of the meta line below the title, outlined, and fills with --like-fill when tapped", async ({
  page,
}) => {
  const posted = await stubApi(page);
  await openReader(page);

  const title = await page.locator("h1").boundingBox();
  const meta = await page.locator(".reader-meta").boundingBox();
  const btn = await like(page).boundingBox();
  expect(btn!.y).toBeGreaterThanOrEqual(title!.y + title!.height - 1);
  expect(btn!.x).toBeGreaterThanOrEqual(meta!.x + meta!.width - 1);
  // Top of the row: the button starts within the meta line's first line, not below the link.
  expect(btn!.y).toBeLessThan(meta!.y + meta!.height);

  await expect(like(page)).toHaveAttribute("aria-pressed", "false");
  expect(await style(heart(page), "fill")).toBe("none");

  await like(page).click();
  await expect(like(page)).toHaveAttribute("aria-pressed", "true");
  expect(await style(heart(page), "fill")).toBe(await token(page, "--like-fill"));
  await expect.poll(() => posted.map((e) => [e.article_id, e.event, e.value])).toContainEqual([
    item.id,
    "like",
    1,
  ]);

  await like(page).click();
  await expect(like(page)).toHaveAttribute("aria-pressed", "false");
  expect(await style(heart(page), "fill")).toBe("none");
});

test("the Like button is at least 44 by 44 with a heart of about 20px", async ({ page }) => {
  await stubApi(page);
  await openReader(page);
  const box = await like(page).boundingBox();
  expect(box!.width).toBeGreaterThanOrEqual(44);
  expect(box!.height).toBeGreaterThanOrEqual(44);
  const icon = await heart(page).boundingBox();
  expect(icon!.width).toBeGreaterThanOrEqual(18);
  expect(icon!.width).toBeLessThanOrEqual(24);
});

for (const width of [390, 320]) {
  test(`a long author name wraps in the left column at ${width}px, with no horizontal scroll`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 844 });
    await stubApi(page, longAuthorFeed);
    await openReader(page);
    const meta = await page.locator(".reader-meta").boundingBox();
    const btn = await like(page).boundingBox();
    expect(meta!.x + meta!.width).toBeLessThanOrEqual(btn!.x + 1);
    expect(meta!.height).toBeGreaterThan(30);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });
}

test("in the dark theme the liked heart takes its stroke and fill from the dark tokens", async ({
  page,
}) => {
  await page.addInitScript(() => localStorage.setItem("newsfeed-theme", "dark"));
  await stubApi(page);
  await openReader(page);
  const dark = await token(page, "--like-fill");
  await like(page).click();
  await expect(like(page)).toHaveAttribute("aria-pressed", "true");
  expect(await style(heart(page), "fill")).toBe(dark);
  expect(await style(heart(page), "stroke")).toBe(await token(page, "--muted"));
});
