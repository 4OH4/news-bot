/**
 * The feedback outbox (#254). Every screen is a full page load, so leaving one straight after a
 * tap can end the page before the event's IndexedDB write finishes. So each event is first
 * written here, synchronously, inside the tap, and removed once the queue holds it; `moveOutbox`
 * queues what is left on the next start, with the id and time given at the tap. One key per
 * event, not a shared list, so two tabs writing at once can't overwrite each other's entries.
 */
import { buildEvent, toPayload, type FeedbackPayload } from "./event";
import { storeEvent } from "./queue";

const PREFIX = "newsfeed-outbox:";
/** Written by the reading-time hand-off before #254; drained once, then removed. */
const LEGACY_READS_KEY = "newsfeed-pending-reads";
const EVENT_NAMES = new Set(["open", "read", "like", "dismiss", "mute_source"]);

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
  try {
    localStorage.removeItem(PREFIX + id);
  } catch (err) {
    console.warn("could not remove a feedback outbox entry", { id, err });
  }
}

/** The payload in `raw`, or null when it isn't a valid one. */
function parsePayload(raw: string | null): FeedbackPayload | null {
  let p: Record<string, unknown> | null;
  try {
    p = raw === null ? null : (JSON.parse(raw) as Record<string, unknown> | null);
  } catch {
    return null;
  }
  const valid =
    typeof p === "object" &&
    p !== null &&
    typeof p.id === "string" &&
    p.id !== "" &&
    typeof p.article_id === "string" &&
    EVENT_NAMES.has(p.event as string) &&
    (p.value === null || typeof p.value === "number") &&
    typeof p.created_at === "string" &&
    !Number.isNaN(Date.parse(p.created_at));
  return valid ? toPayload(p as unknown as FeedbackPayload) : null;
}

/**
 * The readings left under the pre-#254 key, as `read` events stamped now (they carry no time),
 * each written to the outbox before the key goes, so a page that dies in between loses none.
 */
function drainLegacyReads(): FeedbackPayload[] {
  const raw = localStorage.getItem(LEGACY_READS_KEY);
  if (raw === null) return [];
  let pending: unknown = null;
  try {
    pending = JSON.parse(raw);
  } catch {
    // Dropped with the key below.
  }
  const out: FeedbackPayload[] = [];
  for (const read of Object.values(pending ?? {}) as { article_id?: unknown; value?: unknown }[]) {
    const value = Number(read?.value);
    if (typeof read?.article_id !== "string" || !Number.isInteger(value) || value < 0) {
      console.warn("dropped an invalid pending read", { read });
      continue;
    }
    out.push(buildEvent(read.article_id, "read", value));
    writeOutbox(out[out.length - 1]!);
  }
  localStorage.removeItem(LEGACY_READS_KEY);
  return out;
}

/** Every valid outbox entry, oldest first; invalid entries are removed and logged. */
function readOutbox(legacy: FeedbackPayload[]): FeedbackPayload[] {
  // A legacy read whose outbox write failed is queued all the same.
  const entries = new Map(legacy.map((p) => [p.id, p]));
  const keys = Array.from({ length: localStorage.length }, (_, i) => localStorage.key(i));
  for (const key of keys) {
    if (!key?.startsWith(PREFIX)) continue;
    const payload = parsePayload(localStorage.getItem(key));
    if (payload) {
      entries.set(payload.id, payload);
    } else {
      console.warn("dropped an invalid feedback outbox entry", { key });
      localStorage.removeItem(key);
    }
  }
  return [...entries.values()].sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at));
}

/**
 * Queues every outbox entry, oldest first, keeping its own id and `created_at`, and removes each
 * only once the queue has it. An entry the queue refuses stays for the next start. Call once at
 * start-up, before the first flush.
 */
export async function moveOutbox(): Promise<void> {
  let entries: FeedbackPayload[];
  try {
    entries = readOutbox(drainLegacyReads());
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
