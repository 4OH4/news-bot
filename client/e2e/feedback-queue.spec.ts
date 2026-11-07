import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

// #184. Nothing in the app creates events yet (#216, #217), so the tests write a queued event
// straight into the `queue` store of `newsfeed-feedback`, shaped as the payload plus its `seq`.
// The service worker is blocked so every request reaches the stubs below.
test.use({ serviceWorkers: "block" });

const feed = JSON.parse(
  readFileSync(new URL("../../docs/feed.example.json", import.meta.url), "utf-8"),
) as Record<string, unknown>;

/** An invented event, in the shape of docs/feedback.example.json. */
const EVENT = {
  id: "9e8d7c6b5a493827160f1e2d3c4b5a69",
  article_id: "7c6b5a4938271605",
  event: "open",
  value: null,
  created_at: "2026-11-07T09:15:00.000Z",
};

/** Waits until the app has created its feedback database at version 1. */
async function feedbackDbReady(page: Page): Promise<void> {
  await expect
    .poll(() =>
      page.evaluate(async () =>
        (await indexedDB.databases()).some(
          (d) => d.name === "newsfeed-feedback" && d.version === 1,
        ),
      ),
    )
    .toBe(true);
}

async function seedQueue(page: Page, rows: Record<string, unknown>[]): Promise<void> {
  await page.evaluate(async (entries) => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const req = indexedDB.open("newsfeed-feedback");
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction("queue", "readwrite");
      entries.forEach((row, i) => tx.objectStore("queue").put({ ...row, seq: i + 1 }));
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
    db.close();
  }, rows);
}

async function queuedIds(page: Page): Promise<string[]> {
  return page.evaluate(async () => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const req = indexedDB.open("newsfeed-feedback");
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    const rows = await new Promise<{ id: string }[]>((resolve, reject) => {
      const req = db.transaction("queue").objectStore("queue").getAll();
      req.onsuccess = () => resolve(req.result as { id: string }[]);
      req.onerror = () => reject(req.error);
    });
    db.close();
    return rows.map((r) => r.id);
  });
}

test("a queued event survives a reload and is sent, with the same id, once the network returns", async ({
  page,
}) => {
  await page.route("**/api/feed", (route) => route.fulfill({ json: feed }));
  let online = false;
  let attempts = 0;
  const posts: { body: unknown; headers: Record<string, string> }[] = [];
  await page.route("**/api/feedback", async (route) => {
    attempts++;
    if (!online) return route.abort("internetdisconnected");
    posts.push({ body: route.request().postDataJSON(), headers: await route.request().allHeaders() });
    return route.fulfill({ status: 204 });
  });

  await page.goto("/");
  await feedbackDbReady(page);
  await seedQueue(page, [EVENT]);

  await page.reload();
  await feedbackDbReady(page);
  // The flush on start tried and failed; the event is still queued.
  await expect.poll(() => attempts).toBeGreaterThan(0);
  expect(await queuedIds(page)).toEqual([EVENT.id]);

  online = true;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]!.body).toEqual(EVENT);
  expect(posts[0]!.headers["content-type"]).toMatch(/^application\/json/);
  expect(posts[0]!.headers["x-requested-with"]).toBe("XMLHttpRequest");
  await expect.poll(() => queuedIds(page)).toEqual([]);
});

test("on the not-allowed screen with a queued event, sign-out offers no discard, and send keeps everything", async ({
  page,
}) => {
  await page.route("**/api/feed", (route) => route.fulfill({ status: 403, body: "" }));
  const posted: string[] = [];
  await page.route("**/api/feedback", (route) => {
    posted.push(String((route.request().postDataJSON() as { id: string }).id));
    return route.fulfill({ status: 403, body: "" });
  });

  await page.goto("/");
  const signOut = page.getByRole("button", { name: /sign out/i });
  await expect(signOut).toBeVisible();
  await feedbackDbReady(page);
  await seedQueue(page, [EVENT]);

  let navigations = 0;
  const requested: string[] = [];
  page.on("request", (req) => {
    requested.push(req.url());
    if (req.isNavigationRequest()) navigations++;
  });

  await signOut.click();
  await expect(page.getByRole("button", { name: /cancel/i })).toBeVisible();
  const send = page.getByRole("button", { name: /send/i });
  await expect(send).toBeVisible();
  await expect(page.getByRole("button", { name: /discard/i })).toHaveCount(0);

  await send.click();
  await expect.poll(() => posted).toContain(EVENT.id);
  await expect(page.getByText(/n[o’']t (been )?sent/i).first()).toBeVisible();
  expect(navigations).toBe(0);
  expect(requested.filter((u) => u.includes("gcp-iap-mode"))).toEqual([]);
  expect(new URL(page.url()).pathname).toBe("/");
  expect(await queuedIds(page)).toEqual([EVENT.id]);
});
