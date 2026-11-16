import { expect, test, type Page } from "@playwright/test";

// #332: the hidden /lab/preferences prototype, in the production build. It also takes the
// owner-review screenshots into test-results/screenshots/. The page uses invented data only.
test.use({ viewport: { width: 390, height: 844 }, serviceWorkers: "block" });

const shot = (page: Page, name: string, scheme: string) =>
  page.screenshot({ path: `test-results/screenshots/${name}-${scheme}.png`, fullPage: false });

for (const scheme of ["light", "dark"] as const) {
  test(`the lab layouts and first-run flow, ${scheme}`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme });
    await page.goto("/lab/preferences");
    await expect(page.getByRole("navigation", { name: "Sections" })).toHaveCount(0);

    await page.getByRole("radiogroup", { name: "Sport" }).getByRole("radio", { name: "Hide" }).click();
    await page.getByRole("radiogroup", { name: "BBC News" }).getByRole("radio", { name: "More" }).click();
    await shot(page, "variant-a", scheme);
    await page.getByRole("button", { name: "Show 18 more" }).click();
    await page.getByText("Long reads").scrollIntoViewIfNeeded();
    await shot(page, "variant-a-expanded", scheme);

    await page.goto("/lab/preferences?setup=1");
    await shot(page, "setup", scheme);
    await page.getByRole("button", { name: "Start reading" }).click();
    await expect(page.getByRole("status")).toBeVisible();
    await shot(page, "setup-started", scheme);
  });
}
