// @vitest-environment happy-dom
//
// #289: News, Feed and Longer as the three main tabs, each shown from its own paged view
// (`GET /api/feed?view=`, ADR-0012). Layout, sizes and scroll are checked in e2e/views.spec.ts.
import { h, render } from "preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { FeedItem } from "./feed/types";
import { buttonsNamed, loadStores, locationAt, makeItem } from "../test/test-env";

interface ViewPage {
  items: FeedItem[];
  next_cursor?: string | null;
  run_id?: string;
}

/** A fake API for the three views. Page i of a view is reached with the cursor `c<i>`. */
function installViews(pages: Record<string, ViewPage[]>) {
  const api = { online: true, requests: [] as URL[], etags: [] as (string | null)[], articles: [] as string[] };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown, init?: RequestInit) => {
      if (!api.online) throw new TypeError("Failed to fetch");
      const raw =
        typeof input === "string" ? input : input instanceof URL ? input.href : (input as Request).url;
      const url = new URL(raw, "http://localhost");
      if (url.pathname.startsWith("/api/articles/")) {
        api.articles.push(decodeURIComponent(url.pathname.slice("/api/articles/".length)));
        return new Response("<p>Invented body.</p>", { headers: { "Content-Type": "text/html" } });
      }
      if (url.pathname !== "/api/feed") return new Response(null, { status: 204 });
      api.requests.push(url);
      api.etags.push(new Headers(init?.headers).get("If-None-Match"));
      const view = url.searchParams.get("view") ?? "";
      const cursor = url.searchParams.get("cursor");
      const index = cursor ? Number(cursor.slice(1)) : 0;
      const p = pages[view]?.[index];
      if (!p) return new Response(null, { status: 404 });
      const body = {
        schema_version: 1,
        generated_at: "2026-11-15T09:00:00Z",
        run_id: p.run_id ?? "run-invented-1",
        view,
        items: p.items,
        next_cursor: p.next_cursor ?? null,
      };
      return new Response(JSON.stringify(body), {
        headers: { "Content-Type": "application/json", ETag: `"${view}-${index}"` },
      });
    }),
  );
  return api;
}

type Api = ReturnType<typeof installViews>;

const story = (id: string) => makeItem(id, { title: `Invented ${id} story` });
const stories = (prefix: string, n: number) =>
  Array.from({ length: n }, (_, i) => story(`${prefix}${String(i).padStart(3, "0")}`));

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

/** Opens the App at `path` (a fresh mount each time, as every screen is a page load). */
async function open(path: string): Promise<void> {
  render(null, root);
  vi.stubGlobal("location", locationAt(path));
  const { App } = await import("./App");
  render(h(App, null), root);
}

const waitText = (text: string) =>
  vi.waitFor(() => expect(root.textContent).toContain(text), { timeout: 2000 });
const h1 = () => root.querySelector("h1")?.textContent;
const refreshButton = () => root.querySelector<HTMLButtonElement>('button[aria-label="Refresh"]');
const loadMore = () => buttonsNamed(root, /load more/i)[0];
const idle = () => vi.waitFor(() => expect(refreshButton()?.disabled).toBe(false), { timeout: 2000 });
const viewParams = (api: Api) =>
  api.requests.map((u) => ({
    view: u.searchParams.get("view"),
    cursor: u.searchParams.get("cursor"),
    limit: u.searchParams.get("limit"),
  }));
const EMPTY = { news: [{ items: [] }], feed: [{ items: [] }], longer: [{ items: [] }] };

describe("the three tabs", () => {
  it.each([
    ["/news", "News"],
    ["/news/", "News"],
    ["/", "Feed"],
    ["/longer", "Longer"],
    ["/liked", null],
    ["/settings", null],
  ])("at %s the Sections bar lists News, Feed, Longer and marks %s", async (path, current) => {
    installViews(EMPTY);
    await loadStores();
    await open(path);
    const nav = await vi.waitFor(() => {
      const el = root.querySelector('nav[aria-label="Sections"]');
      expect(el).not.toBeNull();
      return el!;
    });
    const links = [...nav.querySelectorAll("a")];
    expect(links.map((a) => [a.textContent, a.getAttribute("href")])).toEqual([
      ["News", "/news"],
      ["Feed", "/"],
      ["Longer", "/longer"],
    ]);
    const marked = links.filter((a) => a.getAttribute("aria-current") === "page");
    expect(marked.map((a) => a.textContent)).toEqual(current ? [current] : []);
  });
});

