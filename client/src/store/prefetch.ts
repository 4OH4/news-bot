import type { Feed } from "../feed/types";
import { getArticleHtml, putArticleHtml } from "./db";

export const PREFETCH_LIMIT = 50;
const CONCURRENCY = 4;

/**
 * Stores the text of the first `limit` article items that are not stored yet, 4 requests at a
 * time. Skipped on Save-Data, and stops quietly on the first error: this must never reach the UI.
 */
export async function prefetchArticles(
  feed: Feed,
  fetchHtml: (id: string) => Promise<string | null>,
  limit = PREFETCH_LIMIT,
): Promise<void> {
  const connection = (navigator as { connection?: { saveData?: boolean } }).connection;
  if (connection?.saveData) return;
  try {
    const ids = feed.items
      .filter((item) => item.display === "article")
      .slice(0, limit)
      .map((item) => item.id);
    const queue: string[] = [];
    for (const id of ids) {
      if ((await getArticleHtml(id)) === null) queue.push(id);
    }
    let next = 0;
    let stopped = false;
    const worker = async () => {
      while (!stopped && next < queue.length) {
        const id = queue[next++]!;
        try {
          const html = await fetchHtml(id);
          if (html !== null) await putArticleHtml(id, html);
        } catch {
          stopped = true;
        }
      }
    };
    await Promise.all(Array.from({ length: CONCURRENCY }, worker));
  } catch {
    // Storage trouble also ends the prefetch quietly.
  }
}
