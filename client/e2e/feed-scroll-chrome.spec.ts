import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

// #307: the feed header moves with the scroll, and a round Back to top button shows when well
// down the list and scrolling up. Production build and CSP. Data is docs/feed.example.json,
// repeated to make a long list. Also takes the owner-review screenshots (light and dark) into
// test-results/screenshots/.
test.use({
  serviceWorkers: "block",
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 2,
});

/** The body padding: the header rests 16px below the page top until it scrolls (then it sticks at 0). */
const BODY_PAD = 16;

type Item = Record<string, unknown> & { id: string };
const example = JSON.parse(
  readFileSync(new URL("../../docs/feed.example.json", import.meta.url), "utf-8"),
) as { items: Item[] } & Record<string, unknown>;
const long = {
  ...example,
  next_cursor: null,
  items: Array.from({ length: 8 }, (_, n) =>
    example.items.map((i) => ({ ...i, id: `${i.id}-${n}` })),
  ).flat(),
};

async function openLongFeed(page: Page): Promise<void> {
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    return path === "/api/feed"
      ? route.fulfill({ json: long })
      : route.fulfill({ status: 204, body: "" });
  });
  await page.goto("/");
  await expect(page.getByTestId("feed-item").first()).toBeVisible();
}

const header = (page: Page) => page.locator("header.top");
// A CSS locator: getByRole skips aria-hidden elements, so toBeHidden() on it could never fail.
const toTop = (page: Page) => page.locator(".back-to-top");
const scrollY = (page: Page) => page.evaluate(() => window.scrollY);
const jump = (page: Page, y: number) =>
  page.evaluate((top) => window.scrollTo({ top, behavior: "instant" }), y);
const shot = (page: Page, name: string) =>
  page.screenshot({ path: `test-results/screenshots/${name}.png`, fullPage: false });

async function headerTop(page: Page): Promise<number> {
  return (await header(page).boundingBox())!.y;
}

test("the header slides away going down and back going up, and is whole at the top", async ({
  page,
}) => {
  await openLongFeed(page);
  expect(await headerTop(page)).toBe(BODY_PAD);
  await page.mouse.wheel(0, 1000);
  await expect.poll(() => headerTop(page)).toBeLessThan(0);
  const h = (await header(page).boundingBox())!.height;
  await expect.poll(() => headerTop(page)).toBe(-h);
  await page.mouse.wheel(0, -20);
  await expect.poll(() => headerTop(page)).toBeGreaterThan(-h);
  await jump(page, 0);
  await expect.poll(() => headerTop(page)).toBe(BODY_PAD);
});

test("the header moves exactly as far as the page is scrolled, 30px for 30px, mid-scroll", async ({
  page,
}) => {
  // Programmatic scrolls end at once, which would snap straight away; ignore `scrollend` here so
  // only the 120ms idle timeout is left, and sample before it.
  await page.addInitScript(() => {
    const add = window.addEventListener.bind(window);
    window.addEventListener = ((type: string, ...rest: unknown[]) =>
      type === "scrollend" ? undefined : (add as (...a: unknown[]) => void)(type, ...rest)) as typeof add;
  });
  await openLongFeed(page);
  // Sampled 60ms after the scroll, well before the idle snap.
  const tops = await page.evaluate(async () => {
    const top = () => document.querySelector("header.top")!.getBoundingClientRect().top;
    const frames = () => new Promise((r) => setTimeout(r, 60));
    window.scrollTo({ top: 30, behavior: "instant" });
    await frames();
    const down = top();
    window.scrollTo({ top: 20, behavior: "instant" });
    await frames();
    return [down, top()];
  });
  // It rests BODY_PAD below the top; 30px of scroll leaves it 30px higher.
  expect(tops).toEqual([BODY_PAD - 30, BODY_PAD - 20]);
  // With scrollend ignored, the 120ms idle timer alone finishes the slide: up, so fully shown.
  await expect.poll(() => headerTop(page)).toBe(0);
});

test("near the top, a snap after a short downward scroll leaves no blank band", async ({ page }) => {
  await openLongFeed(page);
  for (const y of [20, 50]) {
    await jump(page, 0);
    await expect.poll(() => headerTop(page)).toBe(BODY_PAD);
    await page.mouse.wheel(0, y);
    await expect.poll(() => scrollY(page)).toBe(y);
    // Poll until the snap has settled: the header bottom meets the first card's top.
    await expect
      .poll(async () => {
        const h = (await header(page).boundingBox())!;
        const card = (await page.getByTestId("feed-item").first().boundingBox())!;
        return Math.abs(h.y + h.height - card.y);
      })
      .toBeLessThanOrEqual(1);
  }
});

test("when scrolling stops the header snaps fully shown after up, fully hidden after down", async ({
  page,
}) => {
  await openLongFeed(page);
  await jump(page, 3000);
  const h = (await header(page).boundingBox())!.height;
  await page.mouse.wheel(0, 20);
  await expect.poll(() => headerTop(page)).toBe(-h);
  await page.mouse.wheel(0, -20);
  await expect.poll(() => headerTop(page)).toBe(0);
  await page.mouse.wheel(0, 20);
  await expect.poll(() => headerTop(page)).toBe(-h);
});

test("the Back to top button is a 44px circle above the tab bar, bottom right", async ({ page }) => {
  await openLongFeed(page);
  await jump(page, 3000);
  await page.mouse.wheel(0, -50);
  const b = toTop(page);
  await expect(b).toBeVisible();
  await expect(b).toHaveAttribute("aria-label", "Back to top");
  const box = (await b.boundingBox())!;
  expect(box.width).toBe(44);
  expect(box.height).toBe(44);
  const bar = (await page.getByRole("navigation", { name: "Sections" }).boundingBox())!;
  expect(box.y + box.height).toBeLessThanOrEqual(bar.y);
  expect(box.x + box.width).toBeGreaterThan(390 - 32);
  expect(await b.evaluate((e) => getComputedStyle(e).borderRadius)).toBe("50%");
});

test("the button is hidden near the top and while scrolling down", async ({ page }) => {
  await openLongFeed(page);
  await expect(toTop(page)).toBeHidden();
  await jump(page, 3000);
  await page.mouse.wheel(0, 50);
  await expect(toTop(page)).toBeHidden();
});

test("tapping the button scrolls to the top and it hides", async ({ page }) => {
  await openLongFeed(page);
  await jump(page, 3000);
  await page.mouse.wheel(0, -50);
  await toTop(page).click();
  await expect.poll(() => scrollY(page)).toBe(0);
  await expect(toTop(page)).toBeHidden();
  expect(await headerTop(page)).toBe(BODY_PAD);
});

for (const scheme of ["light", "dark"] as const) {
  test(`screenshots, ${scheme}`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme });
    await openLongFeed(page);
    await shot(page, `feed-top-${scheme}`);
    await jump(page, 3000);
    await page.mouse.wheel(0, 30);
    await page.mouse.wheel(0, -20);
    await expect(toTop(page)).toBeVisible();
    // At rest after the snap: fully shown, nothing part-hidden.
    await expect.poll(() => headerTop(page)).toBe(0);
    await page.waitForTimeout(300);
    await shot(page, `feed-scrolled-up-${scheme}`);
  });
}
