/**
 * The outgoing feedback queue (#184). An event is written to IndexedDB before anything else
 * happens and stays there until the server has accepted it, because owner feedback can't be
 * recreated. Sends go oldest first, one at a time, and stop at the first failure a retry could
 * fix, so order is kept and nothing is lost.
 */
import { apiFetch } from "../api/client";
import { openFeedbackDb, type FeedbackDb, type QueuedEvent } from "./db";
import { buildEvent, toPayload, type FeedbackEventName, type FeedbackPayload } from "./event";

/** Answers a retry could fix: the entry stays and the flush stops. */
const RETRYABLE = new Set([401, 403, 408, 429]);

/**
 * How long one feedback POST may take. A send that never answers would otherwise hold the flush,
 * and with it every later flush and the sign-out dialog's send, until the page closes.
 */
export const SEND_TIMEOUT_MS = 30_000;

/**
 * The time limit for one POST. `AbortSignal.timeout` is missing on older browsers (Safari before
 * 16); there a timer does the same job, rather than the call throwing and the queue never sending.
 */
function sendTimeout(): AbortSignal {
  if (typeof AbortSignal.timeout === "function") return AbortSignal.timeout(SEND_TIMEOUT_MS);
  const controller = new AbortController();
  setTimeout(
    () => controller.abort(new DOMException("The operation timed out.", "TimeoutError")),
    SEND_TIMEOUT_MS,
  );
  return controller.signal;
}

/**
 * Adds an event to the queue, resolving once it is stored, then starts a send. The write happens
 * at once, even while a sign-out is asking: the sign-out deletes only the events it counted.
 */
export async function enqueueFeedback(
  articleId: string,
  event: FeedbackEventName,
  value?: number,
): Promise<FeedbackPayload> {
  const payload = buildEvent(articleId, event, value);
  const db = await openFeedbackDb();
  const tx = db.transaction("queue", "readwrite");
  const last = await tx.store.index("seq").openCursor(null, "prev");
  await tx.store.add({ ...payload, seq: (last?.value.seq ?? 0) + 1 });
  await tx.done;
  void flushQueue().catch((err: unknown) => console.error("feedback flush failed", err));
  return payload;
}

let running: Promise<void> | null = null;
let again = false;

/**
 * Sends the queue. Only one flush runs at a time: a call during a flush waits for it, and if
 * that flush emptied the queue it runs once more, for events added meanwhile.
 */
export function flushQueue(): Promise<void> {
  if (running) {
    again = true;
    return running;
  }
  running = (async () => {
    try {
      let result: "empty" | "stopped";
      do {
        again = false;
        result = await drain();
      } while (again && result === "empty");
    } finally {
      running = null;
    }
  })();
  return running;
}

async function oldest(db: FeedbackDb): Promise<QueuedEvent | undefined> {
  const cursor = await db.transaction("queue").store.index("seq").openCursor();
  return cursor?.value;
}

async function drain(): Promise<"empty" | "stopped"> {
  const db = await openFeedbackDb();
  for (;;) {
    const row = await oldest(db);
    if (!row) return "empty";
    const payload = toPayload(row);
    const signal = sendTimeout();
    let response: Response;
    try {
      response = await apiFetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal,
      });
    } catch {
      // Offline, a lost answer, the time limit, 401 or 403 (apiFetch reports the last two):
      // keep and stop.
      return "stopped";
    }
    const { status } = response;
    if (status >= 200 && status < 300) {
      await db.delete("queue", row.id);
    } else if (status >= 400 && status < 500 && !RETRYABLE.has(status)) {
      // The server will never take this event; set it aside so it can't block the queue.
      const tx = db.transaction(["queue", "rejected"], "readwrite");
      await Promise.all([
        tx.objectStore("rejected").put(payload),
        tx.objectStore("queue").delete(row.id),
        tx.done,
      ]);
      console.warn("feedback event rejected", { id: payload.id, event: payload.event, status });
    } else {
      return "stopped";
    }
  }
}

export async function queuedCount(): Promise<number> {
  return (await openFeedbackDb()).count("queue");
}

export async function rejectedCount(): Promise<number> {
  return (await openFeedbackDb()).count("rejected");
}

/** The ids of every queued and rejected event, read in one transaction. */
export async function unsentIds(): Promise<{ queued: string[]; rejected: string[] }> {
  const tx = (await openFeedbackDb()).transaction(["queue", "rejected"]);
  const [queued, rejected] = await Promise.all([
    tx.objectStore("queue").getAllKeys(),
    tx.objectStore("rejected").getAllKeys(),
    tx.done,
  ]);
  return { queued, rejected };
}

export async function listRejected(): Promise<FeedbackPayload[]> {
  return (await openFeedbackDb()).getAll("rejected");
}
