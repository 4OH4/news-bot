// @vitest-environment happy-dom
//
// The /liked and /settings routes and the app-level header links to them (#216, #217).
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

  it("the feed page has a bottom Sections bar with a link to /liked, outside the screen's <main>", async () => {
    await showApp("/");
    await vi.waitFor(() => expect(root.textContent).toContain(IN_FEED.title), { timeout: 2000 });
    const link = root.querySelector('a[href="/liked"]');
    expect(link).not.toBeNull();
    expect(link?.closest("nav")?.getAttribute("aria-label")).toBe("Sections");
    expect(link?.closest("header")).toBeNull();
    expect(link?.closest("nav")?.classList.contains("tab-bar")).toBe(true);
    expect(link?.closest("main")).toBeNull();
  });

  it.each(["/", "/liked"])("the Sections bar on %s links to /settings, outside the screen's <main>", async (path) => {
    await showApp(path);
    await vi.waitFor(() => expect(root.querySelector('a[href="/settings"]')).not.toBeNull(), {
      timeout: 2000,
    });
    const link = root.querySelector('a[href="/settings"]');
    expect(link?.closest("nav")?.classList.contains("tab-bar")).toBe(true);
    expect(link?.closest("header")).toBeNull();
    expect(link?.closest("main")).toBeNull();
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
  it("is a <nav> named Sections with exactly Feed, Liked and Settings links", async () => {
    await showApp("/");
    await vi.waitFor(() => expect(bar()).not.toBeNull(), { timeout: 2000 });
    expect(bar()?.classList.contains("tab-bar")).toBe(true);
    const links = [...bar()!.querySelectorAll("a")].map((a) => [
      a.textContent?.trim(),
      a.getAttribute("href"),
    ]);
    expect(links).toEqual([
      ["Feed", "/"],
      ["Liked", "/liked"],
      ["Settings", "/settings"],
    ]);
  });

  it("no app-level <header> remains", async () => {
    await showApp("/");
    await vi.waitFor(() => expect(bar()).not.toBeNull(), { timeout: 2000 });
    expect(root.querySelector("header.app-header")).toBeNull();
    expect(root.querySelector("nav a")?.closest("header")).toBeNull();
  });

  it.each([
    ["/", "Feed"],
    ["/liked", "Liked"],
    ["/liked/", "Liked"],
    ["/settings", "Settings"],
    ["/settings/", "Settings"],
  ])("at %s only %s has aria-current=page", async (path, name) => {
    await showApp(path);
    await vi.waitFor(() => expect(bar()).not.toBeNull(), { timeout: 2000 });
    expect(current()).toEqual([name]);
  });

  it("is not shown in the reader", async () => {
    await showApp(`/article/${IN_FEED.id}`);
    await new Promise((r) => setTimeout(r, 100));
    expect(bar()).toBeNull();
    expect(root.querySelector("nav a")).toBeNull();
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
