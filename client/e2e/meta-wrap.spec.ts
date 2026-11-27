import { readFileSync } from "node:fs";
import { expect, test, type Locator, type Page } from "@playwright/test";

// #412: details lines (reader and feed card) break only between items, except inside the author.
// jsdom does no layout, so the wrapping rules are measured here against the production build.

type Item = Record<string, unknown> & { id: string; title: string };
type FeedBody = Record<string, unknown> & { items: Item[] };

const example = JSON.parse(
  readFileSync(new URL("../../docs/feed.example.json", import.meta.url), "utf-8"),
) as FeedBody;
const first = example.items[0]!;
const AUTHOR = "A. Writer, B. Reporter and C. Longname-Contributor";
const HYPHEN_DOMAIN = "news-example.co.uk";
const LONG_DOMAIN = `${"x".repeat(116)}.test`;

function feedWith(over: Record<string, unknown>): FeedBody {
  return { ...example, items: [{ ...first, ...over }, ...example.items.slice(1)] };
}

async function stub(page: Page, feed: FeedBody): Promise<void> {
  await page.route("**/api/feed?*", (route) => route.fulfill({ json: feed }));
  await page.route(`**/api/articles/${first.id}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html; charset=utf-8",
      body: "<p>Invented paragraph for the meta test.</p>",
    }),
  );
}

async function openReader(page: Page, feed: FeedBody): Promise<Locator> {
  await stub(page, feed);
  await page.goto(`/article/${first.id}`);
  await expect(page.getByText("Invented paragraph for the meta test.")).toBeVisible();
  return page.locator(".reader-meta");
}

async function openCard(page: Page, feed: FeedBody): Promise<Locator> {
  await stub(page, feed);
  await page.goto("/");
  return page.locator("[data-testid='feed-item']").first().locator(".card-meta");
}

/**
 * Per-character layout of a details line: the character's top edge (which tells lines apart)
 * and whether it sits in a non-wrapping item.
 */
async function layout(meta: Locator) {
  return meta.evaluate((root) => {
    const chars: { ch: string; top: number; whole: boolean; item: number }[] = [];
    const items = [...root.querySelectorAll(".meta-item")];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const text = n as Text;
      const owner = text.parentElement!.closest(".meta-item");
      for (let i = 0; i < text.length; i++) {
        const r = document.createRange();
        r.setStart(text, i);
        r.setEnd(text, i + 1);
        const rect = r.getBoundingClientRect();
        if (rect.width === 0) continue;
        chars.push({
          ch: text.data[i]!,
          top: rect.top,
          whole: !!owner && !owner.classList.contains("meta-wrap"),
          item: owner ? items.indexOf(owner) : -1,
        });
      }
    }
    return chars;
  });
}

/** Every non-wrapping item sits on one line, and no line starts with the separator. */
async function expectBreaksOnlyBetweenItems(meta: Locator): Promise<void> {
  expect(await meta.locator(".meta-item").count()).toBeGreaterThan(1);
  const chars = await layout(meta);
  const lineOf = (c: { top: number }) => Math.round(c.top / 3);
  for (const item of new Set(chars.filter((c) => c.whole).map((c) => c.item))) {
    const lines = new Set(chars.filter((c) => c.item === item).map(lineOf));
    expect(lines.size, `item ${item} is split across lines`).toBe(1);
  }
  chars.forEach((c, i) => {
    if (c.ch !== "·") return;
    const prev = chars
      .slice(0, i)
      .reverse()
      .find((p) => p.ch.trim() !== "");
    expect(prev, "line starts with the separator").toBeTruthy();
    expect(lineOf(prev!), "separator is first on its line").toBe(lineOf(c));
  });
}

const overflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);

const reading = { author: AUTHOR, domain: HYPHEN_DOMAIN, word_count: 1000 };

// A sweep of phone widths, so that some width puts a break inside "N min read" or a date today.
for (const width of [300, 320, 340, 360, 380]) {
  test(`reader details line breaks only between items at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 740 });
    await expectBreaksOnlyBetweenItems(await openReader(page, feedWith(reading)));
  });

  test(`card details line keeps the domain, time and sources whole at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 740 });
    const meta = await openCard(
      page,
      feedWith({
        domain: HYPHEN_DOMAIN,
        coverage: 3,
        published_at: new Date(Date.now() - 3 * 3600_000).toISOString(),
      }),
    );
    await expectBreaksOnlyBetweenItems(meta);
    await expect(meta.getByText("3 sources")).toBeVisible();
    await expect(meta.getByRole("link", { name: HYPHEN_DOMAIN })).toHaveAttribute(
      "target",
      "_blank",
    );
  });
}

test("a long author continues on the next line in the reader", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 740 });
  const meta = await openReader(page, feedWith(reading));
  const author = meta.locator(".meta-wrap");
  await expect(author).toHaveCount(1);
  const tops = await author.evaluate((e) => [...e.getClientRects()].map((r) => Math.round(r.top)));
  expect(new Set(tops).size).toBeGreaterThan(1);
});

test("a 120-character domain causes no horizontal scroll at 320px in the reader or on a card", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 740 });
  const feed = feedWith({ domain: LONG_DOMAIN, coverage: 2 });
  const meta = await openReader(page, feed);
  await expect(meta.locator(".meta-item").first()).toBeVisible();
  expect(await overflow(page)).toBeLessThanOrEqual(0);

  const card = await openCard(page, feed);
  await expect(card.locator(".meta-item").first()).toBeVisible();
  expect(await overflow(page)).toBeLessThanOrEqual(0);
});
