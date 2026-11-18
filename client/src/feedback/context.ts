/**
 * The view and A/B arm that ride along with feedback events (#327). Cards build a context from
 * their screen; a tap on an article title hands it to the reader through `sessionStorage`, because
 * the reader is a full page load.
 */
import type { FeedItem } from "../feed/types";
import type { FeedbackContext } from "./event";

const READER_CONTEXT_KEY = "reader-context";

/**
 * The context for events from a card. A screen opened with `?ranking=` wasn't served
 * interleaved, so its items' arm doesn't say who placed them: only the view is sent.
 */
export function cardContext(view: string | undefined, item: FeedItem): FeedbackContext {
  if (view === undefined) return {};
  const compare = new URLSearchParams(location.search).has("ranking");
  return item.arm && !compare ? { view, arm: item.arm } : { view };
}

/** Synchronous, so it lands before the page unloads. Storage failures lose only the context. */
export function writeReaderContext(id: string, context: FeedbackContext): void {
  if (context.view === undefined) return;
  try {
    sessionStorage.setItem(READER_CONTEXT_KEY, JSON.stringify({ id, ...context }));
  } catch {
    // Private mode or full storage: the reader just sends neither field.
  }
}

/** The stored context if it was written for article `id`, consumed on read; otherwise nothing. */
export function readReaderContext(id: string): FeedbackContext | undefined {
  try {
    const raw = sessionStorage.getItem(READER_CONTEXT_KEY);
    if (!raw) return undefined;
    // Read once, like reader/scroll.ts: a later open of the same article (Liked, a shared link)
    // wasn't opened from a card and must not reuse this view and arm.
    sessionStorage.removeItem(READER_CONTEXT_KEY);
    const parsed = JSON.parse(raw) as { id?: unknown; view?: unknown; arm?: unknown };
    if (parsed.id !== id || typeof parsed.view !== "string") return undefined;
    const arm = parsed.arm === "rules" || parsed.arm === "personal" ? parsed.arm : undefined;
    return arm ? { view: parsed.view, arm } : { view: parsed.view };
  } catch {
    return undefined;
  }
}
