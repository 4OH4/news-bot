import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

// #216: articles the feed prefetched in the background open with no network, in real Chromium
// under the production service worker. The feed is docs/feed.example.json; the HTML is invented.

type Item = Record<string, unknown> & { id: string; title: string; display: string };
type FeedBody = Record<string, unknown> & { items: Item[] };

const example = JSON.parse(
  readFileSync(new URL("../../docs/feed.example.json", import.meta.url), "utf-8"),
) as FeedBody;

const top = example.items.find((i) => i.display === "article")!;
const articleIds = example.items.filter((i) => i.display === "article").map((i) => i.id);
const bodyText = (id: string) => `Invented prefetched paragraph for ${id}.`;

const cards = (page: Page) => page.locator("[data-testid='feed-item']");

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

/** Ids in the `articles` store of the app's `newsfeed` IndexedDB. */
async function storedArticleIds(page: Page): Promise<string[]> {
  return page.evaluate(async () => {
    const dbs = await indexedDB.databases();
    if (!dbs.some((d) => d.name === "newsfeed")) return [];
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const req = indexedDB.open("newsfeed");
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    try {
      if (!db.objectStoreNames.contains("articles")) return [];
      return await new Promise<string[]>((resolve, reject) => {
        const req = db.transaction("articles").objectStore("articles").getAllKeys();
        req.onsuccess = () => resolve(req.result.map(String));
        req.onerror = () => reject(req.error);
      });
    } finally {
      db.close();
    }
  });
}

test("a prefetched top article opens offline under the production service worker", async ({
  page,
  context,
}) => {
  const requested: string[] = [];
  await page.route("**/api/feed", (route) =>
    route.fulfill({ json: example, headers: { ETag: '"v1"' } }),
  );
  await page.route("**/api/articles/*", (route) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split("/").pop()!);
    requested.push(id);
    return route.fulfill({
      status: 200,
      contentType: "text/html; charset=utf-8",
      body: `<h2>An invented heading</h2><p>${bodyText(id)}</p>`,
    });
  });
  await page.route("**/api/feedback", (route) => route.fulfill({ status: 204, body: "" }));

  // First run: the feed shows, the worker takes control, and the background prefetch stores the
  // article items' text without the reader ever being opened.
  await page.goto("/");
  await expect(cards(page)).toHaveCount(example.items.length);
  await controlled(page);
  await expect.poll(async () => (await storedArticleIds(page)).sort()).toEqual(
    [...articleIds].sort(),
  );
  expect(requested).toContain(top.id);

  // No network: drop the stubs too, since a fulfilled route answers even while offline.
  await context.setOffline(true);
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.goto(`/article/${top.id}`);

  await expect(page.getByText(bodyText(top.id))).toBeVisible();
  await expect(page.getByText(/not available offline/i)).toHaveCount(0);
  await expect(page.getByText(/could not load/i)).toHaveCount(0);
});
