import { readFileSync } from "node:fs";
import { expect, test, type Locator, type Page } from "@playwright/test";

// #272: the bottom tab bar and the compact feed header, checked as positions, sizes and computed
// styles in the production build (which runs under the production CSP). Data is
// docs/feed.example.json; article HTML is invented. The same spec takes the owner-review
// screenshots into test-results/screenshots/.
test.use({
  serviceWorkers: "block",
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 2,
});

type Item = Record<string, unknown> & { id: string; title: string; display: string };
const feed = JSON.parse(
  readFileSync(new URL("../../docs/feed.example.json", import.meta.url), "utf-8"),
) as { items: Item[] } & Record<string, unknown>;
const article = feed.items.find((i) => i.display === "article")!;

/** Stubs `/api/**`. `hold` delays the feed response until `release()` is called. */
async function stubApi(page: Page, hold?: Promise<void>): Promise<void> {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/feed") {
      if (hold) await hold;
      return route.fulfill({ json: feed });
    }
    if (path.startsWith("/api/articles/")) {
      return route.fulfill({
        contentType: "text/html; charset=utf-8",
        body: "<p>Invented paragraph for the navigation test.</p>",
      });
    }
    return route.fulfill({ status: 204, body: "" });
  });
}

const sections = (page: Page) => page.getByRole("navigation", { name: "Sections" });
const refresh = (page: Page) => page.getByRole("button", { name: "Refresh" });
const shot = (page: Page, name: string) =>
  page.screenshot({ path: `test-results/screenshots/${name}.png`, fullPage: false });

function style(el: Locator, prop: string): Promise<string> {
  return el.evaluate((e, p) => getComputedStyle(e).getPropertyValue(p), prop);
}

async function box(el: Locator) {
  const b = await el.boundingBox();
  expect(b).not.toBeNull();
  return b!;
}

test("the Sections bar sits at the bottom of the viewport with Feed, Liked and Settings", async ({
  page,
}) => {
  await stubApi(page);
  await page.goto("/");
  await expect(page.getByTestId("feed-item").first()).toBeVisible();
  const bar = sections(page);
  await expect(bar).toBeVisible();
  const b = await box(bar);
  expect(Math.round(b.y + b.height)).toBe(844);
  await expect(bar.getByRole("link")).toHaveText(["Feed", "Liked", "Settings"]);
  expect(await style(bar, "position")).toBe("fixed");
  await shot(page, "feed-top-light");
});

test("each tab link is at least 44px tall and the three share the bar evenly", async ({ page }) => {
  await stubApi(page);
  await page.goto("/");
  const links = sections(page).getByRole("link");
  await expect(links).toHaveCount(3);
  const boxes = [await box(links.nth(0)), await box(links.nth(1)), await box(links.nth(2))];
  for (const b of boxes) expect(b.height).toBeGreaterThanOrEqual(44);
  // On a phone each link fills a third of the width.
  for (const b of boxes) expect(Math.abs(b.width - 390 / 3)).toBeLessThan(2);
});

test("on a wide screen the links stay within the 40rem content width", async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 800 });
  await stubApi(page);
  await page.goto("/");
  const links = sections(page).getByRole("link");
  const first = await box(links.first());
  const last = await box(links.last());
  expect(last.x + last.width - first.x).toBeLessThanOrEqual(640 + 1);
  expect(Math.abs(first.x - (1200 - (last.x + last.width - first.x)) / 2)).toBeLessThan(2);
});

