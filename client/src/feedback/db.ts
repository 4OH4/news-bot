/**
 * The phone's feedback database, `newsfeed-feedback` (#184). It is separate from the feed cache
 * (#183), so neither one's version upgrade can touch the other's data. Every store #216 and
 * #217 need is defined here at version 1, so they add no upgrade of their own.
 */
import { openDB, type DBSchema, type IDBPDatabase } from "idb";
import type { FeedbackPayload } from "./event";

/** A feed item as given, typed loosely so this module doesn't depend on the feed's types. */
export type StoredFeedItem = { id: string; source_id: string } & Record<string, unknown>;

/** A queued event: the payload plus `seq`, its place in the queue (never posted). */
export type QueuedEvent = FeedbackPayload & { seq: number };

export interface FeedbackSchema extends DBSchema {
  queue: { key: string; value: QueuedEvent; indexes: { seq: number } };
  rejected: { key: string; value: FeedbackPayload };
  seen: { key: string; value: { key: string; at: string } };
  dismissed: { key: string; value: { key: string; item: StoredFeedItem; at: string } };
  muted: { key: string; value: { source_id: string; item: StoredFeedItem; at: string } };
  liked: { key: string; value: { key: string; item: StoredFeedItem; at: string } };
}

export type FeedbackDb = IDBPDatabase<FeedbackSchema>;

const NAME = "newsfeed-feedback";
const VERSION = 1;
export const FEEDBACK_STORES = ["queue", "rejected", "seen", "dismissed", "muted", "liked"] as const;

let opening: Promise<FeedbackDb> | null = null;

/** Opens (and on first use creates) the database; the connection is shared. */
export function openFeedbackDb(): Promise<FeedbackDb> {
  if (!opening) {
    opening = openDB<FeedbackSchema>(NAME, VERSION, {
      upgrade(db) {
        db.createObjectStore("queue", { keyPath: "id" }).createIndex("seq", "seq");
        db.createObjectStore("rejected", { keyPath: "id" });
        db.createObjectStore("seen", { keyPath: "key" });
        db.createObjectStore("dismissed", { keyPath: "key" });
        db.createObjectStore("muted", { keyPath: "source_id" });
        db.createObjectStore("liked", { keyPath: "key" });
      },
      // Another tab is upgrading: let it, and reopen on next use.
      blocking() {
        void opening?.then((db) => db.close());
        opening = null;
      },
      terminated() {
        opening = null;
      },
    });
    opening.catch(() => {
      opening = null;
    });
  }
  return opening;
}

/**
 * Empties every store, rejected events and per-story state included. Only the sign-out hook
 * calls this, after the guard has let the sign-out go ahead.
 */
export async function clearFeedbackData(): Promise<void> {
  const db = await openFeedbackDb();
  const tx = db.transaction([...FEEDBACK_STORES], "readwrite");
  await Promise.all([...FEEDBACK_STORES.map((name) => tx.objectStore(name).clear()), tx.done]);
}
