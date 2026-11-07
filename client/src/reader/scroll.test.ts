// @vitest-environment happy-dom
//
// #258: the list scroll position is kept per list, and the "opened from a list" memory names the
// list, is used once, and belongs to the reader's history entry rather than to the tab.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { locationAt } from "../../test/test-env";
import { listOrigin, restoreListScroll, saveListScroll } from "./scroll";

let scrollY = 0;
let historyLength = 2;
let historyState: unknown = null;

function at(path: string): void {
  vi.stubGlobal("location", locationAt(path));
}

/** Leaves the list at `path` for an article with the list scrolled to `y`. */
function leaveList(path: string, y: number): void {
  at(path);
  scrollY = y;
  saveListScroll();
}

/** Returns to the list at `path` the way the browser does for Back, and records scrollTo calls. */
function returnTo(path: string): ReturnType<typeof vi.fn> {
  at(path);
  scrollY = 0;
  vi.spyOn(performance, "getEntriesByType").mockReturnValue([
    { type: "back_forward" } as unknown as PerformanceEntry,
  ]);
  const scrollTo = vi.fn((_x: number, y: number) => {
    scrollY = y;
  });
  vi.stubGlobal("scrollTo", scrollTo);
  restoreListScroll();
  return scrollTo;
}

beforeEach(() => {
  sessionStorage.clear();
  scrollY = 0;
  historyLength = 2;
  historyState = null;
  Object.defineProperty(window, "scrollY", { configurable: true, get: () => scrollY });
  vi.stubGlobal("history", {
    get length() {
      return historyLength;
    },
    get state() {
      return historyState;
    },
    replaceState: (state: unknown) => {
      historyState = state;
    },
    back: vi.fn(),
  });
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => setTimeout(cb, 0));
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  sessionStorage.clear();
});

describe("list scroll, kept per list", () => {
  it("restores the Liked list to its own position, not the feed's", async () => {
    leaveList("/", 150);
    leaveList("/liked", 40);
    const scrollTo = returnTo("/liked");
    await vi.waitFor(() => expect(scrollTo).toHaveBeenCalledWith(0, 40));
    expect(scrollTo).not.toHaveBeenCalledWith(0, 150);
  });

  it("restores the feed to its own position after the Liked list was left later", async () => {
    leaveList("/", 150);
    leaveList("/liked", 40);
    const scrollTo = returnTo("/");
    await vi.waitFor(() => expect(scrollTo).toHaveBeenCalledWith(0, 150));
    expect(scrollTo).not.toHaveBeenCalledWith(0, 40);
  });

  it("leaves the Liked list at the top when it was left at 0, whatever the feed saved", async () => {
    leaveList("/", 150);
    leaveList("/liked", 0);
    const scrollTo = returnTo("/liked");
    await new Promise((r) => setTimeout(r, 50));
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it("treats /liked/ as the Liked list", async () => {
    leaveList("/liked/", 70);
    const scrollTo = returnTo("/liked");
    await vi.waitFor(() => expect(scrollTo).toHaveBeenCalledWith(0, 70));
  });
});

describe("listOrigin", () => {
  it("names the list the article was opened from", () => {
    leaveList("/liked", 10);
    at("/article/aa11bb22cc33dd44");
    expect(listOrigin()).toBe("/liked");
  });

  it("names the feed for an article opened from the feed", () => {
    leaveList("/", 10);
    at("/article/aa11bb22cc33dd44");
    expect(listOrigin()).toBe("/");
  });

  it("is null when no list was left", () => {
    at("/article/aa11bb22cc33dd44");
    expect(listOrigin()).toBeNull();
  });

  it("is null when the article is the only entry in history", () => {
    leaveList("/liked", 10);
    at("/article/aa11bb22cc33dd44");
    historyLength = 1;
    expect(listOrigin()).toBeNull();
  });

  it("still answers after a reload of the same history entry, with the tab's session cleared", () => {
    leaveList("/liked", 10);
    at("/article/aa11bb22cc33dd44");
    expect(listOrigin()).toBe("/liked");
    sessionStorage.clear();
    expect(listOrigin()).toBe("/liked");
    expect(listOrigin()).toBe("/liked");
  });

  it("is used once: a later entry opened directly in the same tab gets null", () => {
    leaveList("/liked", 10);
    at("/article/aa11bb22cc33dd44");
    expect(listOrigin()).toBe("/liked");

    // A direct open is a new history entry with no state, in the same tab and session.
    historyState = null;
    at("/article/ff00ee11dd22cc33");
    expect(listOrigin()).toBeNull();
  });
});
