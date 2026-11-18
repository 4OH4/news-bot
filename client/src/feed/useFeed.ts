import { useEffect, useRef, useState } from "preact/hooks";
import { FeedNotReadyError, fetchView, UnsupportedFeedError } from "../api/feed";
import { fetchArticle } from "../api/articles";
import { OfflineError } from "../api/client";
import { loadFeed, pruneArticles, saveFeed } from "../store/db";
import { prefetchArticles } from "../store/prefetch";
import type { Feed, ViewName } from "./types";

export interface UseFeed {
  /** The list shown: page 1 of the view, plus any pages added by Load more. */
  feed: Feed | null;
  /** When the shown feed was last fetched from the API (ms since epoch), or null. */
  fetchedAt: number | null;
  offline: boolean;
  /** A short message about a failed refresh, or "". */
  notice: string;
  busy: boolean;
  /** True while Load more is fetching the next page. */
  loadingMore: boolean;
  refresh: () => Promise<void>;
  loadMore: () => Promise<void>;
}

function noticeFor(err: unknown, hasFeed: boolean): string {
  if (err instanceof UnsupportedFeedError) {
    return "An update is needed to read the newest feed. Reload the app to get it.";
  }
  if (err instanceof FeedNotReadyError) {
    return "This list is not ready yet. Try again in a few minutes.";
  }
  return hasFeed ? "Could not refresh. Showing the last feed." : "Could not load the feed. Try again.";
}

/** `page`'s items appended to `shown`, without ids already shown. */
export function appendPage(shown: Feed, page: Feed): Feed {
  const ids = new Set(shown.items.map((i) => i.id));
  return {
    ...shown,
    items: [...shown.items, ...page.items.filter((i) => !ids.has(i.id))],
    next_cursor: page.next_cursor ?? null,
  };
}

/**
 * One view's list (ADR-0012): the stored copy at once, then a conditional refresh of page 1
 * behind it. A failed refresh never hides the list or touches the store; stored data needs
 * neither the network nor a session. A compare-ranking list (#328) is never stored or pruned
 * against, as it would replace the normal Feed's copy; it is online-only.
 */
export function useFeed(view: ViewName = "feed", ranking?: "rules" | "personal"): UseFeed {
  const [feed, setFeed] = useState<Feed | null>(null);
  const [fetchedAt, setFetchedAt] = useState<number | null>(null);
  const [offline, setOffline] = useState(false);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  // Page 1's etag. Cleared when a later page came from another run, so the next refresh
  // fetches page 1 in full instead of keeping a list mixed from two runs.
  const etag = useRef<string | undefined>(undefined);
  const shown = useRef<Feed | null>(null);

  function show(next: Feed): void {
    shown.current = next;
    setFeed(next);
  }

  /** Stores the shown list; storage is a convenience, so a failure is ignored. */
  async function store(next: Feed): Promise<void> {
    if (ranking) return;
    try {
      await saveFeed(next, etag.current ?? "", view);
    } catch {
      // The list on screen is still good.
    }
  }

  function fail(err: unknown): void {
    if (err instanceof OfflineError) {
      setOffline(true);
      setNotice("");
    } else {
      // Auth failures are already shown by the session banner; keep the list either way.
      setOffline(false);
      setNotice(noticeFor(err, shown.current !== null));
    }
  }

  async function refresh(): Promise<void> {
    setBusy(true);
    try {
      const result = await fetchView(view, { etag: etag.current, ranking });
      if (result !== "not-modified") {
        const next = result.feed;
        etag.current = result.etag || undefined;
        show(next);
        setFetchedAt(Date.now());
        await store(next);
        // A compare list isn't stored, so pruning against it would drop the Feed's articles.
        if (!ranking) {
          try {
            await pruneArticles(next);
            // In the background, so the top stories open with no signal (#216).
            void prefetchArticles(next, fetchArticle);
          } catch {
            // As above.
          }
        }
      }
      setOffline(false);
      setNotice("");
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  async function loadMore(): Promise<void> {
    const current = shown.current;
    if (!current?.next_cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const result = await fetchView(view, { cursor: current.next_cursor, ranking });
      if (result === "not-modified") return;
      if (result.feed.run_id !== current.run_id) etag.current = undefined;
      const next = appendPage(current, result.feed);
      show(next);
      // Stored too, so an article from a later page opens with its details and Back keeps
      // the place; the next changed page 1 replaces it.
      await store(next);
      setOffline(false);
      setNotice("");
    } catch (err) {
      fail(err);
    } finally {
      setLoadingMore(false);
    }
  }

  useEffect(() => {
    void (async () => {
      try {
        const stored = ranking ? null : await loadFeed(view);
        if (stored) {
          etag.current = stored.etag || undefined;
          show(stored.feed);
          setFetchedAt(stored.fetchedAt);
        }
      } catch {
        // No usable store: fall through to the network.
      }
      await refresh();
    })();
  }, [view, ranking]);

  return { feed, fetchedAt, offline, notice, busy, loadingMore, refresh, loadMore };
}
