import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeedHttpError, FeedNotReadyError, UnsupportedFeedError, fetchView } from "./feed";

// #289: the per-view page request. Errors match fetchFeed, plus 404 for a view whose file is not
// written yet (the hour after a deploy), which counts as "not ready" like 503.
const page = {
  schema_version: 1,
  generated_at: "2026-11-15T09:00:00Z",
  run_id: "run-invented-1",
  view: "news",
  next_cursor: "abc",
  items: [],
};

function stubFetch(respond: () => Response) {
  const spy = vi.fn<(input: unknown, init?: RequestInit) => Promise<Response>>(async () => respond());
  vi.stubGlobal("fetch", spy);
  return spy;
}

const json = (body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json", ...headers },
  });

beforeEach(() => {
  vi.stubGlobal("location", { href: "/", replace: vi.fn() });
  vi.stubGlobal("navigator", { onLine: true });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("fetchView", () => {
  it("requests the view with a default limit of 50 and no cursor", async () => {
    const spy = stubFetch(() => json(page));
    await fetchView("news", {});
    const url = new URL(String(spy.mock.calls[0]?.[0]), "http://localhost");
    expect(url.pathname).toBe("/api/feed");
    expect(url.searchParams.get("view")).toBe("news");
    expect(url.searchParams.get("limit")).toBe("50");
    expect(url.searchParams.has("cursor")).toBe(false);
  });

  it("sends the cursor, the limit and If-None-Match when given", async () => {
    const spy = stubFetch(() => json(page));
    await fetchView("longer", { cursor: "abc", limit: 10, etag: '"e1"' });
    const url = new URL(String(spy.mock.calls[0]?.[0]), "http://localhost");
    expect(url.searchParams.get("view")).toBe("longer");
    expect(url.searchParams.get("cursor")).toBe("abc");
    expect(url.searchParams.get("limit")).toBe("10");
    const init = spy.mock.calls[0]?.[1] as RequestInit;
    expect(new Headers(init.headers).get("If-None-Match")).toBe('"e1"');
  });

  it("returns the page, its etag and next_cursor", async () => {
    stubFetch(() => json(page, { ETag: '"p1"' }));
    const result = await fetchView("news", {});
    if (result === "not-modified") throw new Error("expected a page");
    expect(result.etag).toBe('"p1"');
    expect(result.feed.next_cursor).toBe("abc");
    expect(result.feed.view).toBe("news");
  });

  it("returns 'not-modified' on a 304", async () => {
    stubFetch(() => new Response(null, { status: 304 }));
    expect(await fetchView("feed", { etag: '"e1"' })).toBe("not-modified");
  });

  it.each([503, 404])("treats a %i as FeedNotReadyError", async (status) => {
    stubFetch(() => new Response(null, { status }));
    await expect(fetchView("news", {})).rejects.toBeInstanceOf(FeedNotReadyError);
  });

  it("raises FeedHttpError for another failure status", async () => {
    stubFetch(() => new Response(null, { status: 500 }));
    await expect(fetchView("news", {})).rejects.toBeInstanceOf(FeedHttpError);
  });

  it("rejects an unsupported schema version", async () => {
    stubFetch(() => json({ ...page, schema_version: 2 }));
    await expect(fetchView("news", {})).rejects.toBeInstanceOf(UnsupportedFeedError);
  });
});
