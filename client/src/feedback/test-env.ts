/**
 * Shared set-up for the feedback tests (#184): a fresh fake IndexedDB, fresh copies of the
 * modules, and a fake `fetch` that answers `POST /api/feedback` the way each test chooses.
 * Every id, title and source here is invented.
 */
import "fake-indexeddb/auto";
import { IDBFactory } from "fake-indexeddb";
import { vi } from "vitest";

/**
 * How the fake server answers one feedback POST: an HTTP status; "offline" (the request never
 * left the phone); "lost" (the server got it, the answer never came back); "hang" (no answer yet).
 */
export type Answer = number | "offline" | "lost" | "hang";

export interface Post {
  url: string;
  method: string;
  headers: Headers;
  rawBody: string;
  body: Record<string, unknown>;
  id: string;
}

export interface FakeApi {
  /** Every feedback POST that reached the server, in order. */
  posts: Post[];
  /** Feedback POSTs attempted, including those that never reached the server. */
  attempts: number;
  /** Every other URL fetched (the feed, the sign-out request). */
  other: string[];
  /** Chooses the answer for each feedback POST; tests replace it as they go. */
  answer: (body: Record<string, unknown>) => Answer;
  /** Delay before a reached POST is answered, to keep two flushes overlapping. */
  delayMs: number;
}

function urlOf(input: unknown): string {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return (input as { url: string }).url;
}

/** Replaces `fetch` with the fake server. */
export function installFakeApi(initial: Answer = 204): FakeApi {
  const api: FakeApi = { posts: [], attempts: 0, other: [], answer: () => initial, delayMs: 0 };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown, init: RequestInit = {}): Promise<Response> => {
      const url = urlOf(input);
      if (!url.endsWith("/api/feedback")) {
        api.other.push(url);
        return new Response(null, { status: 200 });
      }
      api.attempts++;
      const rawBody = String(init.body);
      const body = JSON.parse(rawBody) as Record<string, unknown>;
      const answer = api.answer(body);
      if (answer === "offline") throw new TypeError("Failed to fetch");
      if (answer === "hang") return new Promise<Response>(() => {});
      api.posts.push({
        url,
        method: String(init.method ?? "GET").toUpperCase(),
        headers: new Headers(init.headers),
        rawBody,
        body,
        id: String(body.id),
      });
      if (api.delayMs > 0) await new Promise((r) => setTimeout(r, api.delayMs));
      if (answer === "lost") throw new TypeError("Failed to fetch");
      return new Response(null, { status: answer });
    }),
  );
  return api;
}

/** Answers by `article_id`, with `fallback` for any other article. */
export function answerBy(
  map: Record<string, Answer>,
  fallback: Answer = 204,
): (body: Record<string, unknown>) => Answer {
  return (body) => map[String(body.article_id)] ?? fallback;
}

/** Lets background flushes (started without being awaited) finish. */
export async function settle(ms = 50): Promise<void> {
  await new Promise((r) => setTimeout(r, ms));
}

/** Loads fresh copies of every feedback and auth module against an empty IndexedDB. */
export async function loadFeedback() {
  vi.resetModules();
  vi.stubGlobal("indexedDB", new IDBFactory());
  const db = await import("./db");
  const event = await import("./event");
  const queue = await import("./queue");
  const seen = await import("./seen");
  const signout = await import("../auth/signout");
  const store = await import("../auth/status");
  const status = (): unknown => {
    const v: unknown = store.sessionStatus;
    return typeof v === "function" ? (v as () => unknown)() : v;
  };
  return { ...db, ...event, ...queue, ...seen, ...signout, ...store, status };
}

interface StoreLike {
  keyPath: string | string[] | null;
  put(value: unknown, key?: IDBValidKey): Promise<unknown>;
  count(): Promise<number>;
}
interface DbLike {
  transaction(name: string, mode?: IDBTransactionMode): { store: StoreLike; done: Promise<void> };
}

/** Writes a row the way #216 and #217 will, whether the store's key is inline or not. */
export async function putRaw(
  db: unknown,
  storeName: string,
  value: Record<string, unknown>,
  key: string,
): Promise<void> {
  const tx = (db as DbLike).transaction(storeName, "readwrite");
  await (tx.store.keyPath === null ? tx.store.put(value, key) : tx.store.put(value));
  await tx.done;
}

/** Number of rows in one store. */
export async function countRows(db: unknown, storeName: string): Promise<number> {
  return (db as DbLike).transaction(storeName).store.count();
}

/** An invented feed item, as #216 and #217 will store it. */
export const ITEM = { id: "b7c1e0d2a9f43851", source_id: "invented-blog", title: "A made-up story" };
export const STORY_KEY = "story-invented-1";

/** Fills the per-story stores (`seen`, `dismissed`, `muted`, `liked`) with one row each. */
export async function fillStoryState(f: Awaited<ReturnType<typeof loadFeedback>>): Promise<void> {
  const db = await f.openFeedbackDb();
  const at = "2026-11-07T10:00:00.000Z";
  await f.markSeen(STORY_KEY);
  await putRaw(db, "dismissed", { key: STORY_KEY, item: ITEM, at }, STORY_KEY);
  await putRaw(db, "muted", { source_id: ITEM.source_id, item: ITEM, at }, ITEM.source_id);
  await putRaw(db, "liked", { key: STORY_KEY, item: ITEM, at }, STORY_KEY);
}

/** Rows left in each per-story store. */
export async function storyStateCounts(
  f: Awaited<ReturnType<typeof loadFeedback>>,
): Promise<Record<string, number>> {
  const db = await f.openFeedbackDb();
  const out: Record<string, number> = {};
  for (const name of ["seen", "dismissed", "muted", "liked"]) out[name] = await countRows(db, name);
  return out;
}

/** A stand-in for `location` that records replacements instead of navigating. */
export function fakeLocation() {
  return { href: "/", pathname: "/", replace: vi.fn(), assign: vi.fn() };
}
