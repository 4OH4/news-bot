/**
 * Shared set-up for the reading, like and Liked-list tests (#216): invented feed items, a fresh
 * fake IndexedDB with fresh copies of the store and feedback modules, a fake API that can go
 * offline, and control over `document.visibilityState`. Every id, title and body is invented.
 */
import "fake-indexeddb/auto";
import { IDBFactory } from "fake-indexeddb";
import { openDB } from "idb";
import { vi } from "vitest";
import type { Feed, FeedItem } from "../feed/types";

/** An invented feed item; `overrides` replaces any field. */
export function makeItem(id: string, overrides: Partial<FeedItem> = {}): FeedItem {
  return {
    id,
    story_id: id,
    title: `Invented headline ${id}`,
    url: `https://invented.example.test/stories/${id}`,
    source_id: "invented-source",
    domain: "invented.example.test",
    author: "A. Nobody",
    published_at: "2026-11-01T08:00:00Z",
    summary: `An invented summary for ${id}.`,
    lead_image: null,
    score: 1,
    score_breakdown: {},
    extraction_status: "ok",
    display: "article",
    word_count: 400,
    content_path: `articles/${id}.html`,
    ...overrides,
  };
}

export function makeFeed(items: FeedItem[], runId = "run-invented-1"): Feed {
  return { schema_version: 1, generated_at: "2026-11-07T09:00:00Z", run_id: runId, items };
}

/** The invented article body served or stored for an id. */
export function bodyFor(id: string, label = "network"): string {
  return `<p>Invented ${label} body text for ${id}.</p>`;
}

export interface ApiCall {
  method: string;
  url: string;
  body: Record<string, unknown> | null;
}

/** The fake API. Tests change its fields as they go. */
export interface FakeRouter {
  /** False: every request fails as if the phone had no signal. */
  online: boolean;
  /** The feed `GET /api/feed` returns, or null for a 503. */
  feed: Feed | null;
  /** Article HTML by id; a missing id answers 404. */
  articles: Record<string, string>;
  /** How `POST /api/feedback` answers: a status, or "hang" (no answer ever comes). */
  feedback: number | "hang";
  /** Every request that reached the server, in order. */
  calls: ApiCall[];
}

function urlOf(input: unknown): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return (input as { url: string }).url;
}

/** Replaces `fetch` with the fake API. */
export function installRouter(initial: Partial<FakeRouter> = {}): FakeRouter {
  const router: FakeRouter = {
    online: true,
    feed: null,
    articles: {},
    feedback: 204,
    calls: [],
    ...initial,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown, init: RequestInit = {}): Promise<Response> => {
      if (!router.online) throw new TypeError("Failed to fetch");
      const url = urlOf(input);
      const path = new URL(url, "http://localhost").pathname;
      const method = String(init.method ?? "GET").toUpperCase();
      const body =
        typeof init.body === "string" ? (JSON.parse(init.body) as Record<string, unknown>) : null;
      router.calls.push({ method, url: path, body });
      if (path === "/api/feed") {
        if (!router.feed) return new Response(null, { status: 503 });
        return new Response(JSON.stringify(router.feed), {
          status: 200,
          headers: { "Content-Type": "application/json", ETag: `"${router.feed.run_id}"` },
        });
      }
      const article = /^\/api\/articles\/(.+)$/.exec(path);
      if (article) {
        const html = router.articles[decodeURIComponent(article[1]!)];
        return html === undefined
          ? new Response(null, { status: 404 })
          : new Response(html, { status: 200, headers: { "Content-Type": "text/html" } });
      }
      if (path === "/api/feedback") {
        if (router.feedback === "hang") {
          const signal = init.signal;
          return new Promise<Response>((_resolve, reject) => {
            signal?.addEventListener("abort", () => reject(signal.reason), { once: true });
          });
        }
        return new Response(null, { status: router.feedback });
      }
      return new Response(null, { status: 200 });
    }),
  );
  return router;
}

/** Requests for article text that reached the server. */
export function articleCalls(router: FakeRouter): ApiCall[] {
  return router.calls.filter((c) => c.url.startsWith("/api/articles/"));
}

/** Feedback POSTs that reached the server. */
export function feedbackPosts(router: FakeRouter): Record<string, unknown>[] {
  return router.calls
    .filter((c) => c.url === "/api/feedback" && c.method === "POST")
    .map((c) => c.body ?? {});
}

/**
 * Fresh copies of the feedback database, the queue and the feed/article store against an empty
 * IndexedDB. Modules imported after this (`await import(...)`) share the same fresh copies.
 */
export async function loadStores() {
  vi.resetModules();
  vi.stubGlobal("indexedDB", new IDBFactory());
  const feedbackDb = await import("../feedback/db");
  const queue = await import("../feedback/queue");
  const store = await import("../store/db");
  return { ...feedbackDb, ...queue, ...store };
}

export type Stores = Awaited<ReturnType<typeof loadStores>>;

/** Every queued feedback event, oldest first. */
export async function queuedEvents(s: Stores): Promise<Record<string, unknown>[]> {
  const rows = await (await s.openFeedbackDb()).getAll("queue");
  return [...rows].sort((a, b) => a.seq - b.seq) as unknown as Record<string, unknown>[];
}

/** The raw stored article record (`id`, `html`, `storedAt`, `keep`), or undefined. */
export async function articleRecord(
  id: string,
): Promise<{ id: string; html: string; storedAt: number; keep: boolean } | undefined> {
  // Opening a database that doesn't exist would create it empty, so check first.
  const existing = await indexedDB.databases();
  if (!existing.some((d) => d.name === "newsfeed")) return undefined;
  const db = await openDB("newsfeed");
  try {
    if (!db.objectStoreNames.contains("articles")) return undefined;
    return (await db.get("articles", id)) as
      | { id: string; html: string; storedAt: number; keep: boolean }
      | undefined;
  } finally {
    db.close();
  }
}

/**
 * Sets what `document.visibilityState` reports and, unless `notify` is false, fires
 * `visibilitychange` the way a browser does when the app is hidden or shown.
 */
export function setVisibility(state: "visible" | "hidden", notify = true): void {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  Object.defineProperty(document, "hidden", { configurable: true, get: () => state === "hidden" });
  if (notify) document.dispatchEvent(new Event("visibilitychange", { bubbles: true }));
}

/** Removes the overrides `setVisibility` made. */
export function resetVisibility(): void {
  delete (document as unknown as Record<string, unknown>).visibilityState;
  delete (document as unknown as Record<string, unknown>).hidden;
}

/** A stand-in for `location` at `path`, recording navigations instead of making them. */
export function locationAt(path: string) {
  return {
    href: `http://localhost${path}`,
    origin: "http://localhost",
    pathname: path,
    search: "",
    hash: "",
    replace: vi.fn(),
    assign: vi.fn(),
  };
}

/** Lets background work (unawaited flushes, effects) finish. */
export async function settle(ms = 50): Promise<void> {
  await new Promise((r) => setTimeout(r, ms));
}

/** Buttons whose accessible name (text or aria-label) matches. */
export function buttonsNamed(container: ParentNode, name: RegExp): HTMLButtonElement[] {
  return [...container.querySelectorAll("button")].filter((b) =>
    name.test(`${b.getAttribute("aria-label") ?? ""} ${b.textContent ?? ""}`),
  );
}
