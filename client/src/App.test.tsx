// @vitest-environment happy-dom
//
// The /liked and /settings routes and the links to them (#216, #217; header icons since #289).
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installRouter, loadStores, locationAt, makeFeed, makeItem } from "../test/test-env";

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

  // #289: Liked and Settings moved from the tab bar to header icons on the feed screens.
  it.each(["/settings"])(
    "the feed page links to %s from its header, not from the Sections bar",
    async (href) => {
      await showApp("/");
      await vi.waitFor(() => expect(root.textContent).toContain(IN_FEED.title), { timeout: 2000 });
      const link = root.querySelector(`a[href="${href}"]`);
      expect(link).not.toBeNull();
      expect(link?.closest("header.top")).not.toBeNull();
      expect(link?.closest("nav")).toBeNull();
    },
  );

  // #307: the heart left the header; Liked is reached from Settings.
  it("the feed page has no link to /liked; Settings links to it", async () => {
    await showApp("/");
    await vi.waitFor(() => expect(root.textContent).toContain(IN_FEED.title), { timeout: 2000 });
    expect(root.querySelector('a[href="/liked"]')).toBeNull();
    render(null, root);
    await showApp("/settings");
    await vi.waitFor(() => expect(root.querySelector('a[href="/liked"]')).not.toBeNull(), {
      timeout: 2000,
    });
    expect(root.querySelector('a[href="/liked"]')?.textContent).toMatch(/Liked articles/);
  });

  it("/settings shows the Settings screen, not the feed", async () => {
    await showApp("/settings");
    await vi.waitFor(
      () =>
        expect(
          [...root.querySelectorAll("button")].some((b) => /sign out/i.test(b.textContent ?? "")),
        ).toBe(true),
      { timeout: 2000 },
    );
    await new Promise((r) => setTimeout(r, 50));
    expect(root.textContent).not.toContain(IN_FEED.title);
  });
});

// #272: the tab bar replaces the top links, and the footer goes.
const bar = () => root.querySelector('nav[aria-label="Sections"]');
const current = () =>
  [...(bar()?.querySelectorAll("a") ?? [])]
    .filter((a) => a.getAttribute("aria-current") === "page")
    .map((a) => a.textContent?.trim());

describe("bottom tab bar (#272)", () => {
  it("is a <nav> named Sections with exactly News, Feed and Longer links (#289)", async () => {
    await showApp("/");
    await vi.waitFor(() => expect(bar()).not.toBeNull(), { timeout: 2000 });
    expect(bar()?.classList.contains("tab-bar")).toBe(true);
    const links = [...bar()!.querySelectorAll("a")].map((a) => [
      a.textContent?.trim(),
      a.getAttribute("href"),
    ]);
    expect(links).toEqual([
      ["News", "/news"],
      ["Feed", "/"],
      ["Longer", "/longer"],
    ]);
  });

  it("no app-level <header> remains", async () => {
    await showApp("/");
    await vi.waitFor(() => expect(bar()).not.toBeNull(), { timeout: 2000 });
    expect(root.querySelector("header.app-header")).toBeNull();
    expect(root.querySelector("nav a")?.closest("header")).toBeNull();
  });

  it.each([
    ["/", ["Feed"]],
    ["/liked", []],
    ["/liked/", []],
    ["/settings", []],
    ["/settings/", []],
  ])("at %s the current tab is %j (Liked and Settings are not tabs, #289)", async (path, names) => {
    await showApp(path);
    await vi.waitFor(() => expect(bar()).not.toBeNull(), { timeout: 2000 });
    expect(current()).toEqual(names);
  });

  it("is not shown in the reader", async () => {
    await showApp(`/article/${IN_FEED.id}`);
    await new Promise((r) => setTimeout(r, 100));
    expect(bar()).toBeNull();
    expect(root.querySelector(".tab-bar")).toBeNull();
  });
});

describe("footer removal and the #version hook (#272)", () => {
  it.each(["/", "/liked", "/settings"])("%s has no visible footer text", async (path) => {
    await showApp(path);
    await vi.waitFor(() => expect(bar()).not.toBeNull(), { timeout: 2000 });
    expect(root.querySelector("footer")).toBeNull();
    expect(root.querySelector("#status")).toBeNull();
    expect(root.textContent).not.toContain("The app is running.");
    expect(root.textContent).not.toMatch(/Build\s+test/);
  });

  it.each(["/", "/liked", "/settings"])(
    "%s keeps a hidden #version carrying the build's data-version",
    async (path) => {
      await showApp(path);
      await vi.waitFor(() => expect(bar()).not.toBeNull(), { timeout: 2000 });
      const v = root.querySelector("#version");
      expect(v).not.toBeNull();
      expect(v?.getAttribute("data-version")).toBe("test");
      expect(v?.hasAttribute("hidden")).toBe(true);
    },
  );
});
