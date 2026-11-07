import { useEffect, useRef, useState } from "preact/hooks";
import { FeedNotReadyError, fetchFeed, UnsupportedFeedError } from "../api/feed";
import { FeedItemCard } from "./FeedItemCard";
import type { Feed } from "./types";

function noticeFor(err: unknown, hasFeed: boolean): string {
  if (err instanceof UnsupportedFeedError) {
    return "An update is needed to read the newest feed. Reload the app to get it.";
  }
  if (err instanceof FeedNotReadyError) {
    return "The first feed is not ready yet. Try again in a few minutes.";
  }
  return hasFeed ? "Could not refresh. Showing the last feed." : "Could not load the feed. Try again.";
}

/** The home screen: the ranked feed, kept in component state (storage is #183). */
export function FeedScreen() {
  const [feed, setFeed] = useState<Feed | null>(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const etag = useRef<string | undefined>(undefined);
  const feedRef = useRef<Feed | null>(null);

  async function load() {
    setBusy(true);
    try {
      const result = await fetchFeed(etag.current);
      if (result !== "not-modified") {
        feedRef.current = result.feed;
        etag.current = result.etag || undefined;
        setFeed(result.feed);
      }
      setNotice("");
    } catch (err) {
      // Auth failures are already shown by the session banner; keep the last feed either way.
      setNotice(noticeFor(err, feedRef.current !== null));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  return (
    <main>
      <header class="top">
        <h1>Newsfeed</h1>
        <button type="button" onClick={() => void load()} disabled={busy}>
          Refresh
        </button>
      </header>
      {notice ? (
        <p role="status" class="notice">
          {notice}
        </p>
      ) : null}
      {feed ? (
        feed.items.length === 0 ? (
          <p>No stories yet.</p>
        ) : (
          feed.items.map((item) => <FeedItemCard key={item.id} item={item} />)
        )
      ) : null}
    </main>
  );
}
