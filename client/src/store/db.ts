import { openDB, type DBSchema, type IDBPDatabase } from "idb";
import type { Feed } from "../feed/types";

/**
 * The phone's copy of the feed and of article text. It holds nothing that cannot be fetched
 * again: feedback has its own database (#184).
 */
export const DB_NAME = "newsfeed";
const DB_VERSION = 1;
const FEED_KEY = "current";
const KEEP_DAYS = 7;
const DAY_MS = 24 * 60 * 60 * 1000;

export interface StoredFeed {
  feed: Feed;
  etag: string;
  fetchedAt: number;
}

interface ArticleRecord {
  id: string;
  html: string;
  storedAt: number;
  keep: boolean;
}

interface NewsfeedDB extends DBSchema {
  feed: { key: string; value: StoredFeed & { key: string } };
  articles: { key: string; value: ArticleRecord };
}

let dbPromise: Promise<IDBPDatabase<NewsfeedDB>> | null = null;

function db(): Promise<IDBPDatabase<NewsfeedDB>> {
  dbPromise ??= openDB<NewsfeedDB>(DB_NAME, DB_VERSION, {
    // Add a step per version here; never edit an earlier one.
    upgrade(database, oldVersion) {
      if (oldVersion < 1) {
        database.createObjectStore("feed", { keyPath: "key" });
        database.createObjectStore("articles", { keyPath: "id" });
      }
    },
  }).catch((err: unknown) => {
    dbPromise = null;
    throw err;
  });
  return dbPromise;
}

/** The stored feed with its etag and fetch time, or null when nothing is stored. */
export async function loadFeed(): Promise<StoredFeed | null> {
  const record = await (await db()).get("feed", FEED_KEY);
  if (!record) return null;
  return { feed: record.feed, etag: record.etag, fetchedAt: record.fetchedAt };
}

/** Replaces the one feed record. */
export async function saveFeed(feed: Feed, etag: string): Promise<void> {
  await (await db()).put("feed", { key: FEED_KEY, feed, etag, fetchedAt: Date.now() });
}

/** The stored article text, or null. */
export async function getArticleHtml(id: string): Promise<string | null> {
  return (await (await db()).get("articles", id))?.html ?? null;
}

export async function putArticleHtml(
  id: string,
  html: string,
  opts: { keep?: boolean } = {},
): Promise<void> {
  await (await db()).put("articles", { id, html, storedAt: Date.now(), keep: opts.keep ?? false });
}

/** Marks a stored article to survive pruning (or not). A missing article is left missing. */
export async function markKept(id: string, keep: boolean): Promise<void> {
  const tx = (await db()).transaction("articles", "readwrite");
  const record = await tx.store.get(id);
  if (record) await tx.store.put({ ...record, keep });
  await tx.done;
}

/** Drops articles that are not in `feed`, not kept, and stored more than 7 days ago. */
export async function pruneArticles(feed: Feed): Promise<void> {
  const inFeed = new Set(feed.items.map((i) => i.id));
  const cutoff = Date.now() - KEEP_DAYS * DAY_MS;
  const tx = (await db()).transaction("articles", "readwrite");
  for (const record of await tx.store.getAll()) {
    if (!inFeed.has(record.id) && !record.keep && record.storedAt < cutoff) {
      await tx.store.delete(record.id);
    }
  }
  await tx.done;
}

/** Removes the feed and every article, kept ones included (a sign-out the user chose). */
export async function clearAll(): Promise<void> {
  const tx = (await db()).transaction(["feed", "articles"], "readwrite");
  await Promise.all([tx.objectStore("feed").clear(), tx.objectStore("articles").clear()]);
  await tx.done;
}
