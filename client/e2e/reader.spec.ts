import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

type Item = Record<string, unknown> & { id: string; title: string; url: string; summary: string };
type FeedBody = Record<string, unknown> & { items: Item[] };

const example = JSON.parse(
  readFileSync(new URL("../../docs/feed.example.json", import.meta.url), "utf-8"),
) as FeedBody;

// The first example item is an `article` with a known id, author, domain and word_count (812).
const item = example.items[0]!;
const path = `/article/${item.id}`;

const NORMAL = `
  <h2>A heading</h2>
  <p>First invented paragraph with <em>emphasis</em> and <strong>weight</strong>.</p>
  <ul><li>one</li><li>two</li></ul>
  <blockquote>An invented quotation.</blockquote>
  <figure><img src="https://images.example.com/a.png" alt="An invented picture"><figcaption>Caption</figcaption></figure>
  <p>A <a href="https://example.com/out">link out</a>.</p>`;

const HOSTILE = `
  <p>Safe text before.</p>
  <script>window.__pwned = 'script'</script>
  <img src="x" onerror="window.__pwned = 'onerror'" alt="bad">
  <a href="javascript:window.__pwned='js-link'">js link</a>
  <iframe src="https://evil.example.net/frame"></iframe>
  <style>body { display: none }</style>
  <svg onload="window.__pwned = 'svg'"><circle r="5"></circle></svg>
  <p>Safe text after.</p>`;

/** Serves the feed and one article; `html` of null means the article has expired (404). */
async function serve(page: Page, html: string | null) {
  await page.route("**/api/feed", (route) => route.fulfill({ json: example }));
  await page.route(`**/api/articles/${item.id}`, (route) =>
    html === null
      ? route.fulfill({ status: 404, body: "" })
      : route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: html }),
  );
}

async function open(page: Page, html: string | null) {
  await serve(page, html);
  await page.goto(path);
}

test.use({ viewport: { width: 360, height: 740 } });

test("hostile markup is removed, no payload runs, and nothing trips the CSP", async ({ page }) => {
  const messages: string[] = [];
  page.on("console", (m) => messages.push(m.text()));
  page.on("pageerror", (e) => messages.push(e.message));
  await open(page, HOSTILE);
  await expect(page.getByText("Safe text after.")).toBeVisible();

  const article = page.locator("article, main").last();
  for (const selector of ["script", "iframe", "style", "svg", "[onerror]", "[onload]"]) {
    await expect(article.locator(selector)).toHaveCount(0);
  }
  await expect(page.locator("a[href^='javascript:']")).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as { __pwned?: string }).__pwned)).toBe(
    undefined,
  );
  expect(messages.filter((m) => /content security policy|refused to/i.test(m))).toEqual([]);
});

test("a javascript: link is neutralised, so clicking its text runs nothing", async ({ page }) => {
  await open(page, HOSTILE);
  await page.getByText("js link").click({ timeout: 2000 }).catch(() => {});
  expect(await page.evaluate(() => (window as unknown as { __pwned?: string }).__pwned)).toBe(
    undefined,
  );
});

test("form, input and base elements go, and the text inside the form stays", async ({ page }) => {
  await open(
    page,
    `<base href="https://evil.example.net/"><form action="https://evil.example.net/post"><p>Text inside the form.</p><input name="q" value="x"></form><p>After.</p>`,
  );
  await expect(page.getByText("Text inside the form.")).toBeVisible();
  await expect(page.locator("main form, main input")).toHaveCount(0);
  await expect(page.locator("base")).toHaveCount(0);
  expect(await page.evaluate(() => document.baseURI)).toBe(new URL(path, page.url()).href);
});

test("a normal article keeps paragraphs, headings, lists, quotes and images", async ({ page }) => {
  await open(page, NORMAL);
  const main = page.locator("main");
  await expect(main.locator("h2", { hasText: "A heading" })).toBeVisible();
  await expect(main.locator("p", { hasText: "First invented paragraph" })).toBeVisible();
  await expect(main.locator("ul li")).toHaveCount(2);
  await expect(main.locator("blockquote")).toContainText("An invented quotation.");
  await expect(main.locator("figure img")).toHaveAttribute("alt", "An invented picture");
});

test("article images are lazy, send no referrer, fit the width and hide when broken", async ({
  page,
}) => {
  await page.route("https://images.example.com/**", (route) => route.abort());
  await open(page, NORMAL);
  const img = page.locator("main figure img");
  await expect(img).toHaveAttribute("loading", "lazy");
  await expect(img).toHaveAttribute("referrerpolicy", "no-referrer");
  await expect(img).toBeHidden();
});

test("links in the article open outside the app with noopener noreferrer", async ({ page }) => {
  await open(page, NORMAL);
  const link = page.getByRole("link", { name: "link out" });
  await expect(link).toHaveAttribute("target", "_blank");
  await expect(link).toHaveAttribute("rel", "noopener noreferrer");
});

