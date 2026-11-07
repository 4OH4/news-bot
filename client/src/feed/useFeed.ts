import { useEffect, useRef, useState } from "preact/hooks";
import { FeedNotReadyError, fetchFeed, UnsupportedFeedError } from "../api/feed";
import { fetchArticle } from "../api/articles";
import { OfflineError } from "../api/client";
import { loadFeed, pruneArticles, saveFeed } from "../store/db";
import { prefetchArticles } from "../store/prefetch";
import type { Feed } from "./types";

export interface UseFeed {
  feed: Feed | null;
  /** When the shown feed was last fetched from the API (ms since epoch), or null. */
  fetchedAt: number | null;
  offline: boolean;
  /** A short message about a failed refresh, or "". */
  notice: string;
  busy: boolean;
  refresh: () => Promise<void>;
}

function noticeFor(err: unknown, hasFeed: boolean): string {
  if (err instanceof UnsupportedFeedError) {
    return "An update is needed to read the newest feed. Reload the app to get it.";
  }
  if (err instanceof FeedNotReadyError) {
    return "The first feed is not ready yet. Try again in a few minutes.";
  }
  return hasFeed ? "Could not refresh. Showing the last feed." : "Could not load the feed. Try again.";
}

/**
 * The stored feed at once, then a conditional refresh behind it. A failed refresh never hides
 * the feed or touches the store; stored data needs neither the network nor a session.
 */
export function useFeed(): UseFeed {
  const [feed, setFeed] = useState<Feed | null>(null);
  const [fetchedAt, setFetchedAt] = useState<number | null>(null);
  const [offline, setOffline] = useState(false);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const etag = useRef<string | undefined>(undefined);
  const hasFeed = useRef(false);

  async function refresh(): Promise<void> {
    setBusy(true);
    try {
      const result = await fetchFeed(etag.current);
      if (result !== "not-modified") {
        const next = result.feed;
        etag.current = result.etag || undefined;
        hasFeed.current = true;
        setFeed(next);
        setFetchedAt(Date.now());
        try {
          await saveFeed(next, result.etag);
          await pruneArticles(next);
          // In the background, so the top stories open with no signal (#216).
          void prefetchArticles(next, fetchArticle);
        } catch {
          // Storage is a convenience; the feed on screen is still good.
        }
      }
      setOffline(false);
      setNotice("");
    } catch (err) {
      if (err instanceof OfflineError) {
        setOffline(true);
        setNotice("");
      } else {
        // Auth failures are already shown by the session banner; keep the feed either way.
        setOffline(false);
        setNotice(noticeFor(err, hasFeed.current));
      }
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void (async () => {
      try {
        const stored = await loadFeed();
        if (stored) {
          etag.current = stored.etag || undefined;
          hasFeed.current = true;
          setFeed(stored.feed);
          setFetchedAt(stored.fetchedAt);
        }
      } catch {
        // No usable store: fall through to the network.
      }
      await refresh();
    })();
  }, []);

  return { feed, fetchedAt, offline, notice, busy, refresh };
}
