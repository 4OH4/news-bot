/**
 * Reading time (#216): how long an article was actually on screen, sent as one `read` event per
 * reader opening. Only time while the page is visible counts, so a phone left in a pocket with
 * the reader open doesn't look like a long read.
 *
 * Every screen is a full page load, so leaving the reader unloads the page: no cleanup runs and
 * an IndexedDB write started then never finishes. So a finished reading is first written to
 * `localStorage`, which is synchronous, and moved into the queue by `movePendingReads` on the
 * next start if the page went away before the queue had it.
 */
import type { FeedItem } from "../feed/types";
import { newEventId } from "./event";
import { enqueueFeedback } from "./queue";

/** Readings shorter than this send nothing: a glance is not a read. */
export const MIN_READ_SECONDS = 5;

const PENDING_KEY = "newsfeed-pending-reads";

interface PendingRead {
  article_id: string;
  value: number;
}

function readPending(): Record<string, PendingRead> {
  try {
    const raw = localStorage.getItem(PENDING_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? (parsed as Record<string, PendingRead>) : {};
  } catch {
    return {};
  }
}

function updatePending(change: (pending: Record<string, PendingRead>) => void): void {
  try {
    const pending = readPending();
    change(pending);
    if (Object.keys(pending).length === 0) localStorage.removeItem(PENDING_KEY);
    else localStorage.setItem(PENDING_KEY, JSON.stringify(pending));
  } catch {
    // No storage: the queue write below is then the only copy.
  }
}

/**
 * Queues a reading, then forgets the pending copy. If the page dies in between, the copy is
 * queued again on the next start: a rare duplicate `read` is cheap, a lost one isn't.
 */
async function queueRead(opening: string, read: PendingRead): Promise<void> {
  await enqueueFeedback(read.article_id, "read", read.value);
  updatePending((pending) => {
    delete pending[opening];
  });
}

/** Moves readings left by a page that unloaded into the queue. Call once at start-up. */
export async function movePendingReads(): Promise<void> {
  for (const [opening, read] of Object.entries(readPending())) {
    const value = Number(read?.value);
    if (typeof read?.article_id !== "string" || !Number.isInteger(value) || value < 0) {
      updatePending((pending) => {
        delete pending[opening];
      });
      continue;
    }
    try {
      await queueRead(opening, { article_id: read.article_id, value });
    } catch (err) {
      // Left in place for the next start.
      console.error("could not queue pending read event", { article_id: read.article_id, err });
    }
  }
}

/**
 * Starts timing a reading of `item`; call the returned function when the reader closes.
 *
 * A reading ends when stopped, when the page is hidden or when it unloads, and sends its whole
 * visible seconds if they reach the floor. Coming back starts a new count, but once an event is
 * sent no other is sent for this opening. A reading under the floor sends nothing and doesn't
 * use up the event.
 */
export function startReading(item: FeedItem): () => void {
  const opening = newEventId();
  let since: number | null = document.visibilityState === "visible" ? Date.now() : null;
  let sent = false;

  const end = () => {
    if (since === null) return;
    const seconds = Math.floor((Date.now() - since) / 1000);
    since = null;
    if (sent || seconds < MIN_READ_SECONDS) return;
    sent = true;
    const read = { article_id: item.id, value: seconds };
    // Synchronously first, so it survives the page unloading before the queue write finishes.
    updatePending((pending) => {
      pending[opening] = read;
    });
    queueRead(opening, read).catch((err: unknown) =>
      console.error("could not queue read event", { article_id: item.id, err }),
    );
  };

  const onVisibility = () => {
    if (document.visibilityState === "visible") {
      since ??= Date.now();
    } else {
      end();
    }
  };

  document.addEventListener("visibilitychange", onVisibility);
  window.addEventListener("pagehide", end);
  let stopped = false;
  return () => {
    if (stopped) return;
    stopped = true;
    document.removeEventListener("visibilitychange", onVisibility);
    window.removeEventListener("pagehide", end);
    end();
  };
}