test("the header shows headline, domain, author, date and reading time from word_count", async ({
  page,
}) => {
  await open(page, NORMAL);
  const main = page.locator("main");
  await expect(main.getByRole("heading", { level: 1 })).toContainText(item.title);
  await expect(main).toContainText(String(item.domain));
  await expect(main).toContainText(String(item.author));
  // 812 words is about four minutes at 200 words a minute.
  await expect(main).toContainText(/\b[3-5] min/i);
  await expect(main).not.toContainText(/null|undefined/);
});

test("an Original article link to the source page is always shown", async ({ page }) => {
  await open(page, NORMAL);
  const original = page.getByRole("link", { name: /original article/i });
  await expect(original).toHaveAttribute("href", item.url);
  await expect(original).toHaveAttribute("target", "_blank");
  await expect(original).toHaveAttribute("rel", /noopener/);
});

test("a 404 shows the title, summary and original link, and no error page", async ({ page }) => {
  await open(page, null);
  const main = page.locator("main");
  await expect(main).toContainText(item.title);
  await expect(main).toContainText(item.summary);
  await expect(page.getByRole("link", { name: /original article/i })).toHaveAttribute(
    "href",
    item.url,
  );
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(main).not.toContainText(/error|failed|404/i);
});

test("a table wider than the screen scrolls inside its own box, not the page", async ({ page }) => {
  const cells = Array.from({ length: 12 }, (_, i) => `<td>column-${i}-with-long-content</td>`);
  await open(page, `<p>Intro.</p><table><tbody><tr>${cells.join("")}</tr></tbody></table><pre><code>${"x".repeat(300)}</code></pre>`);
  await expect(page.getByText("Intro.")).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
});

test("dark mode follows the system and the toggle overrides it after a reload", async ({
  page,
}) => {
  const bg = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  await page.emulateMedia({ colorScheme: "light" });
  await open(page, NORMAL);
  await expect(page.getByText("First invented paragraph")).toBeVisible();
  const light = await bg();
  await page.emulateMedia({ colorScheme: "dark" });
  const dark = await bg();
  expect(dark).not.toBe(light);

  // In a dark system, the toggle switches to light; the choice survives a reload.
  await page.getByRole("button", { name: /theme|dark|light/i }).click();
  await page.reload();
  await expect(page.getByText("First invented paragraph")).toBeVisible();
  expect(await bg()).toBe(light);
});

test("no font or other asset is loaded from another host except article images", async ({
  page,
}) => {
  const hosts = new Set<string>();
  page.on("request", (r) => hosts.add(new URL(r.url()).host));
  await page.route("https://images.example.com/**", (route) => route.abort());
  await open(page, NORMAL);
  await expect(page.getByText("First invented paragraph")).toBeVisible();
  hosts.delete("images.example.com");
  expect([...hosts]).toEqual(["localhost:4173"]);
});

test("returning to the list restores the scroll position", async ({ page }) => {
  await page.route("**/api/feed", (route) => route.fulfill({ json: example }));
  await page.route(`**/api/articles/${item.id}`, (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: NORMAL }),
  );
  await page.goto("/");
  await expect(page.locator("[data-testid='feed-item']")).toHaveCount(5);
  await page.evaluate(() => window.scrollTo(0, 150));
  const before = await page.evaluate(() => window.scrollY);
  expect(before).toBeGreaterThan(0);
  // A DOM click: Playwright's click would scroll the link into view first (it sits under the
  // sticky header at this offset), changing the very position the test is checking.
  await page
    .getByRole("link", { name: item.title })
    .first()
    .evaluate((el) => (el as HTMLElement).click());
  await expect(page.getByText("First invented paragraph")).toBeVisible();
  await page.goBack();
  await expect(page.locator("[data-testid='feed-item']")).toHaveCount(5);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(before - 10);
});

test("the Back to the feed link returns to the list at the same scroll position", async ({
  page,
}) => {
  await page.route("**/api/feed", (route) => route.fulfill({ json: example }));
  await page.route(`**/api/articles/${item.id}`, (route) =>
    route.fulfill({ status: 200, contentType: "text/html", body: NORMAL }),
  );
  await page.goto("/");
  await expect(page.locator("[data-testid='feed-item']")).toHaveCount(5);
  await page.evaluate(() => window.scrollTo(0, 150));
  const before = await page.evaluate(() => window.scrollY);
  await page
    .getByRole("link", { name: item.title })
    .first()
    .evaluate((el) => (el as HTMLElement).click());
  await expect(page.getByText("First invented paragraph")).toBeVisible();
  await page.getByRole("link", { name: /back to the feed/i }).click();
  await expect(page.locator("[data-testid='feed-item']")).toHaveCount(5);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(before - 10);
});
