import "fake-indexeddb/auto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Feed, FeedItem } from "../feed/types";
import { clearAll, getArticleHtml, putArticleHtml } from "./db";
import { PREFETCH_LIMIT, prefetchArticles } from "./prefetch";

function item(id: string, display: "article" | "link"): FeedItem {
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

function articlesFeed(count: number): Feed {
  return {
    schema_version: 1,
    generated_at: "2026-11-01T00:00:00Z",
    run_id: "run-1",
    items: Array.from({ length: count }, (_, i) => item(`a${i + 1}`, "article")),
  };
}

const tick = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

beforeEach(async () => {
  await clearAll();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("prefetchArticles", () => {
  it("limits itself to 50 by default", () => {
    expect(PREFETCH_LIMIT).toBe(50);
  });

  it("stores the first 50 of 60 article items, fetched in feed order, 4 at a time", async () => {
    const requested: string[] = [];
    let inFlight = 0;
    let peak = 0;
    const fetchHtml = async (id: string) => {
      requested.push(id);
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      await tick();
      inFlight -= 1;
      return `<p>invented body ${id}</p>`;
    };

    await prefetchArticles(articlesFeed(60), fetchHtml);

    const first50 = Array.from({ length: 50 }, (_, i) => `a${i + 1}`);
    expect(requested).toEqual(first50);
    expect(peak).toBeGreaterThan(1);
    expect(peak).toBeLessThanOrEqual(4);
    for (const id of first50) expect(await getArticleHtml(id)).toBe(`<p>invented body ${id}</p>`);
    expect((await getArticleHtml("a51")) ?? null).toBeNull();
  });

  it("honours a smaller limit argument", async () => {
    const fetchHtml = vi.fn(async (id: string) => `<p>${id}</p>`);
    await prefetchArticles(articlesFeed(10), fetchHtml, 3);
    expect(fetchHtml.mock.calls.map((c) => c[0])).toEqual(["a1", "a2", "a3"]);
  });

  it("skips link items and counts only article items towards the limit", async () => {
    const mixed: Feed = {
      ...articlesFeed(0),
      items: [item("l1", "link"), item("a1", "article"), item("l2", "link"), item("a2", "article")],
    };
    const fetchHtml = vi.fn(async (id: string) => `<p>${id}</p>`);
    await prefetchArticles(mixed, fetchHtml, 2);
    expect(fetchHtml.mock.calls.map((c) => c[0])).toEqual(["a1", "a2"]);
  });

  it("does not fetch an article that is already stored", async () => {
    await putArticleHtml("a2", "<p>already here</p>");
    const fetchHtml = vi.fn(async (id: string) => `<p>${id}</p>`);
    await prefetchArticles(articlesFeed(3), fetchHtml);
    expect(fetchHtml.mock.calls.map((c) => c[0])).toEqual(["a1", "a3"]);
    expect(await getArticleHtml("a2")).toBe("<p>already here</p>");
  });

  it("stores nothing for an article the fetcher answers with null", async () => {
    const fetchHtml = async (id: string) => (id === "a2" ? null : `<p>${id}</p>`);
    await prefetchArticles(articlesFeed(3), fetchHtml);
    expect(await getArticleHtml("a1")).toBe("<p>a1</p>");
    expect((await getArticleHtml("a2")) ?? null).toBeNull();
    expect(await getArticleHtml("a3")).toBe("<p>a3</p>");
  });

  it("stops quietly when the fetcher fails on the third item, keeping the first two", async () => {
    let calls = 0;
    const fetchHtml = async (id: string) => {
      calls += 1;
      const mine = calls;
      await tick();
      // Compare this call's own number: `calls` has moved on by the time 4 requests resume.
      if (mine === 3) throw new Error("network down");
      return `<p>invented body ${id}</p>`;
    };

    await expect(prefetchArticles(articlesFeed(20), fetchHtml)).resolves.toBeUndefined();

    expect(await getArticleHtml("a1")).toBe("<p>invented body a1</p>");
    expect(await getArticleHtml("a2")).toBe("<p>invented body a2</p>");
    // It stopped: the whole feed was not fetched after the failure.
    expect(calls).toBeLessThan(20);
  });

  it("fetches nothing when Save-Data is on", async () => {
    vi.stubGlobal("navigator", { onLine: true, connection: { saveData: true } });
    const fetchHtml = vi.fn(async (id: string) => `<p>${id}</p>`);
    await prefetchArticles(articlesFeed(5), fetchHtml);
    expect(fetchHtml).not.toHaveBeenCalled();
    expect((await getArticleHtml("a1")) ?? null).toBeNull();
  });

  it("fetches normally when Save-Data is off", async () => {
    vi.stubGlobal("navigator", { onLine: true, connection: { saveData: false } });
    const fetchHtml = vi.fn(async (id: string) => `<p>${id}</p>`);
    await prefetchArticles(articlesFeed(2), fetchHtml);
    expect(fetchHtml).toHaveBeenCalledTimes(2);
  });
});
