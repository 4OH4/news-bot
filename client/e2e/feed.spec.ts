import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

type Item = Record<string, unknown> & { id: string; title: string; url: string; display: string };
type FeedBody = Record<string, unknown> & { items: Item[] };

const example = JSON.parse(
  readFileSync(new URL("../../docs/feed.example.json", import.meta.url), "utf-8"),
) as FeedBody;

const cards = (page: Page) => page.locator("[data-testid='feed-item']");
const refresh = (page: Page) => page.getByRole("button", { name: /refresh/i });

/** Serves `body` at /api/feed; calling it again replaces the answer. */
async function serve(page: Page, body: FeedBody, headers: Record<string, string> = {}) {
  await page.unroute("**/api/feed");
  await page.route("**/api/feed", (route) => route.fulfill({ json: body, headers }));
}

test.use({ viewport: { width: 360, height: 740 } });

test("the example feed lists all five items in the given order", async ({ page }) => {
  await serve(page, example);
  await page.goto("/");
  await expect(cards(page)).toHaveCount(5);
  const texts = await cards(page).allInnerTexts();
  example.items.forEach((item, i) => expect(texts[i]).toContain(item.title));
});

test("an article item goes to the reader route and a link item goes to its url", async ({
  page,
}) => {
  await serve(page, example);
  await page.goto("/");
  const article = example.items.find((i) => i.display === "article")!;
  const link = example.items.find((i) => i.display === "link")!;

  await expect(page.getByRole("link", { name: article.title }).first()).toHaveAttribute(
    "href",
    `/article/${article.id}`,
  );

  const linkEl = page.getByRole("link", { name: link.title }).first();
  await expect(linkEl).toHaveAttribute("href", link.url);
  await expect(linkEl).toHaveAttribute("target", /_blank/);
  await expect(linkEl).toHaveAttribute("rel", /noopener/);
  await expect(linkEl).toHaveAttribute("rel", /noreferrer/);
});

test("link items are marked as opening the site, and no raw score is shown", async ({ page }) => {
  await serve(page, example);
  await page.goto("/");
  await expect(cards(page)).toHaveCount(5);
  await expect(page.getByText(/opens the site/i)).toHaveCount(
    example.items.filter((i) => i.display === "link").length,
  );
  expect(await page.locator("main").innerText()).not.toContain("3.42");
});

test("an item with null author, summary, published_at and lead_image renders without 'null'", async ({
  page,
}) => {
  await serve(page, example);
  await page.goto("/");
  const last = cards(page).last();
  await expect(last).toContainText("Notes on a rainy hackathon");
  expect(await last.innerText()).not.toMatch(/null|undefined/i);
  await expect(last.locator("img")).toHaveCount(0);
});

test("lead images are lazy, send no referrer, and hide themselves when they fail", async ({
  page,
}) => {
  await page.route("**/*.jpg", (route) => route.abort());
  await page.route("**/*.png", (route) => route.abort());
  await serve(page, example);
  await page.goto("/");
  const img = page.locator("[data-testid='feed-item'] img").first();
  await expect(img).toHaveAttribute("loading", "lazy");
  await expect(img).toHaveAttribute("referrerpolicy", "no-referrer");
  await expect(img).toBeHidden();
});

test("unknown top-level and item fields are ignored", async ({ page }) => {
  await serve(page, {
    ...example,
    future_top_field: { a: 1 },
    items: example.items.map((i) => ({ ...i, future_item_field: [1, 2] })),
  });
  await page.goto("/");
  await expect(cards(page)).toHaveCount(5);
});

test("an unknown extraction_status shows as a plain label and does not crash", async ({ page }) => {
  await serve(page, {
    ...example,
    items: example.items.map((i, n) => (n === 1 ? { ...i, extraction_status: "quantum_flux" } : i)),
  });
  await page.goto("/");
  await expect(cards(page)).toHaveCount(5);
  await expect(page.getByText("quantum_flux")).toBeVisible();
});

test("schema_version 2 on refresh keeps the good feed and says an update is needed", async ({
  page,
}) => {
  await serve(page, example);
  await page.goto("/");
  await expect(cards(page)).toHaveCount(5);
  await serve(page, { ...example, schema_version: 2, items: [] });
  await refresh(page).click();
  await expect(page.getByText(/update is needed|update needed/i)).toBeVisible();
  await expect(cards(page)).toHaveCount(5);
});

test("a 304 on refresh leaves the list and scroll position unchanged", async ({ page }) => {
  let calls = 0;
  let conditional = "";
  await page.route("**/api/feed", (route) => {
    calls += 1;
    if (calls === 1) return route.fulfill({ json: example, headers: { ETag: '"v1"' } });
    conditional = route.request().headers()["if-none-match"] ?? "";
    return route.fulfill({ status: 304, body: "" });
  });
  await page.goto("/");
  await expect(cards(page)).toHaveCount(5);
  await page.evaluate(() => window.scrollTo(0, 120));
  const before = await page.evaluate(() => window.scrollY);
  await refresh(page).click();
  await expect.poll(() => calls).toBe(2);
  expect(conditional).toBe('"v1"');
  await expect(cards(page)).toHaveCount(5);
  expect(await page.evaluate(() => window.scrollY)).toBe(before);
});

test("a network failure on refresh keeps the list and shows a notice", async ({ page }) => {
  let calls = 0;
  await page.route("**/api/feed", (route) => {
    calls += 1;
    return calls === 1 ? route.fulfill({ json: example }) : route.abort("failed");
  });
  await page.goto("/");
  await expect(cards(page)).toHaveCount(5);
  await refresh(page).click();
  await expect(page.getByRole("status").or(page.getByRole("alert"))).toBeVisible();
  await expect(cards(page)).toHaveCount(5);
});

test("a 503 on first load says the first feed is not ready", async ({ page }) => {
  await page.route("**/api/feed", (route) => route.fulfill({ status: 503, body: "" }));
  await page.goto("/");
  await expect(page.getByText(/not ready/i)).toBeVisible();
  await expect(cards(page)).toHaveCount(0);
});

test("the app makes one startup feed request, replacing the old probe", async ({ page }) => {
  let calls = 0;
  await page.route("**/api/feed", (route) => {
    calls += 1;
    return route.fulfill({ json: example });
  });
  await page.goto("/");
  await expect(cards(page)).toHaveCount(5);
  expect(calls).toBe(1);
});

test("the page has no horizontal scroll at 360 px", async ({ page }) => {
  await serve(page, example);
  await page.goto("/");
  await expect(cards(page)).toHaveCount(5);
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
});

test("dark mode follows the system setting", async ({ page }) => {
  await serve(page, example);
  const bg = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/");
  await expect(cards(page)).toHaveCount(5);
  const light = await bg();
  await page.emulateMedia({ colorScheme: "dark" });
  expect(await bg()).not.toBe(light);
});
