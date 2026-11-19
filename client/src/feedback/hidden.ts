/**
 * Dismissed stories and muted sources (#217). Each change is owner feedback, so its event is
 * queued first. A dismissed story is kept in the `dismissed` store by `storyKey`, so it stays
 * hidden when a later feed re-keys it. Muting a source sets it to Hide on the server (#339): the
 * server filters the feed, and until a change is sent it waits in a `localStorage` outbox.
 */
import { storyKey, type FeedItem } from "../feed/types";
import { openFeedbackDb, type FeedbackDb, type StoredFeedItem } from "./db";
import type { FeedbackContext } from "./event";
import { setSourceStates } from "../preferences/api";
import { enqueueFeedback } from "./queue";

const PENDING_KEY = "pending-source-states";
const MIGRATED_KEY = "muted-migrated";
type SourceChanges = Record<string, "hide" | "show">;

// localStorage, not IndexedDB: the write must land inside the tap, before a page change (REVIEW.md).
function readPending(): SourceChanges {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(PENDING_KEY) ?? "{}");
    return parsed && typeof parsed === "object" ? (parsed as SourceChanges) : {};
  } catch {
    return {};
  }
}

function writePending(pending: SourceChanges): void {
  try {
    if (Object.keys(pending).length === 0) localStorage.removeItem(PENDING_KEY);
    else localStorage.setItem(PENDING_KEY, JSON.stringify(pending));
  } catch {
    // Storage blocked: the change is still sent now, it just can't wait offline.
  }
}

/** Sends every pending change in one PUT; failures leave the outbox for the next successful call. */
async function flushPending(): Promise<void> {
  const sent = readPending();
  if (Object.keys(sent).length === 0) return;
  try {
    await setSourceStates(sent);
  } catch (err) {
    console.warn("could not send pending source changes", err);
    return;
  }
  // Keep entries changed while the PUT was in flight.
  const now = readPending();
  for (const [id, state] of Object.entries(sent)) if (now[id] === state) delete now[id];
  writePending(now);
  changed();
}

/**
 * Moves the sources muted on the phone before #339 into the saved preferences as Hide, once
 * (flag `muted-migrated`), then sends any pending change. Offline it does nothing and the flag
 * stays unset, so the next online load tries again.
 */
export async function migrateMutedSources(): Promise<void> {
  try {
    if (!localStorage.getItem(MIGRATED_KEY)) {
      const rows = await (await openFeedbackDb()).getAll("muted");
      if (rows.length > 0) {
        await setSourceStates(Object.fromEntries(rows.map((r) => [r.source_id, "hide" as const])));
      }
      localStorage.setItem(MIGRATED_KEY, new Date().toISOString());
    }
    await flushPending();
  } catch (err) {
    console.warn("could not migrate muted sources", err);
  }
}

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
  context?: FeedbackContext,
): Promise<void> {
  const queued = enqueueFeedback(item.id, event, value, context);
  const state = stateWrites.then(async () => {
    await write(await openFeedbackDb());
    changed();
  });
  stateWrites = state.catch(() => {});
  return Promise.all([queued, state]).then(() => undefined);
}

export function dismissStory(item: FeedItem, context?: FeedbackContext): Promise<void> {
  const row = { key: storyKey(item), item: stored(item), at: new Date().toISOString() };
  return record(item, "dismiss", 1, (db) => db.put("dismissed", row), context);
}

/** Shows the story again; the server takes the later `dismiss` 0 (a new event id) as the undo. */
export function undoDismiss(item: FeedItem, context?: FeedbackContext): Promise<void> {
  return record(item, "dismiss", 0, (db) => db.delete("dismissed", storyKey(item)), context);
}

function setSource(
  item: FeedItem,
  state: "hide" | "show",
  context?: FeedbackContext,
): Promise<void> {
  // Written inside the tap, so a page change straight after can't lose it.
  writePending({ ...readPending(), [item.source_id]: state });
  // The `muted` store no longer filters; it only feeds Settings' "Muted sources" list.
  const row = { source_id: item.source_id, item: stored(item), at: new Date().toISOString() };
  const write = async (db: FeedbackDb) => {
    await (state === "hide" ? db.put("muted", row) : db.delete("muted", item.source_id));
    await flushPending();
  };
  return record(item, "mute_source", state === "hide" ? 1 : 0, write, context);
}

/** Sets the source to Hide: queued as owner feedback and sent to the preferences API (#339). */
export function muteSource(item: FeedItem, context?: FeedbackContext): Promise<void> {
  return setSource(item, "hide", context);
}

/**
 * Unmutes the source of `item`, the item it was muted from. The undo event carries that item's
 * id, because the event needs an `article_id` and the item may have left the feed long ago.
 */
export function unmuteSource(item: FeedItem, context?: FeedbackContext): Promise<void> {
  return setSource(item, "show", context);
}

export async function isHidden(item: FeedItem): Promise<boolean> {
  const dismissed = await (await openFeedbackDb()).count("dismissed", storyKey(item));
  // A mute not yet sent still hides the source, so an offline mute works at once.
  return dismissed > 0 || readPending()[item.source_id] === "hide";
}

/** Sources muted on the phone before #339, newest first; Unmute sets them to Show. */
export async function listMuted(): Promise<FeedItem[]> {
  const rows = await (await openFeedbackDb()).getAll("muted");
  rows.sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0));
  return rows.map((row) => row.item as unknown as FeedItem);
}
