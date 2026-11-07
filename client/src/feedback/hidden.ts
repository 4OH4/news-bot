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

/** The `dismissed` and `muted` writes, in the order tapped, so an Undo can't land first. */
let stateWrites: Promise<void> = Promise.resolve();

/**
 * Queues the event synchronously, so every change below must be called inside the tap, then
 * chains `write`. The state write doesn't wait for the queue write: they are separate stores.
 */
function record(
  item: FeedItem,
  event: "dismiss" | "mute_source",
  value: number,
  write: (db: FeedbackDb) => Promise<unknown>,
): Promise<void> {
  const queued = enqueueFeedback(item.id, event, value);
  const state = stateWrites.then(async () => {
    await write(await openFeedbackDb());
    changed();
  });
  stateWrites = state.catch(() => {});
  return Promise.all([queued, state]).then(() => undefined);
}

export function dismissStory(item: FeedItem): Promise<void> {
  const row = { key: storyKey(item), item: stored(item), at: new Date().toISOString() };
  return record(item, "dismiss", 1, (db) => db.put("dismissed", row));
}

/** Shows the story again; the server takes the later `dismiss` 0 (a new event id) as the undo. */
export function undoDismiss(item: FeedItem): Promise<void> {
  return record(item, "dismiss", 0, (db) => db.delete("dismissed", storyKey(item)));
}

export function muteSource(item: FeedItem): Promise<void> {
  const row = { source_id: item.source_id, item: stored(item), at: new Date().toISOString() };
  return record(item, "mute_source", 1, (db) => db.put("muted", row));
}

/**
 * Unmutes the source of `item`, the item it was muted from. The undo event carries that item's
 * id, because the event needs an `article_id` and the item may have left the feed long ago.
 */
export function unmuteSource(item: FeedItem): Promise<void> {
  return record(item, "mute_source", 0, (db) => db.delete("muted", item.source_id));
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
