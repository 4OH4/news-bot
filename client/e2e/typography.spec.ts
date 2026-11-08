import { readFileSync } from "node:fs";
import { expect, test, type Locator, type Page } from "@playwright/test";

// #271: feed typography and the text-size setting, checked as computed styles in the production
// build (which runs under the production CSP). Data is docs/feed.example.json plus one invented
// long summary. The same spec takes the owner-review screenshots into test-results/screenshots/.
test.use({
  serviceWorkers: "block",
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 2,
});

type Item = Record<string, unknown> & {
  id: string;
  title: string;
  display: string;
  domain: string;
  summary: string | null;
};
const feed = JSON.parse(
  readFileSync(new URL("../../docs/feed.example.json", import.meta.url), "utf-8"),
) as { items: Item[] } & Record<string, unknown>;

const LONG_SUMMARY = Array.from(
  { length: 12 },
  (_, i) => `Invented sentence number ${i + 1} about harbour lighthouses and tidal charts.`,
).join(" ");
const long = { ...feed.items.find((i) => i.display === "article")!, summary: LONG_SUMMARY };
const body = { ...feed, items: [long, ...feed.items.filter((i) => i.id !== long.id)] };

async function stubApi(page: Page): Promise<void> {
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/feed") return route.fulfill({ json: body });
    if (path.startsWith("/api/articles/")) {
      return route.fulfill({
        contentType: "text/html; charset=utf-8",
        body: "<p>Invented paragraph for the typography test.</p>",
      });
    }
    return route.fulfill({ status: 204, body: "" });
  });
}

const cards = (page: Page) => page.getByTestId("feed-item");
const shot = (page: Page, name: string) =>
  page.screenshot({ path: `test-results/screenshots/${name}.png`, fullPage: false });

function style(el: Locator, prop: string): Promise<string> {
  return el.evaluate((e, p) => getComputedStyle(e).getPropertyValue(p), prop);
}

/** "#rrggbb" as the "rgb(r, g, b)" a computed colour reports. */
function rgb(hex: string): string {
  const n = (i: number) => parseInt(hex.slice(i, i + 2), 16);
  return `rgb(${n(1)}, ${n(3)}, ${n(5)})`;
}

async function token(page: Page, name: string): Promise<string> {
  const raw = await page.evaluate(
    (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim(),
    name,
  );
  return raw.startsWith("#") ? rgb(raw) : raw;
}

const rootSize = (page: Page) =>
  page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).fontSize));

test("the headline link is a dark serif with no underline, and the domain is not the accent", async ({
  page,
}) => {
  await stubApi(page);
  await page.goto("/");
  const first = cards(page).first();
  const link = first.locator(".card-title a");
  await expect(link).toBeVisible();

  expect(await style(link, "text-decoration-line")).toBe("none");
  expect(await style(link, "color")).toBe(await token(page, "--headline"));
  expect((await style(link, "font-family")).toLowerCase()).toMatch(/serif/);
  expect(await style(link, "font-weight")).toBe("700");

  const domain = first.locator(".card-meta a");
  expect(await style(domain, "color")).not.toBe(await token(page, "--accent"));
  expect(await style(domain, "text-decoration-line")).toBe("none");
  await expect(domain).toHaveAttribute("href", /^https?:/);
  await shot(page, "feed-light");
});

test("hovering the headline underlines it and keyboard focus outlines it in the accent", async ({
  page,
}) => {
  await stubApi(page);
  await page.goto("/");
  const link = cards(page).first().locator(".card-title a");
  await link.hover();
  expect(await style(link, "text-decoration-line")).toBe("underline");
  await page.mouse.move(0, 0);
  // A key press first, so the browser treats the focus as keyboard focus (:focus-visible).
  await page.keyboard.press("Shift");
  await link.focus();
  await expect(link).toBeFocused();
  expect(await style(link, "outline-width")).toBe("2px");
  expect(await style(link, "outline-color")).toBe(await token(page, "--accent"));
});

