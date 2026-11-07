import "./feed-status.css";
import { FeedItemCard } from "./FeedItemCard";
import { relativeTime } from "./time";
import { useFeed } from "./useFeed";

/** The home screen: the ranked feed, shown from the store first and refreshed behind it. */
export function FeedScreen() {
  const { feed, fetchedAt, offline, notice, busy, refresh } = useFeed();

  return (
    <main>
      <header class="top">
        <h1>Newsfeed</h1>
        <button type="button" onClick={() => void refresh()} disabled={busy}>
          Refresh
        </button>
      </header>
      {offline ? (
        <p role="status" class="notice">
          You are offline. Showing the last feed.
        </p>
      ) : null}
      {fetchedAt ? (
        <p class="updated">Updated {relativeTime(new Date(fetchedAt).toISOString())}</p>
      ) : null}
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
