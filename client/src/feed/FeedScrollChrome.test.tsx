// @vitest-environment happy-dom
//
// #307: on the feed screens the header moves with the scroll (clamped to its own height) and a
// round "Back to top" button shows when well down the list and scrolling up. happy-dom computes
// no layout, so the page's scroll position, the viewport height and the header height are faked
// here; the tests read the header's inline `transform` (set through the CSSOM, which the CSP
// allows) and whether the button is present and not hidden (no `hidden`, `inert` or
// `aria-hidden="true"`; a fade-out must set one of them).
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installRouter, loadStores, locationAt, makeFeed, makeItem } from "../../test/test-env";

const ITEM = makeItem("9f8e7d6c5b4a3f2e", { title: "Invented feed story about ferries" });
const VIEWPORT = 800;
const HEADER = 60;

let root: HTMLElement;
let scrollTo: ReturnType<typeof vi.fn>;

function setScroll(y: number): void {
  for (const key of ["scrollY", "pageYOffset"]) {
    Object.defineProperty(window, key, { value: y, configurable: true, writable: true });
  }
  document.documentElement.scrollTop = y;
  window.dispatchEvent(new Event("scroll"));
}

beforeEach(() => {
  vi.stubGlobal("__APP_VERSION__", "test");
  for (const m of ["error", "warn", "log", "info"] as const) {
    vi.spyOn(console, m).mockImplementation(() => {});
  }
  Object.defineProperty(window, "innerHeight", { value: VIEWPORT, configurable: true });
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", { value: HEADER, configurable: true });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", { value: HEADER, configurable: true });
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    x: 0, y: 0, top: 0, left: 0, right: 360, bottom: HEADER, width: 360, height: HEADER,
    toJSON: () => ({}),
  });
  scrollTo = vi.fn();
  window.scrollTo = scrollTo as unknown as typeof window.scrollTo;
  setScroll(0);
  root = document.createElement("div");
  document.body.appendChild(root);
});

afterEach(() => {
  render(null, root);
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  setScroll(0);
});

async function show(path: string): Promise<void> {
  vi.stubGlobal("location", locationAt(path));
  installRouter({ feed: makeFeed([ITEM]) });
  await loadStores();
  const { App } = await import("../App");
  render(h(App, null), root);
}

async function showFeedScreen(path = "/"): Promise<void> {
  await show(path);
  await vi.waitFor(() => expect(root.textContent).toContain(ITEM.title), { timeout: 2000 });
}

const header = () => root.querySelector<HTMLElement>("header.top")!;
const button = () => root.querySelector<HTMLElement>('[aria-label="Back to top"]');

/** How far up the header has moved, in px (0 when it has no transform). */
function headerOffset(): number {
  const t = header().style.transform;
  if (!t) return 0;
  const m = /(-?\d+(?:\.\d+)?)px/.exec(t);
  return m ? -Number(m[1]) : 0;
}

function buttonShown(): boolean {
  const b = button();
  return (
    b !== null &&
    !b.hasAttribute("hidden") &&
    !b.hasAttribute("inert") &&
    b.getAttribute("aria-hidden") !== "true" &&
    b.closest("[hidden], [inert], [aria-hidden='true']") === null
  );
}

/** Scroll and let the hook's frame-throttled update land. */
async function scrollBy(y: number): Promise<void> {
  setScroll(y);
  await new Promise((r) => setTimeout(r, 40));
}

describe.each([
  ["/news", "News"],
  ["/", "Feed"],
  ["/longer", "Longer"],
])("scroll-linked header at %s", (path, title) => {
  it("is fully shown at the top", async () => {
    await showFeedScreen(path);
    expect(header().querySelector("h1")!.textContent).toBe(title);
    expect(headerOffset()).toBe(0);
  });

  it("moves up by the scroll distance, then is fully out of view past its height", async () => {
    await showFeedScreen(path);
    await scrollBy(30);
    expect(headerOffset()).toBe(30);
    await scrollBy(HEADER + 200);
    expect(headerOffset()).toBe(HEADER);
  });

  it("comes back at the same rate scrolling up, and fully at the top", async () => {
    await showFeedScreen(path);
    await scrollBy(500);
    expect(headerOffset()).toBe(HEADER);
    await scrollBy(470);
    expect(headerOffset()).toBe(HEADER - 30);
    await scrollBy(400);
    expect(headerOffset()).toBe(0);
    await scrollBy(300);
    expect(headerOffset()).toBe(0);
    await scrollBy(0);
    expect(headerOffset()).toBe(0);
  });
});

