/**
 * Dismissed stories and muted sources (#217). Each change is owner feedback, so its event is
 * queued first; the `dismissed` and `muted` stores only decide what the phone shows. A story is
 * kept by `storyKey`, so it stays hidden when a later feed re-keys it.
 */
import { storyKey, type FeedItem } from "../feed/types";
import { openFeedbackDb, type StoredFeedItem } from "./db";
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

export async function dismissStory(item: FeedItem): Promise<void> {
  await enqueueFeedback(item.id, "dismiss", 1);
  const db = await openFeedbackDb();
  const at = new Date().toISOString();
  await db.put("dismissed", { key: storyKey(item), item: stored(item), at });
  changed();
}

/** Shows the story again; the server takes the later `dismiss` 0 (a new event id) as the undo. */
export async function undoDismiss(item: FeedItem): Promise<void> {
  await enqueueFeedback(item.id, "dismiss", 0);
  await (await openFeedbackDb()).delete("dismissed", storyKey(item));
  changed();
}

export async function muteSource(item: FeedItem): Promise<void> {
  await enqueueFeedback(item.id, "mute_source", 1);
  const db = await openFeedbackDb();
  const at = new Date().toISOString();
  await db.put("muted", { source_id: item.source_id, item: stored(item), at });
  changed();
}

/**
 * Unmutes a source. The undo event carries the stored item's id, because the event needs an
 * `article_id` and that item may have left the feed long ago.
 */
export async function unmuteSource(sourceId: string): Promise<void> {
  const db = await openFeedbackDb();
  const row = await db.get("muted", sourceId);
  if (!row) return;
  await enqueueFeedback(row.item.id, "mute_source", 0);
  await db.delete("muted", sourceId);
  changed();
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
