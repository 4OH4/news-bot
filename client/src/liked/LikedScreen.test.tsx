// @vitest-environment happy-dom
//
// The Liked list (#216) at /liked: liked items from the phone's store, even after they have left
// the feed, each opening the reader at /article/<id>.
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  bodyFor,
  installRouter,
  loadStores,
  locationAt,
  makeFeed,
  makeItem,
} from "../reader/test-env";

const A = makeItem("a9a9a9a9a9a9a9a9", { title: "Invented story about lighthouse keepers" });
const B = makeItem("b8b8b8b8b8b8b8b8", { title: "Invented story about allotment rules" });
const C = makeItem("c7c7c7c7c7c7c7c7", { title: "Invented story about tram timetables" });
const DAY_MS = 24 * 60 * 60 * 1000;

let root: HTMLElement;

beforeEach(() => {
  vi.stubGlobal("location", locationAt("/liked"));
  for (const m of ["error", "warn", "log", "info"] as const) {
    vi.spyOn(console, m).mockImplementation(() => {});
  }
  root = document.createElement("div");
  document.body.appendChild(root);
});

afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function linkTo(href: string): HTMLAnchorElement | undefined {
  return [...root.querySelectorAll("a")].find((a) => a.getAttribute("href") === href);
}

describe("LikedScreen", () => {
  it("lists liked items, most recent first, each linking to its reader page", async () => {
    installRouter({ online: false });
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-03T09:00:00Z"));
    await loadStores();
    const liked = await import("../feedback/liked");
    await liked.setLiked(A, true);
    vi.setSystemTime(new Date("2026-11-03T09:10:00Z"));
    await liked.setLiked(B, true);

    const { LikedScreen } = await import("./LikedScreen");
    render(h(LikedScreen, null), root);

    await vi.waitFor(() => expect(linkTo(`/article/${B.id}`)).toBeDefined());
    expect(linkTo(`/article/${A.id}`)?.textContent).toContain(A.title);
    expect(linkTo(`/article/${B.id}`)?.textContent).toContain(B.title);
    const text = root.textContent ?? "";
    expect(text.indexOf(B.title)).toBeLessThan(text.indexOf(A.title));
    expect(text).not.toContain(C.title);
  });

  it("a liked article that has left the feed is still listed offline, and its text opens", async () => {
    const router = installRouter({ online: true });
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-01T08:00:00Z"));
    const s = await loadStores();
    await s.saveFeed(makeFeed([A, C]), '"e1"');
    await s.putArticleHtml(A.id, bodyFor(A.id, "stored"));
    const liked = await import("../feedback/liked");
    await liked.setLiked(A, true);

    // A week and a day later the feed has moved on and the old text has been pruned.
    vi.setSystemTime(new Date(Date.parse("2026-11-01T08:00:00Z") + 8 * DAY_MS));
    const later = makeFeed([C], "run-invented-2");
    await s.saveFeed(later, '"e2"');
    await s.pruneArticles(later);
    router.online = false;

    const { LikedScreen } = await import("./LikedScreen");
    render(h(LikedScreen, null), root);
    await vi.waitFor(() => expect(linkTo(`/article/${A.id}`)).toBeDefined());
    expect(linkTo(`/article/${A.id}`)?.textContent).toContain(A.title);

    render(null, root);
    vi.stubGlobal("location", locationAt(`/article/${A.id}`));
    const { ReaderScreen } = await import("../reader/ReaderScreen");
    render(h(ReaderScreen, null), root);
    await vi.waitFor(
      () => expect(root.textContent).toContain(`Invented stored body text for ${A.id}.`),
      { timeout: 2000 },
    );
  });

  it("with nothing liked, lists no articles", async () => {
    installRouter({ online: false });
    await loadStores();
    const { LikedScreen } = await import("./LikedScreen");
    render(h(LikedScreen, null), root);
    await new Promise((r) => setTimeout(r, 50));
    expect(root.querySelectorAll('a[href^="/article/"]')).toHaveLength(0);
  });
});
