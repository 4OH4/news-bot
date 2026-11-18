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

/** Serves the menu and records each PUT body. */
async function serve(page: Page, setupDone: boolean): Promise<Record<string, unknown>[]> {
  const puts: Record<string, unknown>[] = [];
  await page.route("**/api/feed?*", (route) => route.fulfill({ json: feed }));
  await page.route("**/api/preferences", async (route) => {
    const req = route.request();
    if (req.method() === "PUT") {
      const body = req.postDataJSON() as Record<string, unknown>;
      puts.push(body);
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
});
