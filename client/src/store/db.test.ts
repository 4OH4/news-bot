import "fake-indexeddb/auto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Feed, FeedItem } from "../feed/types";
import {
  clearAll,
  getArticleHtml,
  loadFeed,
  markKept,
  pruneArticles,
  putArticleHtml,
  saveFeed,
} from "./db";

const DAY = 24 * 60 * 60 * 1000;

function item(id: string, display: "article" | "link" = "article"): FeedItem {
  return {
    id,
    title: `Invented story ${id}`,
    url: `https://example.test/${id}`,
    source_id: "invented-source",
    domain: "Invented Source",
    author: null,
    published_at: null,
    summary: null,
    lead_image: null,
    score: 1,
    score_breakdown: {},
    extraction_status: "ok",
    display,
    word_count: null,
    content_path: null,
  };
}

function feed(ids: string[], runId = "run-1"): Feed {
  return {
    schema_version: 1,
    generated_at: "2026-11-01T00:00:00Z",
    run_id: runId,
    items: ids.map((id) => item(id)),
  };
}

beforeEach(async () => {
  await clearAll();
});

afterEach(() => {
  vi.useRealTimers();
});

/** Stores an article as if it had been saved `daysAgo` days ago. */
async function putAged(id: string, daysAgo: number, keep = false): Promise<void> {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(Date.now() - daysAgo * DAY);
  await putArticleHtml(id, `<p>invented body ${id}</p>`, { keep });
  vi.useRealTimers();
}

describe("feed record", () => {
  it("loadFeed is empty before anything is saved", async () => {
    expect(await loadFeed()).toBeNull();
  });

  it("saveFeed then loadFeed returns the feed, its etag and when it was fetched", async () => {
    const before = Date.now();
    await saveFeed(feed(["a", "b"]), '"v1"');
    const stored = await loadFeed();
    expect(stored?.feed.items.map((i) => i.id)).toEqual(["a", "b"]);
    expect(stored?.etag).toBe('"v1"');
    expect(stored?.fetchedAt).toBeGreaterThanOrEqual(before);
  });

  it("a second saveFeed replaces the one record", async () => {
    await saveFeed(feed(["a"], "run-1"), '"v1"');
    await saveFeed(feed(["b"], "run-2"), '"v2"');
    const stored = await loadFeed();
    expect(stored?.feed.run_id).toBe("run-2");
    expect(stored?.etag).toBe('"v2"');
  });
});

describe("article text", () => {
  it("returns nothing for an article that is not stored", async () => {
    expect((await getArticleHtml("missing")) ?? null).toBeNull();
  });

  it("round-trips article html", async () => {
    await putArticleHtml("a", "<p>invented body</p>");
    expect(await getArticleHtml("a")).toBe("<p>invented body</p>");
  });

  it("putArticleHtml overwrites the earlier text", async () => {
    await putArticleHtml("a", "<p>one</p>");
    await putArticleHtml("a", "<p>two</p>");
    expect(await getArticleHtml("a")).toBe("<p>two</p>");
  });
});

describe("pruneArticles", () => {
  it("removes an article that is out of the feed, not kept and 8 days old", async () => {
    await putAged("old", 8);
    await pruneArticles(feed(["other"]));
    expect((await getArticleHtml("old")) ?? null).toBeNull();
  });

  it("keeps an old article that is marked keep", async () => {
    await putAged("old", 8, true);
    await pruneArticles(feed(["other"]));
    expect(await getArticleHtml("old")).toContain("invented body old");
  });

  it("keeps an old article that was kept later with markKept, and drops it once unkept", async () => {
    await putAged("old", 8);
    await markKept("old", true);
    await pruneArticles(feed(["other"]));
    expect(await getArticleHtml("old")).not.toBeNull();
    await markKept("old", false);
    await pruneArticles(feed(["other"]));
    expect((await getArticleHtml("old")) ?? null).toBeNull();
  });

  it("keeps an old article that is still in the feed", async () => {
    await putAged("old", 8);
    await pruneArticles(feed(["old"]));
    expect(await getArticleHtml("old")).not.toBeNull();
  });

  it("keeps a recent article that is out of the feed", async () => {
    await putAged("recent", 6);
    await pruneArticles(feed(["other"]));
    expect(await getArticleHtml("recent")).not.toBeNull();
  });

  it("leaves the stored feed alone", async () => {
    await saveFeed(feed(["a"]), '"v1"');
    await pruneArticles(feed(["a"]));
    expect((await loadFeed())?.feed.items).toHaveLength(1);
  });
});

describe("clearAll", () => {
  it("leaves no feed and no article, kept ones included", async () => {
    await saveFeed(feed(["a"]), '"v1"');
    await putArticleHtml("a", "<p>x</p>");
    await putArticleHtml("b", "<p>y</p>", { keep: true });
    await clearAll();
    expect(await loadFeed()).toBeNull();
    expect((await getArticleHtml("a")) ?? null).toBeNull();
    expect((await getArticleHtml("b")) ?? null).toBeNull();
  });
});
