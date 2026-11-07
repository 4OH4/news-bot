// @vitest-environment happy-dom
//
// #258: the reader's Back link names the list the article was opened from, and that memory is
// used once, so a later direct open in the same tab loads the feed normally.
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  bodyFor,
  installRouter,
  loadStores,
  locationAt,
  makeFeed,
  makeItem,
} from "../../test/test-env";

const A = makeItem("aa11bb22cc33dd44", { title: "Invented headline about canal boats" });
const FEED = makeFeed([A, makeItem("ff00ee11dd22cc33")]);

let root: HTMLElement;
let historyState: unknown;
let back: ReturnType<typeof vi.fn>;

beforeEach(() => {
  sessionStorage.clear();
  historyState = null;
  back = vi.fn();
  vi.stubGlobal("history", {
    length: 3,
    get state() {
      return historyState;
    },
    replaceState: (state: unknown) => {
      historyState = state;
    },
    back,
  });
  for (const m of ["error", "warn", "log", "info"] as const) {
    vi.spyOn(console, m).mockImplementation(() => {});
  }
  installRouter({ feed: FEED, articles: { [A.id]: bodyFor(A.id, "network") } });
  root = document.createElement("div");
  document.body.appendChild(root);
});

afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
  sessionStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** Leaves the list at `path` for the article, as a tap on a card does, then shows the reader. */
async function openFrom(path: "/" | "/liked" | null): Promise<void> {
  await loadStores();
  if (path !== null) {
    vi.stubGlobal("location", locationAt(path));
    const { saveListScroll } = await import("./scroll");
    saveListScroll();
  }
  vi.stubGlobal("location", locationAt(`/article/${A.id}`));
  const { ReaderScreen } = await import("./ReaderScreen");
  render(h(ReaderScreen, null), root);
  await vi.waitFor(() => expect(backLink()).not.toBeNull());
}

function backLink(): HTMLAnchorElement | null {
  return [...root.querySelectorAll("a")].find((a) => /^back to/i.test(a.textContent ?? "")) ?? null;
}

function tap(link: HTMLAnchorElement): MouseEvent {
  const event = new MouseEvent("click", { bubbles: true, cancelable: true });
  link.dispatchEvent(event);
  return event;
}

describe("ReaderScreen Back link", () => {
  it("reads 'Back to Liked' and links to /liked when opened from the Liked list", async () => {
    await openFrom("/liked");
    expect(backLink()?.textContent?.trim()).toBe("Back to Liked");
    expect(backLink()?.getAttribute("href")).toBe("/liked");
  });

  it("goes back in history, not following the link, when opened from the Liked list", async () => {
    await openFrom("/liked");
    const event = tap(backLink()!);
    expect(back).toHaveBeenCalledTimes(1);
    expect(event.defaultPrevented).toBe(true);
  });

  it("reads 'Back to the feed' and goes back in history when opened from the feed", async () => {
    await openFrom("/");
    expect(backLink()?.textContent?.trim()).toBe("Back to the feed");
    expect(backLink()?.getAttribute("href")).toBe("/");
    tap(backLink()!);
    expect(back).toHaveBeenCalledTimes(1);
  });

  it("opened directly, says 'Back to the feed' and follows the link instead of going back", async () => {
    await openFrom(null);
    expect(backLink()?.textContent?.trim()).toBe("Back to the feed");
    const event = tap(backLink()!);
    expect(back).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });

  it("an article opened directly after one opened from Liked does not inherit the Liked origin", async () => {
    await openFrom("/liked");
    expect(backLink()?.textContent?.trim()).toBe("Back to Liked");
    render(null, root);

    // A new history entry in the same tab: no state, the same session storage.
    historyState = null;
    await openFrom(null);
    expect(backLink()?.textContent?.trim()).toBe("Back to the feed");
    tap(backLink()!);
    expect(back).not.toHaveBeenCalled();
  });

  it("still reads 'Back to Liked' when the same entry is shown again after a reload", async () => {
    await openFrom("/liked");
    render(null, root);
    sessionStorage.clear();
    await openFrom(null);
    expect(backLink()?.textContent?.trim()).toBe("Back to Liked");
  });
});
