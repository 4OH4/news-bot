/**
 * Likes (#216). A like is owner feedback, so the event is queued first; the `liked` store (keyed
 * by story) and the kept article text are conveniences for the Liked list and offline reading.
 */
import { storyKey, type FeedItem } from "../feed/types";
import { getArticleHtml, markKept, putArticleHtml } from "../store/db";
import { openFeedbackDb, type StoredFeedItem } from "./db";
import { enqueueFeedback } from "./queue";

/**
 * The `liked` store and kept-text writes, one at a time in the order tapped, so a quick Like then
 * Unlike lands on the phone as not liked without the caller chaining.
 */
let stateWrites: Promise<void> = Promise.resolve();

/**
 * Likes or unlikes `item`. Call it synchronously inside the tap: the event is queued (and so in
 * the outbox) before anything is awaited. Liking also keeps its stored text past pruning,
 * storing `html` (what the reader shows) when no text is stored yet. Unliking drops the keep
 * mark, on the text stored under the id it was liked as too, but leaves the text for normal
 * pruning. The latest event wins on the server.
 */
export function setLiked(item: FeedItem, liked: boolean, html?: string): Promise<void> {
  const queued = enqueueFeedback(item.id, "like", liked ? 1 : 0);
  const state = stateWrites.then(() => writeLiked(item, liked, html));
  stateWrites = state.catch(() => {});
  return Promise.all([queued, state]).then(() => undefined);
}

async function writeLiked(item: FeedItem, liked: boolean, html?: string): Promise<void> {
  const db = await openFeedbackDb();
  const key = storyKey(item);
  const ids = [item.id];
  if (liked) {
    const stored = item as unknown as StoredFeedItem;
    await db.put("liked", { key, item: stored, at: new Date().toISOString() });
  } else {
    // A later feed may have re-keyed the story since the like; its old id's text was kept too.
    const row = await db.get("liked", key);
    if (row && row.item.id !== item.id) ids.push(row.item.id);
    await db.delete("liked", key);
  }
  try {
    if (liked && html !== undefined && (await getArticleHtml(item.id)) === null) {
      await putArticleHtml(item.id, html, { keep: true });
    } else {
      for (const id of ids) await markKept(id, liked);
    }
  } catch (err) {
    // The like itself is queued and recorded; only the offline copy is missing.
    console.warn("could not update kept article text", { article_id: item.id, err });
  }
}

export async function isLiked(item: FeedItem): Promise<boolean> {
  return (await (await openFeedbackDb()).count("liked", storyKey(item))) > 0;
}

/** Liked items, most recently liked first. */
export async function listLiked(): Promise<FeedItem[]> {
  const rows = await (await openFeedbackDb()).getAll("liked");
  rows.sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0));
  return rows.map((row) => row.item as unknown as FeedItem);
}

/** The liked item with this article id, or null; lets the reader open one that left the feed. */
export async function findLiked(id: string): Promise<FeedItem | null> {
  return (await listLiked()).find((item) => item.id === id) ?? null;
}
