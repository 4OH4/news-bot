import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

// #289: News, Feed and Longer tabs, the header icons, per-tab scroll and offline, in the
// production build. Data is invented from docs/feed.example.json.
test.use({ viewport: { width: 390, height: 844 }, serviceWorkers: "block" });

type Item = Record<string, unknown> & { id: string; title: string };
const example = JSON.parse(
  readFileSync(new URL("../../docs/feed.example.json", import.meta.url), "utf-8"),
) as { items: Item[] } & Record<string, unknown>;

/** 40 invented article items for a view, so the list is taller than the screen. */
function items(view: string): Item[] {
  const base = example.items.find((i) => (i as { display?: string }).display === "article")!;
  return Array.from({ length: 40 }, (_, n) => ({
    ...base,
    id: `${view}${String(n).padStart(3, "0")}`,
    story_id: `${view}${String(n).padStart(3, "0")}`,
    title: `Invented ${view} story ${n}`,
  }));
}

async function stubApi(page: Page): Promise<string[]> {
  const seen: string[] = [];
  await page.route(/\/api\/feed(\?|$)/, (route) => {
    const view = new URL(route.request().url()).searchParams.get("view") ?? "";
    seen.push(view);
    return route.fulfill({
      json: { ...example, view, items: items(view), next_cursor: "c1" },
      headers: { ETag: `"${view}"` },
    });
  });
  await page.route("**/api/articles/*", (route) =>
    route.fulfill({ contentType: "text/html", body: "<p>Invented paragraph.</p>" }),
  );
  return seen;
}

const cards = (page: Page) => page.getByTestId("feed-item");
const sections = (page: Page) => page.getByRole("navigation", { name: "Sections" });

test("the tab bar has News, Feed and Longer, each 44px tall, and a tap opens the view", async ({
  page,
}) => {
  const seen = await stubApi(page);
  await page.goto("/");
  await expect(sections(page).getByRole("link")).toHaveText(["News", "Feed", "Longer"]);
  for (const link of await sections(page).getByRole("link").all()) {
    expect((await link.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  }
  await sections(page).getByRole("link", { name: "News" }).click();
  await expect(page).toHaveURL(/\/news$/);
  await expect(page.getByRole("heading", { name: "News" })).toBeVisible();
  await expect(cards(page).first()).toContainText("Invented news story 0");
  expect(seen).toContain("news");
});

test("the Settings icon is round, 44px at least and named, opens Settings, and Liked is reached from there", async ({
  page,
}) => {
  await stubApi(page);
  await page.goto("/longer");
  await expect(cards(page).first()).toBeVisible();
  for (const name of ["Settings"]) {
    const icon = page.locator("header").getByRole("link", { name });
    const b = (await icon.boundingBox())!;
    expect(b.width).toBeGreaterThanOrEqual(44);
    expect(b.height).toBeGreaterThanOrEqual(44);
    const radius = await icon.evaluate((e) => parseFloat(getComputedStyle(e).borderTopLeftRadius));
    expect(radius).toBeGreaterThanOrEqual(b.width / 2 - 1);
    expect(await icon.locator("svg").count()).toBe(1);
  }
  await page.locator("header").getByRole("link", { name: "Settings" }).click();
  await expect(page).toHaveURL(/\/settings$/);
  // The bar stays, with no tab current.
  await expect(sections(page).locator('a[aria-current="page"]')).toHaveCount(0);
  await page.getByRole("link", { name: "Liked articles" }).click();
  await expect(page).toHaveURL(/\/liked$/);
  // #307: the header has no Liked heart.
  await page.goto("/longer");
  await expect(page.locator("header").getByRole("link", { name: "Liked" })).toHaveCount(0);
});

test("Longer returns to its scroll position after an article; Feed is unaffected", async ({
  page,
}) => {
  await stubApi(page);
  await page.goto("/longer");
  await expect(cards(page).first()).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 1200));
  await page.getByRole("link", { name: /Invented longer story 1[0-9]$/ }).first().click();
  await expect(page).toHaveURL(/\/article\//);
  await page.goBack();
  await expect(cards(page).first()).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(1000);
  await page.goto("/");
  await expect(cards(page).first()).toBeVisible();
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
});

test("offline, each visited tab shows its stored page and Load more is disabled", async ({
  browser,
}) => {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    serviceWorkers: "allow",
  });
  const page = await context.newPage();
  await stubApi(page);
  for (const path of ["/news", "/", "/longer"]) {
    await page.goto(path);
    await expect(cards(page).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Refresh" })).toBeEnabled();
  }
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
  });
  await expect
    .poll(() => page.evaluate(() => navigator.serviceWorker.controller !== null))
    .toBe(true);
  await context.setOffline(true);
  await page.unroute(/\/api\/feed(\?|$)/);
  for (const [path, view] of [
    ["/news", "news"],
    ["/", "feed"],
    ["/longer", "longer"],
  ] as const) {
    await page.goto(path);
    await expect(cards(page).first()).toContainText(`Invented ${view} story 0`);
    await expect(page.getByRole("button", { name: /load more/i })).toBeDisabled();
    await expect(page.getByText("More when you're back online")).toBeVisible();
  }
  await context.close();
});
