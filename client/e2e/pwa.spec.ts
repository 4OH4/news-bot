import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

/** Resolves once a service worker controls the page. */
async function controlled(page: Page): Promise<void> {
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
  });
  await expect
    .poll(() => page.evaluate(() => navigator.serviceWorker.controller !== null))
    .toBe(true);
}

test("manifest is installable and fetched with credentials", async ({ page }) => {
  await page.goto("/");
  const link = page.locator('link[rel="manifest"]');
  await expect(link).toHaveAttribute("crossorigin", "use-credentials");

  const href = await link.getAttribute("href");
  const res = await page.request.get(href ?? "");
  const manifest = await res.json();
  expect(manifest.display).toBe("standalone");
  expect(manifest.start_url).toBe("/");
  expect(manifest.scope).toBe("/");
  const sizes = manifest.icons.map((i: { sizes: string }) => i.sizes);
  expect(sizes).toContain("192x192");
  expect(sizes).toContain("512x512");
  expect(manifest.icons.some((i: { purpose?: string }) => i.purpose === "maskable")).toBe(true);
  expect((await page.request.get(manifest.start_url)).ok()).toBe(true);
});

test("the built page loads no script from another host", async () => {
  const html = readFileSync(new URL("../dist/index.html", import.meta.url), "utf8");
  const srcs = [...html.matchAll(/<script[^>]*\ssrc="([^"]+)"/g)].map((m) => m[1]);
  expect(srcs.length).toBeGreaterThan(0);
  for (const src of srcs) expect(src).not.toMatch(/^(?:https?:)?\/\//);
});

test("the shell shows offline after one visit", async ({ page, context }) => {
  await page.goto("/");
  await controlled(page);
  await context.setOffline(true);
  await page.reload();
  await expect(page.getByRole("navigation", { name: "Sections" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Newsfeed" })).toBeVisible();
});

test("an API request fails offline instead of getting index.html", async ({ page, context }) => {
  await page.route("**/api/feed", (route) => route.fulfill({ json: { items: [] } }));
  await page.goto("/");
  await controlled(page);
  expect(await page.evaluate(() => fetch("/api/feed").then((r) => r.status))).toBe(200);

  await page.unroute("**/api/feed");
  await context.setOffline(true);
  const outcome = await page.evaluate(() =>
    fetch("/api/feed").then(
      (r) => `status ${r.status}`,
      () => "network error",
    ),
  );
  expect(outcome).toBe("network error");
});

for (const path of ["/reconnect", "/?gcp-iap-mode=CLEAR_LOGIN_COOKIE", "/_gcp_iap/clear_login_cookie"]) {
  test(`navigating to ${path} goes to the network`, async ({ page }) => {
    await page.goto("/");
    await controlled(page);
    await page.route("**/*", (route) => {
      const url = new URL(route.request().url());
      if (route.request().isNavigationRequest() && url.pathname + url.search === path) {
        return route.fulfill({ contentType: "text/html", body: "<p id='net'>from network</p>" });
      }
      return route.continue();
    });
    await page.goto(path);
    await expect(page.locator("#net")).toHaveText("from network");
  });
}
