/**
 * Reading time (#216): how long an article was actually on screen, sent as one `read` event per
 * reader opening. Only time while the page is visible counts, so a phone left in a pocket with
 * the reader open doesn't look like a long read.
 */
import type { FeedItem } from "../feed/types";
import { enqueueFeedback } from "./queue";

/** Readings shorter than this send nothing: a glance is not a read. */
export const MIN_READ_SECONDS = 5;

/**
 * Starts timing a reading of `item`; call the returned function when the reader closes.
 *
 * A reading ends when stopped or when the page is hidden, and sends its whole visible seconds if
 * they reach the floor. Coming back starts a new count, but once an event is sent no other is
 * sent for this opening. A reading under the floor sends nothing and doesn't use up the event.
 */
export function startReading(item: FeedItem): () => void {
  let since: number | null = document.visibilityState === "visible" ? Date.now() : null;
  let sent = false;

  const end = () => {
    if (since === null) return;
    const seconds = Math.floor((Date.now() - since) / 1000);
    since = null;
    if (sent || seconds < MIN_READ_SECONDS) return;
    sent = true;
    enqueueFeedback(item.id, "read", seconds).catch((err: unknown) =>
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
  let stopped = false;
  return () => {
    if (stopped) return;
    stopped = true;
    document.removeEventListener("visibilitychange", onVisibility);
    end();
  };
}
