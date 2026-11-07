// @vitest-environment happy-dom
//
// The feed's background prefetch (#216): once a new feed is saved, prefetchArticles runs over it
// with fetchArticle, so the top stories open offline.
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installRouter, loadStores, makeFeed, makeItem } from "../../test/test-env";

let root: HTMLElement;

beforeEach(() => {
  for (const m of ["error", "warn", "log", "info"] as const) {
    vi.spyOn(console, m).mockImplementation(() => {});
  }
  root = document.createElement("div");
  document.body.appendChild(root);
});

afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
  vi.doUnmock("../store/prefetch");
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("useFeed prefetch", () => {
  it("a new feed of 60 article items, once saved, is prefetched with fetchArticle", async () => {
    const feed = makeFeed(
      Array.from({ length: 60 }, (_, i) => makeItem(`a${String(i).padStart(15, "0")}`)),
    );
    installRouter({ feed });
    const s = await loadStores();
    const prefetchArticles = vi.fn(async () => {});
    vi.doMock("../store/prefetch", () => ({ PREFETCH_LIMIT: 50, prefetchArticles }));
    const { fetchArticle } = await import("../api/articles");
    const { useFeed } = await import("./useFeed");

    function Probe() {
      const { feed: shown } = useFeed();
      return h("p", null, shown ? `items:${shown.items.length}` : "none");
    }
    render(h(Probe, null), root);

    await vi.waitFor(() => expect(prefetchArticles).toHaveBeenCalled(), { timeout: 2000 });
    const call = prefetchArticles.mock.calls[0] as unknown as [unknown, unknown];
    expect(call[0]).toEqual(feed);
    expect(call[1]).toBe(fetchArticle);
    expect((await s.loadFeed())?.feed).toEqual(feed);
  });
});
