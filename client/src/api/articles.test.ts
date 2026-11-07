import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchArticle } from "./articles";

function stubFetch(impl: (...args: unknown[]) => unknown) {
  const spy = vi.fn(impl);
  vi.stubGlobal("fetch", spy);
  return spy;
}

beforeEach(() => {
  vi.stubGlobal("location", { href: "/", replace: vi.fn() });
  vi.stubGlobal("navigator", { onLine: true });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("fetchArticle", () => {
  it("returns the HTML body on a 200", async () => {
    stubFetch(
      () =>
        new Response("<p>Invented body.</p>", {
          status: 200,
          headers: { "Content-Type": "text/html; charset=utf-8" },
        }),
    );
    expect(await fetchArticle("2dd50c8ba0dfa7df")).toBe("<p>Invented body.</p>");
  });

  it("requests /api/articles/<id> through apiFetch (with X-Requested-With)", async () => {
    const spy = stubFetch(() => new Response("<p>x</p>", { status: 200 }));
    await fetchArticle("2dd50c8ba0dfa7df");
    expect(spy.mock.calls[0]?.[0]).toBe("/api/articles/2dd50c8ba0dfa7df");
    const init = spy.mock.calls[0]?.[1] as RequestInit;
    expect(new Headers(init.headers).get("X-Requested-With")).toBe("XMLHttpRequest");
  });

  it("returns null on a 404 (content expired)", async () => {
    stubFetch(() => new Response("", { status: 404 }));
    expect(await fetchArticle("2dd50c8ba0dfa7df")).toBeNull();
  });

  it("rejects on a network failure rather than returning null", async () => {
    stubFetch(() => {
      throw new TypeError("Failed to fetch");
    });
    await expect(fetchArticle("2dd50c8ba0dfa7df")).rejects.toBeInstanceOf(Error);
  });
});
