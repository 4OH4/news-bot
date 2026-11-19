import { expect, test, type Page } from "@playwright/test";

// #338: the preferences screen in the production build. Invented data only.
test.use({ viewport: { width: 390, height: 844 }, serviceWorkers: "block" });

const menu = (setup_done: boolean, version = 1) => ({
  version,
  setup_done,
  menu: {
    sections: [
      {
        id: "news",
        label: "News sources",
        sources: [
          { id: "bbc-news", label: "BBC News", state: "show", starter: true },
          { id: "invented-wire", label: "Invented Wire", state: "show", starter: true },
          { id: "n-three", label: "Northern Three", state: "show", starter: false },
        ],
      },
    ],
    topics: [{ id: "sport", label: "Sport", state: "show", starter: true }],
    multipliers: { hide: 0, fewer: 0.5, show: 1, more: 2 },
  },
});

const feed = {
  schema_version: 1,
  generated_at: "2026-11-07T09:00:00Z",
  run_id: "run-e2e-1",
  items: [],
};

/**
 * Serves the menu and records each PUT body as the browser sends it. Recording on the request
 * event rather than in the route: Chromium cancels an intercepted keepalive request once the
 * page that sent it has gone, sometimes before the route sees it, though the request was sent.
 */
async function serve(page: Page, setupDone: boolean): Promise<Record<string, unknown>[]> {
  const puts: Record<string, unknown>[] = [];
  page.context().on("request", (req) => {
    if (req.method() === "PUT" && new URL(req.url()).pathname === "/api/preferences") {
      puts.push(req.postDataJSON() as Record<string, unknown>);
    }
  });
  await page.route("**/api/feed?*", (route) => route.fulfill({ json: feed }));
  await page.route("**/api/preferences", async (route) => {
    const req = route.request();
    if (req.method() === "PUT") {
      const body = req.postDataJSON() as Record<string, unknown>;
      await route.fulfill({ json: menu(Boolean(body.setup_done) || setupDone, Number(body.version) + 1) });
    } else {
      await route.fulfill({ json: menu(setupDone) });
    }
  });
  return puts;
}

test("hiding a source from the screen saves it", async ({ page }) => {
  const puts = await serve(page, true);
  await page.goto("/settings/preferences");
  await page.getByRole("radiogroup", { name: "BBC News" }).getByRole("radio", { name: "Hide" }).click();
  await expect.poll(() => puts.length, { timeout: 3000 }).toBe(1);
  expect(puts[0]).toMatchObject({ version: 1, sources: { "bbc-news": "hide" } });
});

test("a fresh install opens setup once, and Start reading finishes it", async ({ page }) => {
  const puts = await serve(page, false);
  await page.goto("/");
  await expect(page).toHaveURL(/\/settings\/preferences\?setup=1/);
  await expect(page.getByText("Welcome")).toBeVisible();
  await page.getByRole("button", { name: "Start reading" }).click();
  await expect.poll(() => puts.length).toBe(1);
  expect(puts[0]).toMatchObject({ setup_done: true });
  await expect(page).toHaveURL(/\/$/);

  // The GET still says setup_done false, so only the once-per-session mark stops a second
  // redirect. Load the feed again and let it settle: no further GET, and still on the Feed.
  const gets = await countGets(page);
  await page.reload();
  await page.waitForLoadState("networkidle");
  await expect(page).toHaveURL(/\/$/);
  expect(gets.length).toBe(0);
});

/** Records each GET /api/preferences from now on. */
async function countGets(page: Page): Promise<string[]> {
  await page.waitForLoadState("networkidle");
  const gets: string[] = [];
  page.on("request", (req) => {
    if (req.method() === "GET" && new URL(req.url()).pathname === "/api/preferences") {
      gets.push(req.url());
    }
  });
  return gets;
}

test("once setup is done, a feed load in the same session sends no preferences request", async ({
  page,
}) => {
  await serve(page, true);
  await page.goto("/");
  const gets = await countGets(page);
  await page.goto("/news");
  await page.waitForLoadState("networkidle");
  expect(gets.length).toBe(0);
});

test("a change followed at once by a tab link is still sent", async ({ page }) => {
  const puts = await serve(page, true);
  await page.goto("/settings/preferences");
  const hide = page.getByRole("radiogroup", { name: "BBC News" }).getByRole("radio", { name: "Hide" });
  await expect(hide).toBeVisible();

  // Tap and follow a full page link in the same step, well inside the save debounce, so only
  // the pagehide flush can send the change.
  await hide.evaluate((el) => {
    (el as HTMLElement).click();
    document.querySelector<HTMLAnchorElement>('nav.tab-bar a[href="/news"]')!.click();
  });

  await expect(page).toHaveURL(/\/news$/);
  await expect.poll(() => puts.length, { timeout: 3000 }).toBe(1);
  expect(puts[0]).toMatchObject({ version: 1, sources: { "bbc-news": "hide" } });
});
