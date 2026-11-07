// @vitest-environment happy-dom
//
// The reader and likes (#216 rework): a liked article that has left the feed opens offline with
// its title and a pressed Like button, and a like pressed while the text is still loading keeps
// that text once it arrives.
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  articleRecord,
  bodyFor,
  buttonsNamed,
  installRouter,
  loadStores,
  locationAt,
  makeFeed,
  makeItem,
} from "../../test/test-env";

const A = makeItem("ab12cd34ef56ab78", { title: "Invented headline about pier repairs" });
const OTHER = makeItem("9a8b7c6d5e4f3a2b");

let root: HTMLElement;

beforeEach(() => {
  vi.stubGlobal("location", locationAt(`/article/${A.id}`));
  for (const m of ["error", "warn", "log", "info"] as const) {
    vi.spyOn(console, m).mockImplementation(() => {});
  }
  root = document.createElement("div");
  document.body.appendChild(root);
});

afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function likeButton(): HTMLButtonElement | undefined {
  return buttonsNamed(root, /\blike/i).find((b) => !/theme/i.test(b.textContent ?? ""));
}

describe("ReaderScreen with a liked article", () => {
  it("opens a liked article that has left the feed offline, with its title and Like pressed", async () => {
    installRouter({ online: false });
    const s = await loadStores();
    await s.saveFeed(makeFeed([OTHER]), '"e2"');
    await s.putArticleHtml(A.id, bodyFor(A.id, "stored"));
    const liked = await import("../feedback/liked");
    await liked.setLiked(A, true);

    const { ReaderScreen } = await import("./ReaderScreen");
    render(h(ReaderScreen, null), root);

    await vi.waitFor(() => expect(root.querySelector("h1")?.textContent).toBe(A.title));
    await vi.waitFor(() => expect(likeButton()?.getAttribute("aria-pressed")).toBe("true"));
  });

  it("a like pressed while the text is loading keeps the text once it is stored", async () => {
    const router = installRouter({
      feed: makeFeed([A]),
      articles: { [A.id]: bodyFor(A.id, "network") },
    });
    // Hold the article request until the like has been saved.
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => {
      release = r;
    });
    const routed = globalThis.fetch;
    vi.stubGlobal("fetch", async (input: unknown, init?: RequestInit) => {
      if (String(input).includes("/api/articles/")) await gate;
      return routed(input as RequestInfo, init);
    });
    const s = await loadStores();
    await s.saveFeed(makeFeed([A]), '"e1"');
    const liked = await import("../feedback/liked");

    const { ReaderScreen } = await import("./ReaderScreen");
    render(h(ReaderScreen, null), root);
    await vi.waitFor(() => expect(likeButton()?.getAttribute("aria-pressed")).toBe("false"));
    likeButton()!.click();
    await vi.waitFor(async () => expect(await liked.isLiked(A)).toBe(true));
    expect(await articleRecord(A.id)).toBeUndefined();

    release();
    await vi.waitFor(() =>
      expect(root.textContent).toContain(`Invented network body text for ${A.id}.`),
    );
    await vi.waitFor(async () => expect((await articleRecord(A.id))?.keep).toBe(true));
    expect(router.calls.some((c) => c.url === `/api/articles/${A.id}`)).toBe(true);
  });
});