describe("header while scrolling in small steps", () => {
  it("follows the delta, not the position: down 10 then 10 hides 20; up 5 shows 5 back", async () => {
    await showFeedScreen();
    await scrollBy(100);
    expect(headerOffset()).toBe(HEADER);
    await scrollBy(110);
    await scrollBy(120);
    expect(headerOffset()).toBe(HEADER);
    await scrollBy(115);
    expect(headerOffset()).toBe(HEADER - 5);
  });
});

describe("Back to top button", () => {
  it("is a 44px-class round control with an inline SVG arrow, labelled Back to top", async () => {
    await showFeedScreen();
    await scrollBy(3 * VIEWPORT);
    await scrollBy(3 * VIEWPORT - 50);
    const b = button()!;
    expect(b).not.toBeNull();
    expect(b.tagName).toBe("BUTTON");
    expect(b.querySelector("svg")).not.toBeNull();
    expect(b.textContent?.trim()).toBe("");
    expect(b.hasAttribute("style")).toBe(false);
  });

  it("is not shown at the top of the list", async () => {
    await showFeedScreen();
    expect(buttonShown()).toBe(false);
  });

  it("is not shown three screens down while scrolling down", async () => {
    await showFeedScreen();
    await scrollBy(2.5 * VIEWPORT);
    await scrollBy(3 * VIEWPORT);
    expect(buttonShown()).toBe(false);
  });

  it("shows three screens down once the owner scrolls up, and hides again on scrolling down", async () => {
    await showFeedScreen();
    await scrollBy(3 * VIEWPORT);
    await scrollBy(3 * VIEWPORT - 100);
    expect(buttonShown()).toBe(true);
    await scrollBy(3 * VIEWPORT + 100);
    expect(buttonShown()).toBe(false);
  });

  it("hides when scrolling up gets within one screen of the top", async () => {
    await showFeedScreen();
    await scrollBy(3 * VIEWPORT);
    await scrollBy(3 * VIEWPORT - 100);
    expect(buttonShown()).toBe(true);
    await scrollBy(VIEWPORT - 100);
    expect(buttonShown()).toBe(false);
  });

  it("does not show up to two screens down, even scrolling up", async () => {
    await showFeedScreen();
    await scrollBy(1.9 * VIEWPORT);
    await scrollBy(1.8 * VIEWPORT);
    expect(buttonShown()).toBe(false);
  });

  it("scrolls smoothly to the top when tapped, and hides once there", async () => {
    await showFeedScreen();
    await scrollBy(3 * VIEWPORT);
    await scrollBy(3 * VIEWPORT - 100);
    button()!.click();
    expect(scrollTo).toHaveBeenCalledTimes(1);
    expect(scrollTo.mock.calls[0]![0]).toMatchObject({ top: 0, behavior: "smooth" });
    // The browser's smooth scroll ends at the top.
    await scrollBy(0);
    expect(buttonShown()).toBe(false);
    expect(headerOffset()).toBe(0);
  });

  it("scrolls without animation under prefers-reduced-motion", async () => {
    vi.stubGlobal(
      "matchMedia",
      (query: string) =>
        ({
          matches: /prefers-reduced-motion:\s*reduce/.test(query),
          media: query,
          addEventListener: () => {},
          removeEventListener: () => {},
          addListener: () => {},
          removeListener: () => {},
        }) as unknown as MediaQueryList,
    );
    await showFeedScreen();
    await scrollBy(3 * VIEWPORT);
    await scrollBy(3 * VIEWPORT - 100);
    button()!.click();
    expect(scrollTo).toHaveBeenCalledTimes(1);
    const arg = scrollTo.mock.calls[0]![0] as { top: number; behavior?: string };
    expect(arg.top).toBe(0);
    expect(arg.behavior).not.toBe("smooth");
  });
});

describe("screens without the scrolling chrome", () => {
  it.each(["/liked", "/settings"])("%s: no moving header and no Back to top button", async (path) => {
    await show(path);
    await new Promise((r) => setTimeout(r, 40));
    await scrollBy(3 * VIEWPORT);
    await scrollBy(3 * VIEWPORT - 100);
    for (const hd of root.querySelectorAll<HTMLElement>("header")) {
      expect(hd.style.transform).toBeFalsy();
    }
    expect(button()).toBeNull();
  });
});

describe("header links", () => {
  it.each(["/news", "/", "/longer"])("%s shows Settings and Refresh but no Liked heart", async (path) => {
    await showFeedScreen(path);
    expect(header().querySelector('a[aria-label="Settings"]')?.getAttribute("href")).toBe("/settings");
    expect(header().querySelector('[aria-label="Refresh"]')).not.toBeNull();
    expect(root.querySelector('a[aria-label="Liked"]')).toBeNull();
    expect(root.querySelector('a[href="/liked"]')).toBeNull();
  });
});
