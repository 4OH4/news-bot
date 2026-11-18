/**
 * Feedback POSTs as the app delivers them: at least once, so the same event (same `id`) can reach
 * the server twice. A tap navigates away (every screen is a full page load) while the queue's POST
 * is in flight; the stub has recorded the request, the page never sees the answer, the row stays
 * queued, and the next page's start sends it again. The server dedupes on `id`.
 *
 * Specs count events through this, so a redelivery of one event isn't an extra "open", while a
 * second event with its own `id` (a real double send by the app) still is.
 */
export function distinctEvents(posted: Record<string, unknown>[]): Record<string, unknown>[] {
  const seen = new Set<unknown>();
  return posted.filter((e) => {
    if (typeof e.id !== "string") return true; // no id to dedupe on: count it
    if (seen.has(e.id)) return false;
    seen.add(e.id);
    return true;
  });
}
