/**
 * The feedback outbox (#254). Every screen is a full page load, so leaving a screen straight
 * after a tap can end the page before the event's IndexedDB write finishes. So each event is
 * first written here, to `localStorage`, which is synchronous, inside the tap. The entry goes
 * once the queue holds the event; anything left is queued on the next start by `moveOutbox`,
 * with the id and time it was given at the tap.
 *
 * One key per event (`newsfeed-outbox:<id>`), not one shared list, so two tabs writing at the
 * same moment can't overwrite each other's entries.
 */
import { buildEvent, type FeedbackEventName, type FeedbackPayload } from "./event";
import { storeEvent } from "./queue";

const PREFIX = "newsfeed-outbox:";

/** Written by the reading-time hand-off before #254; drained once, then removed. */
const LEGACY_READS_KEY = "newsfeed-pending-reads";

const EVENT_NAMES: ReadonlySet<string> = new Set<FeedbackEventName>([
  "open",
  "read",
  "like",
  "dismiss",
  "mute_source",
]);

/** Keeps a copy of `payload` until `removeOutbox`. Blocked or full storage is logged, not thrown. */
export function writeOutbox(payload: FeedbackPayload): void {
  try {
    localStorage.setItem(PREFIX + payload.id, JSON.stringify(payload));
  } catch (err) {
    // The IndexedDB write is then the only copy, as before the outbox.
    console.warn("could not write the feedback outbox", { id: payload.id, err });
  }
}

export function removeOutbox(id: string): void {
  removeKey(PREFIX + id);
}

function removeKey(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch (err) {
    console.warn("could not remove a feedback outbox entry", { key, err });
  }
}

/** The payload in `raw`, or null when it isn't one. */
function parsePayload(raw: string | null): FeedbackPayload | null {
  let value: unknown;
  try {
    value = raw === null ? null : JSON.parse(raw);
  } catch {
    return null;
  }
  if (!value || typeof value !== "object") return null;
  const p = value as Record<string, unknown>;
  const ok =
    typeof p.id === "string" &&
    p.id !== "" &&
    typeof p.article_id === "string" &&
    typeof p.event === "string" &&
    EVENT_NAMES.has(p.event) &&
    (p.value === null || typeof p.value === "number") &&
    typeof p.created_at === "string" &&
    !Number.isNaN(Date.parse(p.created_at));
  if (!ok) return null;
  return {
    id: p.id as string,
    article_id: p.article_id as string,
    event: p.event as FeedbackEventName,
    value: p.value as number | null,
    created_at: p.created_at as string,
  };
}

/** Every outbox entry, valid ones oldest first; invalid ones are removed and logged. */
function readOutbox(): FeedbackPayload[] {
  const keys: string[] = [];
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i);
    if (key?.startsWith(PREFIX)) keys.push(key);
  }
  const entries: FeedbackPayload[] = [];
  for (const key of keys) {
    const payload = parsePayload(localStorage.getItem(key));
    if (payload) {
      entries.push(payload);
    } else {
      console.warn("dropped an invalid feedback outbox entry", { key });
      removeKey(key);
    }
  }
  return entries.sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at));
}

/**
 * The readings left under the pre-#254 key, as `read` events stamped now (they carry no time).
 * Each is written to the outbox before the key is removed, so a page that dies in between loses
 * none of them.
 */
function drainLegacyReads(): FeedbackPayload[] {
  const raw = localStorage.getItem(LEGACY_READS_KEY);
  if (raw === null) return [];
  const out: FeedbackPayload[] = [];
  let pending: unknown = null;
  try {
    pending = JSON.parse(raw);
  } catch {
    // Dropped below.
  }
  const reads = pending && typeof pending === "object" ? Object.values(pending) : [];
  for (const read of reads as { article_id?: unknown; value?: unknown }[]) {
    const value = Number(read?.value);
    if (typeof read?.article_id !== "string" || !Number.isInteger(value) || value < 0) {
      console.warn("dropped an invalid pending read", { read });
      continue;
    }
    const payload = buildEvent(read.article_id, "read", value);
    writeOutbox(payload);
    out.push(payload);
  }
  localStorage.removeItem(LEGACY_READS_KEY);
  return out;
}

/**
 * Queues every outbox entry, oldest first, keeping its own id and `created_at`, and removes each
 * only once the queue has it. An entry the queue refuses stays for the next start. Call once at
 * start-up, before the first flush.
 */
export async function moveOutbox(): Promise<void> {
  let entries: FeedbackPayload[];
  try {
    const legacy = drainLegacyReads();
    entries = readOutbox();
    // Also queued if its outbox write failed; otherwise it is already in `entries`.
    const ids = new Set(entries.map((e) => e.id));
    entries.push(...legacy.filter((e) => !ids.has(e.id)));
  } catch (err) {
    console.warn("could not read the feedback outbox", { err });
    return;
  }
  for (const payload of entries) {
    try {
      await storeEvent(payload);
    } catch (err) {
      console.error("could not queue a feedback outbox entry", { id: payload.id, err });
      continue;
    }
    removeOutbox(payload.id);
  }
}
