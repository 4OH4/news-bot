import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

type Item = Record<string, unknown> & { id: string; title: string };
type FeedBody = Record<string, unknown> & { items: Item[] };

const example = JSON.parse(
  readFileSync(new URL("../../docs/feed.example.json", import.meta.url), "utf-8"),
) as FeedBody;

const cards = (page: Page) => page.locator("[data-testid='feed-item']");
const reconnect = (page: Page) => page.getByRole("button", { name: "Reconnect" });

test.use({ viewport: { width: 360, height: 740 } });

/** Resolves once a service worker controls the page. */
async function controlled(page: Page): Promise<void> {
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
  });
  await expect
    .poll(() => page.evaluate(() => navigator.serviceWorker.controller !== null))
    .toBe(true);
}

/** Record counts in the app's `newsfeed` IndexedDB, or null when it does not exist yet. */
async function stored(page: Page): Promise<{ feed: number; articles: number } | null> {
  return page.evaluate(async () => {
    const dbs = await indexedDB.databases();
    if (!dbs.some((d) => d.name === "newsfeed")) return null;
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const req = indexedDB.open("newsfeed");
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    const count = (name: string) =>
      new Promise<number>((resolve, reject) => {
        if (!db.objectStoreNames.contains(name)) return resolve(0);
        const req = db.transaction(name).objectStore(name).count();
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      });
    const result = { feed: await count("feed"), articles: await count("articles") };
    db.close();
    return result;
  });
}

/** Opens the app once with the example feed, and waits until it is stored and the worker is on. */
async function firstRun(page: Page, headers: Record<string, string> = { ETag: '"v1"' }) {
  await page.route("**/api/feed", (route) => route.fulfill({ json: example, headers }));
  await page.goto("/");
  await expect(cards(page)).toHaveCount(5);
  await expect.poll(async () => (await stored(page))?.feed).toBe(1);
  await controlled(page);
}

test("offline in real Chromium: reloading shows the stored feed with an offline notice", async ({
  page,
  context,
}) => {
  await firstRun(page);
  await context.setOffline(true);
  // A fulfilled route answers even while offline, so drop the stub: the request must fail.
  await page.unroute("**/api/feed");
  await page.reload();
  await expect(cards(page)).toHaveCount(5);
  await expect(page.getByText(/offline/i).first()).toBeVisible();
  await expect(reconnect(page)).toHaveCount(0);
});

test("a stored feed shows at once while a slow API is still answering, then is replaced", async ({
  page,
}) => {
  await firstRun(page);
  const replaced = {
    ...example,
    items: example.items.map((i, n) => (n === 0 ? { ...i, title: "Replaced headline" } : i)),
  };
  await page.unroute("**/api/feed");
  await page.route("**/api/feed", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 2500));
    await route.fulfill({ json: replaced, headers: { ETag: '"v2"' } });
  });
  await page.reload();
  await expect(cards(page)).toHaveCount(5, { timeout: 1500 });
  await expect(page.getByText(example.items[0]!.title).first()).toBeVisible();
  await expect(page.getByText("Replaced headline").first()).toBeVisible({ timeout: 10_000 });
  await expect(cards(page)).toHaveCount(5);
});

test("an unchanged feed is requested with If-None-Match and a 304 changes nothing", async ({
  page,
}) => {
  await firstRun(page);
  let conditional = "";
  await page.unroute("**/api/feed");
  await page.route("**/api/feed", (route) => {
    conditional = route.request().headers()["if-none-match"] ?? "";
    return route.fulfill({ status: 304, body: "" });
  });
  await page.reload();
  await expect(cards(page)).toHaveCount(5);
  await expect.poll(() => conditional).toBe('"v1"');
  await expect(cards(page)).toHaveCount(5);
  expect((await stored(page))?.feed).toBe(1);
});

test("a refresh with schema_version 2 keeps the stored feed and shows it", async ({ page }) => {
  await firstRun(page);
  await page.unroute("**/api/feed");
  await page.route("**/api/feed", (route) =>
    route.fulfill({ json: { ...example, schema_version: 2, items: [] }, headers: { ETag: '"v9"' } }),
  );
  await page.reload();
  await expect(cards(page)).toHaveCount(5);
  await expect(page.getByText(/update is needed|update needed/i)).toBeVisible();

  // The stored copy is still the good one: with the network gone, it is what shows.
  await page.context().setOffline(true);
  await page.reload();
  await expect(cards(page)).toHaveCount(5);
});

test("a 401 while online leaves the feed on screen under the Reconnect banner, store unchanged", async ({
  page,
}) => {
  await firstRun(page);
  await page.unroute("**/api/feed");
  await page.route("**/api/feed", (route) => route.fulfill({ status: 401, body: "" }));
  await page.reload();
  await expect(reconnect(page)).toBeVisible();
  await expect(cards(page)).toHaveCount(5);
  expect((await stored(page))?.feed).toBe(1);
});

test("a lapsed session with no network shows the feed and the offline notice, no Reconnect", async ({
  page,
  context,
}) => {
  await firstRun(page);
  await context.setOffline(true);
  // Offline, the request never reaches IAP, so a lapsed session cannot be told apart from the
  // first offline test above: the same steps cover this case. The 401-while-offline mapping
  // itself is covered by reportAuthRequired (auth/status.test.ts).
  await page.unroute("**/api/feed");
  await page.reload();
  await expect(cards(page)).toHaveCount(5);
  await expect(page.getByText(/offline/i).first()).toBeVisible();
  await expect(reconnect(page)).toHaveCount(0);
});

test("a 403 shows the not-allowed screen and keeps the stored feed and articles", async ({
  page,
}) => {
  await firstRun(page);
  await page.evaluate(async () => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const req = indexedDB.open("newsfeed");
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction("articles", "readwrite");
      tx.objectStore("articles").put({
        id: "kept-one",
        html: "<p>invented body</p>",
        storedAt: Date.now(),
        keep: true,
      });
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
    db.close();
  });
  await page.unroute("**/api/feed");
  await page.route("**/api/feed", (route) => route.fulfill({ status: 403, body: "" }));
  await page.reload();
  await expect(page.getByRole("button", { name: /sign out/i })).toBeVisible();
  expect(await stored(page)).toEqual({ feed: 1, articles: 1 });
});

test("a sign-out the user chooses leaves no feed or article in IndexedDB", async ({ page }) => {
  await firstRun(page);
  await page.unroute("**/api/feed");
  await page.route("**/api/feed", (route) => route.fulfill({ status: 403, body: "" }));
  await page.route("**/gcp-iap-mode=CLEAR_LOGIN_COOKIE*", (route) =>
    route.fulfill({ contentType: "text/html", body: "<p>signed out</p>" }),
  );
  await page.reload();
  // Sign-out ends with location.replace("/"); let that navigation finish before reading the store.
  const navigated = page.waitForEvent("framenavigated");
  await page.getByRole("button", { name: /sign out/i }).click();
  await navigated;
  await page.waitForLoadState("load");
  await expect.poll(async () => await stored(page)).toEqual({ feed: 0, articles: 0 });
});