test("a long summary is limited to three lines but stays whole in the page", async ({ page }) => {
  await stubApi(page);
  await page.goto("/");
  const summary = cards(page).first().locator(".card-summary");
  expect(await style(summary, "-webkit-line-clamp")).toBe("3");
  // Recent Chromium reports a clamped -webkit-box as flow-root, so accept either; the line count below proves the clamp works.
  expect(["-webkit-box", "flow-root"]).toContain(await style(summary, "display"));
  expect(await style(summary, "color")).toBe(await token(page, "--text-secondary"));
  expect(await summary.textContent()).toBe(LONG_SUMMARY);
  const lines = await summary.evaluate((e) => {
    const lh = parseFloat(getComputedStyle(e).lineHeight);
    return Math.round(e.getBoundingClientRect().height / lh);
  });
  expect(lines).toBeLessThanOrEqual(3);
});

test("cards have no border box, and a hairline divider sits between stories", async ({ page }) => {
  await stubApi(page);
  await page.goto("/");
  await expect(cards(page)).not.toHaveCount(1);
  const first = cards(page).first();
  for (const side of ["top", "left", "right"]) {
    expect(await style(first, `border-${side}-width`), side).toBe("0px");
  }
  expect(await style(first, "border-bottom-width")).toBe("1px");
  expect(await style(first, "border-bottom-color")).toBe(await token(page, "--divider"));
  expect(await style(first, "border-radius")).toBe("0px");
  expect(await style(first, "background-color")).toBe("rgba(0, 0, 0, 0)");
  expect(await style(cards(page).last(), "border-bottom-width")).toBe("0px");
});

test("a seen headline is lighter than an unseen one", async ({ page }) => {
  await stubApi(page);
  await page.goto("/");
  const target = cards(page).first();
  const title = (await target.locator(".card-title a").textContent())!;
  await target.getByRole("link", { name: title }).click();
  await expect(page).toHaveURL(/\/article\//);
  await page.getByRole("link", { name: "Back to the feed" }).click();

  const seen = cards(page).first().locator(".card-title a");
  const unseen = cards(page).nth(1).locator(".card-title a");
  await expect(seen).toBeVisible();
  expect(await style(seen, "color")).toBe(await token(page, "--muted"));
  expect(await style(unseen, "color")).toBe(await token(page, "--headline"));
  await shot(page, "feed-seen");
});

test("the dark theme draws headline, summary and meta from the dark tokens", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("newsfeed-theme", "dark"));
  await stubApi(page);
  await page.goto("/");
  const first = cards(page).first();
  await expect(first).toBeVisible();
  expect(await token(page, "--headline")).toBe(rgb("#f0f2f4"));
  expect(await style(first.locator(".card-title a"), "color")).toBe(await token(page, "--headline"));
  expect(await style(first.locator(".card-summary"), "color")).toBe(
    await token(page, "--text-secondary"),
  );
  expect(await style(first.locator(".card-meta"), "color")).toBe(await token(page, "--muted"));
  expect(await token(page, "--text-secondary")).not.toBe(rgb("#4a5260"));
  await shot(page, "feed-dark");
  const article = body.items.find((i) => i.display === "article")!;
  await page.goto(`/article/${article.id}`);
  await expect(page.locator(".reader-title")).toBeVisible();
  await shot(page, "reader-dark");
  await page.goto("/settings");
  await expect(page.getByRole("radio", { name: /regular/i })).toBeChecked();
  await shot(page, "settings-dark");
});

test("the dark system setting gives the same dark headline without a stored theme", async ({
  page,
}) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await stubApi(page);
  await page.goto("/");
  await expect(cards(page).first()).toBeVisible();
  expect(await token(page, "--headline")).toBe(rgb("#f0f2f4"));
});

test("the reader title is a serif in the headline colour", async ({ page }) => {
  await stubApi(page);
  await page.goto("/");
  const article = body.items.find((i) => i.display === "article")!;
  await page.goto(`/article/${article.id}`);
  const title = page.locator(".reader-title");
  await expect(title).toBeVisible();
  expect((await style(title, "font-family")).toLowerCase()).toMatch(/serif/);
  expect(await style(title, "color")).toBe(await token(page, "--headline"));
  await shot(page, "reader-light");
});