describe("each view's data", () => {
  it.each([
    ["/news", "news", "News", "n1"],
    ["/", "feed", "Feed", "f1"],
    ["/longer", "longer", "Longer", "l1"],
  ])("%s requests view=%s with limit 50 and shows %s", async (path, view, title, id) => {
    const api = installViews({
      news: [{ items: [story("n1")] }],
      feed: [{ items: [story("f1")] }],
      longer: [{ items: [story("l1")] }],
    });
    await loadStores();
    await open(path);
    await waitText(`Invented ${id} story`);
    expect(h1()).toBe(title);
    expect(viewParams(api)).toEqual([{ view, cursor: null, limit: "50" }]);
  });

  it("a view with no items says 'Nothing here yet'", async () => {
    installViews({ longer: [{ items: [] }] });
    await loadStores();
    await open("/longer");
    await waitText("Nothing here yet");
  });

  it("a view not written yet (404) leaves the screen and tab bar working", async () => {
    installViews({});
    await loadStores();
    await open("/longer");
    await vi.waitFor(() => expect(root.querySelector('nav[aria-label="Sections"]')).not.toBeNull());
    expect(h1()).toBe("Longer");
    await idle();
  });
});

describe("Load more", () => {
  it("fetches the next page by cursor, appends it without repeating ids, and goes away at the end", async () => {
    const api = installViews({
      news: [
        { items: [story("n1"), story("n2")], next_cursor: "c1" },
        { items: [story("n2"), story("n3")], next_cursor: null },
      ],
    });
    await loadStores();
    await open("/news");
    await waitText("Invented n2 story");
    loadMore()!.click();
    await waitText("Invented n3 story");
    expect(viewParams(api)[1]).toEqual({ view: "news", cursor: "c1", limit: "50" });
    expect(root.textContent!.split("Invented n2 story")).toHaveLength(2);
    await vi.waitFor(() => expect(loadMore()).toBeUndefined());
  });

  it("is absent when the first page has no next_cursor", async () => {
    installViews({ feed: [{ items: [story("f1")], next_cursor: null }] });
    await loadStores();
    await open("/");
    await waitText("Invented f1 story");
    expect(loadMore()).toBeUndefined();
  });

  it("a page from a different run keeps the list, adds only new ids, and the next Refresh restarts at page 1", async () => {
    const api = installViews({
      news: [
        { items: [story("n1"), story("n2")], next_cursor: "c1", run_id: "run-a" },
        { items: [story("n2"), story("n9")], next_cursor: null, run_id: "run-b" },
      ],
    });
    await loadStores();
    await open("/news");
    await waitText("Invented n2 story");
    loadMore()!.click();
    await waitText("Invented n9 story");
    expect(root.textContent).toContain("Invented n1 story");
    expect(root.textContent!.split("Invented n2 story")).toHaveLength(2);
    refreshButton()!.click();
    await vi.waitFor(() => expect(api.requests).toHaveLength(3));
    expect(viewParams(api)[2]).toMatchObject({ view: "news", cursor: null });
    // The page-1 ETag was dropped, so this refresh is unconditional.
    expect(api.etags[2]).toBeNull();
  });

  it("a Refresh after Load more from the same run still sends the page-1 ETag", async () => {
    const api = installViews({
      news: [
        { items: [story("n1")], next_cursor: "c1" },
        { items: [story("n2")], next_cursor: null },
      ],
    });
    await loadStores();
    await open("/news");
    await waitText("Invented n1 story");
    await idle();
    loadMore()!.click();
    await waitText("Invented n2 story");
    refreshButton()!.click();
    await vi.waitFor(() => expect(api.requests).toHaveLength(3));
    expect(api.etags[2]).toBe('"news-0"');
  });

  it("Load more is disabled while a refresh is in flight", async () => {
    installViews({ news: [{ items: [story("n1")], next_cursor: "c1" }, { items: [story("n2")] }] });
    await loadStores();
    await open("/news");
    await waitText("Invented n1 story");
    await idle();
    expect(loadMore()!.disabled).toBe(false);
    // Hold the refresh's answer so the busy window can be observed.
    const inner = globalThis.fetch;
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    vi.stubGlobal("fetch", async (...args: Parameters<typeof fetch>) => {
      await gate;
      return inner(...args);
    });
    refreshButton()!.click();
    await vi.waitFor(() => expect(loadMore()!.disabled).toBe(true));
    release();
    await idle();
    expect(loadMore()!.disabled).toBe(false);
  });

  it("Load more pages are stored, so the list reopens offline with them", async () => {
    const api = installViews({
      news: [
        { items: [story("n1")], next_cursor: "c1" },
        { items: [story("n2")], next_cursor: null },
      ],
    });
    await loadStores();
    await open("/news");
    await waitText("Invented n1 story");
    await idle();
    loadMore()!.click();
    await waitText("Invented n2 story");
    await idle();
    api.online = false;
    await open("/news");
    await waitText("Invented n2 story");
    expect(root.textContent).toContain("Invented n1 story");
  });
});

