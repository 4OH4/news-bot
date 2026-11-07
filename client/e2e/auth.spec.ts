import { readdirSync, readFileSync } from "node:fs";
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

/**
 * Loads the app with `/api/feed` answering `status`. The app is assumed to call
 * `apiFetch("/api/feed")` once when it starts (the issue leaves the first caller open), so the
 * answer reaches the session-status store the way a real lapse would.
 */
async function loadWithFeedStatus(page: Page, status: number): Promise<void> {
  await page.route("**/api/feed", (route) => route.fulfill({ status, body: "" }));
  await page.goto("/");
  await controlled(page);
  await page.reload();
}

test("a 401 shows the Reconnect banner, and tapping it requests /reconnect from the network", async ({
  page,
}) => {
  await loadWithFeedStatus(page, 401);
  const banner = page.getByRole("button", { name: "Reconnect" });
  await expect(banner).toBeVisible();

  let reached = false;
  await page.route("**/reconnect", (route) => {
    reached = true;
    return route.fulfill({ contentType: "text/html", body: "<p id='net'>from network</p>" });
  });
  await banner.click();
  await expect(page.locator("#net")).toHaveText("from network");
  expect(reached).toBe(true);
  expect(new URL(page.url()).pathname).toBe("/reconnect");
});

test("the app loaded at /reconnect replaces the address with / without navigating", async ({
  page,
}) => {
  await page.route("**/reconnect", (route) => route.continue());
  let navigations = 0;
  page.on("framenavigated", () => navigations++);
  await page.goto("/reconnect");
  await expect.poll(() => new URL(page.url()).pathname).toBe("/");
  expect(navigations).toBe(1);
});

test("a 403 shows the not-allowed screen with a sign-out button", async ({ page }) => {
  await loadWithFeedStatus(page, 403);
  await expect(page.getByRole("button", { name: /sign out/i })).toBeVisible();
  await expect(page.getByRole("button", { name: "Reconnect" })).toHaveCount(0);
});

test("signing out requests the clear-cookie URL from the network, not the precache", async ({
  page,
}) => {
  await loadWithFeedStatus(page, 403);
  const seen: string[] = [];
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    seen.push(url.pathname + url.search);
    if (route.request().isNavigationRequest()) {
      return route.fulfill({ contentType: "text/html", body: "<p id='landed'>signed out</p>" });
    }
    return route.fulfill({ status: 200, body: "" });
  });
  await page.getByRole("button", { name: /sign out/i }).click();
  await expect(page.locator("#landed")).toBeVisible();
  expect(seen).toContain("/?gcp-iap-mode=CLEAR_LOGIN_COOKIE");
  expect(new URL(page.url()).search).toBe("");
});

test("the production build has no DO_SESSION_REFRESH and no cross-host script", async () => {
  const dist = new URL("../dist/", import.meta.url);
  const files = (dir: URL): URL[] =>
    readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
      e.isDirectory() ? files(new URL(`${e.name}/`, dir)) : [new URL(e.name, dir)],
    );
  const text = files(dist).filter((u) => /\.(js|html)$/.test(u.pathname));
  expect(text.length).toBeGreaterThan(0);
  const combined = text.map((u) => readFileSync(u, "utf8")).join("\n");
  expect(combined).not.toContain("DO_SESSION_REFRESH");
  expect(combined).toContain("/reconnect");
  expect(readFileSync(new URL("index.html", dist), "utf8")).not.toMatch(
    /<script[^>]*\ssrc="(?:https?:)?\/\//,
  );
});
