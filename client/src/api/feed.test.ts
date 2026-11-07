import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchFeed, SUPPORTED_SCHEMA_VERSION, UnsupportedFeedError } from "./feed";

const example = JSON.parse(
  readFileSync(new URL("../../../docs/feed.example.json", import.meta.url), "utf-8"),
) as Record<string, unknown>;

function stubFetch(impl: (...args: unknown[]) => unknown) {
  const spy = vi.fn(impl);
  vi.stubGlobal("fetch", spy);
  return spy;
}

function jsonResponse(body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

beforeEach(() => {
  vi.stubGlobal("location", { href: "/", replace: vi.fn() });
  vi.stubGlobal("navigator", { onLine: true });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("fetchFeed", () => {
  it("supports schema version 1", () => {
    expect(SUPPORTED_SCHEMA_VERSION).toBe(1);
  });

  it("loads the real docs/feed.example.json with its five items in order", async () => {
    stubFetch(() => jsonResponse(example, { ETag: '"abc"' }));
    const result = await fetchFeed();
    if (result === "not-modified") throw new Error("expected a feed");
    expect(result.etag).toBe('"abc"');
    expect(result.feed.items.map((i) => i.id)).toEqual(
      (example.items as { id: string }[]).map((i) => i.id),
    );
    expect(result.feed.items).toHaveLength(5);
  });

  it("requests /api/feed through apiFetch (with X-Requested-With)", async () => {
    const spy = stubFetch(() => jsonResponse(example));
    await fetchFeed();
    expect(spy.mock.calls[0]?.[0]).toBe("/api/feed");
    const init = spy.mock.calls[0]?.[1] as RequestInit;
    expect(new Headers(init.headers).get("X-Requested-With")).toBe("XMLHttpRequest");
  });

  it("sends If-None-Match when given an etag, and none otherwise", async () => {
    const spy = stubFetch(() => jsonResponse(example));
    await fetchFeed('"abc"');
    await fetchFeed();
    const first = new Headers((spy.mock.calls[0]?.[1] as RequestInit).headers);
    const second = new Headers((spy.mock.calls[1]?.[1] as RequestInit).headers);
    expect(first.get("If-None-Match")).toBe('"abc"');
    expect(second.get("If-None-Match")).toBeNull();
  });

  it("returns 'not-modified' on a 304", async () => {
    stubFetch(() => new Response(null, { status: 304 }));
    expect(await fetchFeed('"abc"')).toBe("not-modified");
  });

  it("accepts a feed with unknown top-level and item fields", async () => {
    const items = (example.items as Record<string, unknown>[]).map((i) => ({
      ...i,
      future_item_field: { nested: true },
    }));
    stubFetch(() => jsonResponse({ ...example, items, future_top_field: 7 }));
    const result = await fetchFeed();
    if (result === "not-modified") throw new Error("expected a feed");
    expect(result.feed.items).toHaveLength(5);
  });

  it("rejects schema_version 2 with UnsupportedFeedError", async () => {
    stubFetch(() => jsonResponse({ ...example, schema_version: 2 }));
    await expect(fetchFeed()).rejects.toBeInstanceOf(UnsupportedFeedError);
  });

  it("rejects when the request fails on the network", async () => {
    stubFetch(() => {
      throw new TypeError("Failed to fetch");
    });
    await expect(fetchFeed()).rejects.toBeInstanceOf(Error);
  });
});