describe("header icons", () => {
  it.each(["/news", "/", "/longer"])("%s has a Settings link, then Refresh, and no Liked heart (#307)", async (path) => {
    installViews(EMPTY);
    await loadStores();
    await open(path);
    await vi.waitFor(() => expect(root.querySelector('header [aria-label="Refresh"]')).not.toBeNull());
    const header = root.querySelector("header")!;
    expect(header.querySelector('a[aria-label="Liked"]')).toBeNull();
    const settings = header.querySelector('a[aria-label="Settings"]')!;
    const refresh = header.querySelector('[aria-label="Refresh"]')!;
    expect(settings.getAttribute("href")).toBe("/settings");
    expect(settings.querySelector("svg")).not.toBeNull();
    const before = (a: Element, b: Element) =>
      !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
    expect(before(settings, refresh)).toBe(true);
  });
});

describe("offline", () => {
  it("each tab shows its stored first page, and Load more is disabled with a note", async () => {
    const api = installViews({
      news: [{ items: [story("n1")], next_cursor: "c1" }],
      feed: [{ items: [story("f1")], next_cursor: "c1" }],
      longer: [{ items: [story("l1")], next_cursor: "c1" }],
    });
    const tabs = [
      ["/news", "n1"],
      ["/", "f1"],
      ["/longer", "l1"],
    ] as const;
    await loadStores();
    for (const [path, id] of tabs) {
      await open(path);
      await waitText(`Invented ${id} story`);
      await idle();
    }
    api.online = false;
    for (const [path, id] of tabs) {
      await open(path);
      await waitText(`Invented ${id} story`);
      await waitText("offline");
      expect(loadMore()?.disabled).toBe(true);
      expect(root.textContent).toContain("More when you're back online");
    }
  });
});

describe("shared story state", () => {
  it("a story dismissed on News is hidden on Feed too", async () => {
    installViews({
      news: [{ items: [story("shared"), story("n2")] }],
      feed: [{ items: [story("shared"), story("f2")] }],
    });
    await loadStores();
    await open("/news");
    await waitText("Invented shared story");
    buttonsNamed(root, /^\s*Dismiss$/)[0]!.click();
    await vi.waitFor(() => expect(root.textContent).toMatch(/undo/i));
    await open("/");
    await waitText("Invented f2 story");
    expect(root.textContent).not.toContain("Invented shared story");
  });
});

describe("article prefetch", () => {
  it("fetches at most 20 article bodies per view's first page", async () => {
    const api = installViews({
      news: [{ items: stories("n", 30) }],
      feed: [{ items: stories("f", 30) }],
      longer: [{ items: stories("l", 30) }],
    });
    await loadStores();
    for (const path of ["/news", "/", "/longer"]) {
      await open(path);
      await waitText("Invented");
      await idle();
    }
    await vi.waitFor(() => expect(api.articles.length).toBeGreaterThanOrEqual(60), { timeout: 3000 });
    await new Promise((r) => setTimeout(r, 100));
    for (const p of ["n", "f", "l"]) {
      expect(api.articles.filter((id) => id.startsWith(p))).toHaveLength(20);
    }
  });

  it("fetches none when Save-Data is on", async () => {
    const api = installViews({ news: [{ items: stories("n", 5) }] });
    vi.stubGlobal("navigator", { onLine: true, connection: { saveData: true } });
    await loadStores();
    await open("/news");
    await waitText("Invented n000 story");
    await new Promise((r) => setTimeout(r, 150));
    expect(api.articles).toEqual([]);
  });
});
