// @vitest-environment happy-dom
//
// The /liked route and the app-level header link to it (#216).
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installRouter, loadStores, locationAt, makeFeed, makeItem } from "./reader/test-env";

const LIKED = makeItem("1a2b3c4d5e6f7a8b", { title: "Invented liked story about kites" });
const IN_FEED = makeItem("9f8e7d6c5b4a3f2e", { title: "Invented feed story about ferries" });

let root: HTMLElement;

beforeEach(() => {
  vi.stubGlobal("__APP_VERSION__", "test");
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

async function showApp(path: string): Promise<void> {
  vi.stubGlobal("location", locationAt(path));
  installRouter({ feed: makeFeed([IN_FEED]) });
  await loadStores();
  const liked = await import("./feedback/liked");
  await liked.setLiked(LIKED, true);
  const { App } = await import("./App");
  render(h(App, null), root);
}

describe("App routes", () => {
  it("/liked shows the Liked list, not the feed", async () => {
    await showApp("/liked");
    await vi.waitFor(
      () =>
        expect(
          [...root.querySelectorAll("a")].some(
            (a) => a.getAttribute("href") === `/article/${LIKED.id}`,
          ),
        ).toBe(true),
      { timeout: 2000 },
    );
    expect(root.textContent).toContain(LIKED.title);
    await new Promise((r) => setTimeout(r, 50));
    expect(root.textContent).not.toContain(IN_FEED.title);
  });

  it("the feed page has an app-level header with a link to /liked", async () => {
    await showApp("/");
    await vi.waitFor(() => expect(root.textContent).toContain(IN_FEED.title), { timeout: 2000 });
    const link = root.querySelector('a[href="/liked"]');
    expect(link).not.toBeNull();
    expect(link?.closest("header")).not.toBeNull();
    // App-level, not the feed screen's own header inside its <main>.
    expect(link?.closest("main")).toBeNull();
  });
});
