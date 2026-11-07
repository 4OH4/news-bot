import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

// #216: leaving the reader is a full page load, so its `read` event must survive the unload and
// reach the server from the next page. The feed is docs/feed.example.json; the HTML is invented.

type Item = Record<string, unknown> & { id: string; title: string; display: string };
type FeedBody = Record<string, unknown> & { items: Item[] };

const example = JSON.parse(
  readFileSync(new URL("../../docs/feed.example.json", import.meta.url), "utf-8"),
) as FeedBody;

const top = example.items.find((i) => i.display === "article")!;
const bodyText = (id: string) => `Invented paragraph for reading time ${id}.`;

test.use({ viewport: { width: 360, height: 740 } });

/** Stubs the API and returns the feedback bodies POSTed, in order. */
async function stubApi(page: Page): Promise<Record<string, unknown>[]> {
  const posted: Record<string, unknown>[] = [];
  await page.route("**/api/feed", (route) =>
    route.fulfill({ json: example, headers: { ETag: '"v1"' } }),
  );
  await page.route("**/api/articles/*", (route) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split("/").pop()!);
    return route.fulfill({
      status: 200,
      contentType: "text/html; charset=utf-8",
      body: `<p>${bodyText(id)}</p>`,
    });
  });
  await page.route("**/api/feedback", (route) => {
    posted.push(JSON.parse(route.request().postData() ?? "{}") as Record<string, unknown>);
    return route.fulfill({ status: 204, body: "" });
  });
  return posted;
}

/** Opens the top article from the feed and reads it for 7 seconds of page time. */
async function readTopArticle(page: Page): Promise<void> {
  await page.clock.install();
  await page.goto("/");
  await page.getByRole("link", { name: top.title }).first().click();
  await expect(page.getByText(bodyText(top.id))).toBeVisible();
  await expect(page.getByRole("button", { name: /like/i })).toBeVisible();
  await page.clock.fastForward(7_000);
}

function reads(posted: Record<string, unknown>[]): Record<string, unknown>[] {
  return posted.filter((e) => e.event === "read");
}

test("leaving the reader by the Back link sends its read event", async ({ page }) => {
  const posted = await stubApi(page);
  await readTopArticle(page);
  await page.getByRole("link", { name: "Back to the feed" }).click();
  await expect(page.locator("[data-testid='feed-item']").first()).toBeVisible();

  await expect.poll(() => reads(posted).length).toBe(1);
  const [read] = reads(posted);
  expect(read).toMatchObject({ article_id: top.id, event: "read" });
  expect(read!.value as number).toBeGreaterThanOrEqual(7);
  expect(read!.value as number).toBeLessThanOrEqual(9);
});

test("leaving the reader by going to another address sends its read event", async ({ page }) => {
  const posted = await stubApi(page);
  await readTopArticle(page);
  await page.goto("/liked");
  await expect(page.getByRole("heading", { name: "Liked" })).toBeVisible();

  await expect.poll(() => reads(posted).length).toBe(1);
  expect(reads(posted)[0]).toMatchObject({ article_id: top.id, event: "read" });
  // Nothing is left over to be sent a second time.
  expect(await page.evaluate(() => localStorage.getItem("newsfeed-pending-reads"))).toBeNull();
});