test.describe("the current tab", () => {
  for (const [path, name] of [
    ["/", "Feed"],
    ["/liked", "Liked"],
    ["/liked/", "Liked"],
    ["/settings", "Settings"],
    ["/settings/", "Settings"],
  ] as const) {
    test(`${path} marks only ${name} with aria-current=page`, async ({ page }) => {
      await stubApi(page);
      await page.goto(path);
      const current = sections(page).locator('a[aria-current="page"]');
      await expect(current).toHaveCount(1);
      await expect(current).toHaveText(name);
    });
  }

  test("the current tab uses --accent and the others --muted", async ({ page }) => {
    await stubApi(page);
    await page.goto("/");
    const tokens = await page.evaluate(() => {
      const probe = document.createElement("span");
      document.body.appendChild(probe);
      const read = (name: string) => {
        probe.style.color = `var(${name})`;
        return getComputedStyle(probe).color;
      };
      const out = { accent: read("--accent"), muted: read("--muted") };
      probe.remove();
      return out;
    });
    const links = sections(page).getByRole("link");
    expect(await style(links.nth(0), "color")).toBe(tokens.accent);
    expect(await style(links.nth(1), "color")).toBe(tokens.muted);
    expect(await style(links.nth(2), "color")).toBe(tokens.muted);
  });

  test("tapping a tab goes to that screen", async ({ page }) => {
    await stubApi(page);
    await page.goto("/");
    await sections(page).getByRole("link", { name: "Liked" }).click();
    await expect(page).toHaveURL(/\/liked$/);
    await expect(sections(page).locator('a[aria-current="page"]')).toHaveText("Liked");
    await shot(page, "liked");
    await sections(page).getByRole("link", { name: "Settings" }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await shot(page, "settings");
  });
});

test("the reader has no Sections bar", async ({ page }) => {
  await stubApi(page);
  await page.goto(`/article/${article.id}`);
  await expect(page.getByText("Invented paragraph for the navigation test.")).toBeVisible();
  await expect(sections(page)).toHaveCount(0);
});

test("dark theme: the bar background, border and links come from the dark tokens", async ({
  page,
}) => {
  await page.addInitScript(() => localStorage.setItem("newsfeed-theme", "dark"));
  await stubApi(page);
  await page.goto("/");
  const bar = sections(page);
  await expect(bar).toBeVisible();
  const want = await page.evaluate(() => {
    const probe = document.createElement("span");
    document.body.appendChild(probe);
    const read = (prop: "color" | "backgroundColor", name: string) => {
      probe.style[prop] = `var(${name})`;
      return getComputedStyle(probe)[prop];
    };
    const out = {
      bg: read("backgroundColor", "--bg"),
      divider: read("color", "--divider"),
      accent: read("color", "--accent"),
    };
    probe.remove();
    return out;
  });
  expect(await style(bar, "background-color")).toBe(want.bg);
  expect(await style(bar, "border-top-color")).toBe(want.divider);
  expect(await style(bar.getByRole("link", { name: "Feed" }), "color")).toBe(want.accent);
  await shot(page, "feed-top-dark");
});

test("the last story and its menu button end above the tab bar", async ({ page }) => {
  await stubApi(page);
  await page.goto("/");
  const cards = page.getByTestId("feed-item");
  await expect(cards.first()).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  const last = cards.last();
  const bar = await box(sections(page));
  const c = await box(last);
  expect(c.y + c.height).toBeLessThanOrEqual(bar.y);
  const menu = await box(last.locator("summary, button[aria-haspopup], button[aria-expanded]").first());
  expect(menu.y + menu.height).toBeLessThanOrEqual(bar.y);
  await shot(page, "feed-bottom");
});

test("the Settings screen's last control is not hidden under the bar", async ({ page }) => {
  await stubApi(page);
  await page.goto("/settings");
  await expect(sections(page)).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  const bar = await box(sections(page));
  const buttons = page.getByRole("main").getByRole("button");
  const n = await buttons.count();
  expect(n).toBeGreaterThan(0);
  const b = await box(buttons.nth(n - 1));
  expect(b.y + b.height).toBeLessThanOrEqual(bar.y);
});

test.describe("the compact feed header", () => {
  test("has no Refresh text button; one 44px round icon button sits right of the heading", async ({
    page,
  }) => {
    await stubApi(page);
    await page.goto("/");
    await expect(page.getByTestId("feed-item").first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Refresh", exact: true })).toHaveCount(1);
    await expect(page.locator("button", { hasText: /^\s*Refresh\s*$/ })).toHaveCount(0);

    const row = page.locator(".top");
    const heading = page.getByRole("heading", { name: "Newsfeed" });
    const button = refresh(page);
    await expect(row.getByRole("button", { name: "Refresh" })).toHaveCount(1);
    const h = await box(heading);
    const b = await box(button);
    expect(b.width).toBeGreaterThanOrEqual(44);
    expect(b.height).toBeGreaterThanOrEqual(44);
    expect(b.x).toBeGreaterThanOrEqual(h.x + h.width);
    expect(await button.locator("svg").count()).toBe(1);
    // Round: the corner radius is at least half the side.
    const radius = parseFloat(await style(button, "border-top-left-radius"));
    expect(radius).toBeGreaterThanOrEqual(b.width / 2 - 1);
  });

  test("shows Updated ... in small text below the heading, in the same row", async ({ page }) => {
    await stubApi(page);
    await page.goto("/");
    const updated = page.locator(".top").getByText(/^Updated /);
    await expect(updated).toBeVisible();
    const h = await box(page.getByRole("heading", { name: "Newsfeed" }));
    const u = await box(updated);
    expect(u.y).toBeGreaterThanOrEqual(h.y + h.height - 1);
    const hSize = parseFloat(await style(page.getByRole("heading", { name: "Newsfeed" }), "font-size"));
    expect(parseFloat(await style(updated, "font-size"))).toBeLessThan(hSize);
  });

  test("the row stays stuck to the top when scrolled", async ({
    page,
  }) => {
    await stubApi(page);
    await page.goto("/");
    await expect(page.getByTestId("feed-item").first()).toBeVisible();
    const row = page.locator(".top");
    await page.evaluate(() => window.scrollTo(0, 400));
    expect(Math.round((await box(row)).y)).toBe(0);
  });

  test("while refreshing the button is disabled and spins; afterwards it is enabled again", async ({
    page,
  }) => {
    let release!: () => void;
    const hold = new Promise<void>((r) => (release = r));
    // The first load goes through at once; the tap on Refresh is held.
    let first = true;
    await page.route("**/api/feed", async (route) => {
      if (first) {
        first = false;
        return route.fulfill({ json: feed });
      }
      await hold;
      return route.fulfill({ json: feed });
    });
    await page.route("**/api/articles/**", (route) =>
      route.fulfill({ contentType: "text/html", body: "<p>Invented.</p>" }),
    );
    await page.goto("/");
    await expect(refresh(page)).toBeEnabled();
    await refresh(page).click();
    await expect(refresh(page)).toBeDisabled();
    const icon = refresh(page).locator("svg");
    expect(await style(icon, "animation-name")).not.toBe("none");
    await shot(page, "refreshing");
    release();
    await expect(refresh(page)).toBeEnabled();
    await expect(page.locator(".top").getByText(/^Updated /)).toBeVisible();
  });

  test("with prefers-reduced-motion the icon does not spin but the button is still disabled", async ({
    page,
  }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    let release!: () => void;
    const hold = new Promise<void>((r) => (release = r));
    let first = true;
    await page.route("**/api/feed", async (route) => {
      if (first) {
        first = false;
        return route.fulfill({ json: feed });
      }
      await hold;
      return route.fulfill({ json: feed });
    });
    await page.route("**/api/articles/**", (route) =>
      route.fulfill({ contentType: "text/html", body: "<p>Invented.</p>" }),
    );
    await page.goto("/");
    await expect(refresh(page)).toBeEnabled();
    await refresh(page).click();
    await expect(refresh(page)).toBeDisabled();
    expect(await style(refresh(page).locator("svg"), "animation-name")).toBe("none");
    release();
    await expect(refresh(page)).toBeEnabled();
  });
});

test("the visible footer is gone but #version carries the build, hidden", async ({ page }) => {
  await stubApi(page);
  await page.goto("/");
  await expect(page.getByTestId("feed-item").first()).toBeVisible();
  await expect(page.getByText("The app is running.")).toHaveCount(0);
  await expect(page.getByText(/^Build /)).toHaveCount(0);
  const version = page.locator("#version");
  await expect(version).toHaveCount(1);
  await expect(version).toBeHidden();
  expect(await version.getAttribute("data-version")).toBeTruthy();
});

test("text size XL: the bar and header still fit", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("newsfeed-font-size", "xl"));
  await stubApi(page);
  await page.goto("/");
  await expect(page.getByTestId("feed-item").first()).toBeVisible();
  const bar = await box(sections(page));
  expect(Math.round(bar.y + bar.height)).toBe(844);
  expect(bar.x).toBeGreaterThanOrEqual(0);
  expect(bar.x + bar.width).toBeLessThanOrEqual(390);
  const b = await box(refresh(page));
  expect(b.x + b.width).toBeLessThanOrEqual(390);
  await shot(page, "feed-xl");
});
