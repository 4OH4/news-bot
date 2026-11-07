/**
 * Feedback events as `POST /api/feedback` takes them (#176). `docs/feedback.example.json` holds
 * one of each shape, and both the client and the server tests read it.
 */
export type FeedbackEventName = "open" | "read" | "like" | "dismiss" | "mute_source";

export type FeedbackPayload = {
  id: string;
  article_id: string;
  event: FeedbackEventName;
  value: number | null;
  created_at: string;
};

/**
 * A new event id: 32 lower-case hex characters. The id is made once on the phone and resent
 * unchanged on every retry, so the server can drop a duplicate whose first answer was lost.
 */
export function newEventId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Builds one event; `value` is null when not given (0 stays 0). */
export function buildEvent(
  articleId: string,
  event: FeedbackEventName,
  value?: number,
  now: Date = new Date(),
): FeedbackPayload {
  return {
    id: newEventId(),
    article_id: articleId,
    event,
    value: value ?? null,
    created_at: now.toISOString(),
  };
}

/** The payload fields alone, so a stored row's extra fields (such as `seq`) are never posted. */
export function toPayload(row: FeedbackPayload): FeedbackPayload {
  return {
    id: row.id,
    article_id: row.article_id,
    event: row.event,
    value: row.value,
    created_at: row.created_at,
  };
}