test("with nothing stored the root has no data-font-size and Settings shows Regular", async ({
  page,
}) => {
  await stubApi(page);
  await page.goto("/settings");
  await expect(page.getByRole("radio", { name: /regular/i })).toBeChecked();
  expect(await page.evaluate(() => document.documentElement.hasAttribute("data-font-size"))).toBe(
    false,
  );
  const default_ = await rootSize(page);
  expect(default_).toBe(16);
  await shot(page, "settings-text-size");
});

test("choosing Large makes the root 112.5%, and it survives a reload", async ({ page }) => {
  await stubApi(page);
  await page.goto("/settings");
  await page.getByRole("radio", { name: /large/i }).check();
  expect(await page.evaluate(() => document.documentElement.dataset.fontSize)).toBe("large");
  expect(await rootSize(page)).toBe(18);

  await page.reload();
  expect(await page.evaluate(() => document.documentElement.dataset.fontSize)).toBe("large");
  expect(await rootSize(page)).toBe(18);
  await expect(page.getByRole("radio", { name: /large/i })).toBeChecked();
});

test("Small and XL scale the root, and the feed text follows", async ({ page }) => {
  await stubApi(page);
  await page.goto("/");
  const title = cards(page).first().locator(".card-title a");
  await expect(title).toBeVisible();
  const regular = parseFloat(await style(title, "font-size"));

  await page.evaluate(() => localStorage.setItem("newsfeed-font-size", "small"));
  await page.reload();
  expect(await rootSize(page)).toBe(14);
  expect(parseFloat(await style(cards(page).first().locator(".card-title a"), "font-size"))).toBeCloseTo(
    regular * 0.875,
    1,
  );
  await shot(page, "feed-small");

  await page.evaluate(() => localStorage.setItem("newsfeed-font-size", "xl"));
  await page.reload();
  expect(await rootSize(page)).toBe(20);
  expect(parseFloat(await style(cards(page).first().locator(".card-title a"), "font-size"))).toBeCloseTo(
    regular * 1.25,
    1,
  );
  await shot(page, "feed-xl");

  const article = body.items.find((i) => i.display === "article")!;
  await page.goto(`/article/${article.id}`);
  await expect(page.locator(".reader-title")).toBeVisible();
  await shot(page, "reader-xl");
  await page.goto("/settings");
  await expect(page.getByRole("radio", { name: /xl/i })).toBeChecked();
  await shot(page, "settings-xl");
});

test("choosing Regular after XL removes the attribute and clears the stored key", async ({
  page,
}) => {
  await stubApi(page);
  await page.goto("/settings");
  await page.getByRole("radio", { name: /xl/i }).check();
  await page.getByRole("radio", { name: /regular/i }).check();
  expect(await page.evaluate(() => document.documentElement.hasAttribute("data-font-size"))).toBe(
    false,
  );
  expect(await page.evaluate(() => localStorage.getItem("newsfeed-font-size"))).toBeNull();
  expect(await rootSize(page)).toBe(16);
});

test("a garbage stored size behaves as Regular", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("newsfeed-font-size", "huge"));
  await stubApi(page);
  await page.goto("/settings");
  await expect(page.getByRole("radio", { name: /regular/i })).toBeChecked();
  expect(await rootSize(page)).toBe(16);
});

test("with storage that throws, choosing a size still applies it", async ({ page }) => {
  await page.addInitScript(() => {
    const boom = () => {
      throw new Error("blocked");
    };
    Storage.prototype.setItem = boom;
    Storage.prototype.removeItem = boom;
  });
  await stubApi(page);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/settings");
  await page.getByRole("radio", { name: /large/i }).check();
  expect(await page.evaluate(() => document.documentElement.dataset.fontSize)).toBe("large");
  expect(await rootSize(page)).toBe(18);
  expect(errors.filter((m) => /blocked/.test(m))).toEqual([]);
});
