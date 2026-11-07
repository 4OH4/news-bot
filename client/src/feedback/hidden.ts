/**
 * Dismissed stories and muted sources (#217). Each change is owner feedback, so its event is
 * queued first; the `dismissed` and `muted` stores only decide what the phone shows. A story is
 * kept by `storyKey`, so it stays hidden when a later feed re-keys it.
 */
import { storyKey, type FeedItem } from "../feed/types";
import { openFeedbackDb, type FeedbackDb, type StoredFeedItem } from "./db";
import { enqueueFeedback } from "./queue";

const listeners = new Set<() => void>();

function changed(): void {
  for (const listener of [...listeners]) listener();
}

/** Calls `listener` after every dismiss, mute and undo; returns the unsubscribe. */
export function subscribeHidden(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function stored(item: FeedItem): StoredFeedItem {
  return item as unknown as StoredFeedItem;
}

/**
 * The `dismissed` and `muted` writes, one at a time in the order tapped, so an Undo's delete
 * can't land before the write of the action it undoes, without the caller chaining.
 */
let stateWrites: Promise<void> = Promise.resolve();

/**
 * Adds `write` to the state chain beside the event the caller has already queued, inside the
 * tap. The state write doesn't wait for the queue write: they are separate stores.
 */
function record(
  queued: Promise<unknown>,
  write: (db: FeedbackDb) => Promise<unknown>,
): Promise<void> {
  const state = stateWrites.then(async () => {
    await write(await openFeedbackDb());
    changed();
  });
  stateWrites = state.catch(() => {});
  return Promise.all([queued, state]).then(() => undefined);
}

/** Each change below queues its event synchronously, so call it inside the tap. */
export function dismissStory(item: FeedItem): Promise<void> {
  const queued = enqueueFeedback(item.id, "dismiss", 1);
  const at = new Date().toISOString();
  return record(queued, (db) =>
    db.put("dismissed", { key: storyKey(item), item: stored(item), at }),
  );
}

/** Shows the story again; the server takes the later `dismiss` 0 (a new event id) as the undo. */
export function undoDismiss(item: FeedItem): Promise<void> {
  const queued = enqueueFeedback(item.id, "dismiss", 0);
  return record(queued, (db) => db.delete("dismissed", storyKey(item)));
}

export function muteSource(item: FeedItem): Promise<void> {
  const queued = enqueueFeedback(item.id, "mute_source", 1);
  const at = new Date().toISOString();
  return record(queued, (db) =>
    db.put("muted", { source_id: item.source_id, item: stored(item), at }),
  );
}

/**
 * Unmutes the source of `item`, the item it was muted from (Settings lists those). The undo
 * event carries that item's id, because the event needs an `article_id` and the item may have
 * left the feed long ago.
 */
export function unmuteSource(item: FeedItem): Promise<void> {
  const queued = enqueueFeedback(item.id, "mute_source", 0);
  return record(queued, (db) => db.delete("muted", item.source_id));
}

export async function isHidden(item: FeedItem): Promise<boolean> {
  const tx = (await openFeedbackDb()).transaction(["dismissed", "muted"]);
  const [dismissed, muted] = await Promise.all([
    tx.objectStore("dismissed").count(storyKey(item)),
    tx.objectStore("muted").count(item.source_id),
    tx.done,
  ]);
  return dismissed > 0 || muted > 0;
}

/** The item each source was muted from, most recently muted first. */
export async function listMuted(): Promise<FeedItem[]> {
  const rows = await (await openFeedbackDb()).getAll("muted");
  rows.sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0));
  return rows.map((row) => row.item as unknown as FeedItem);
}
