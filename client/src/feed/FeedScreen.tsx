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
        <div class="top-title">
          <h1>Newsfeed</h1>
          {fetchedAt ? (
            <p class="updated">Updated {relativeTime(new Date(fetchedAt).toISOString())}</p>
          ) : null}
        </div>
        <button
          type="button"
          class="refresh"
          aria-label="Refresh"
          onClick={() => void refresh()}
          disabled={busy}
        >
          <svg
            class={busy ? "refresh-icon spinning" : "refresh-icon"}
            viewBox="0 0 24 24"
            width="24"
            height="24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path d="M20 12a8 8 0 1 1-2.34-5.66" />
            <path d="M20 4v5h-5" />
          </svg>
        </button>
      </header>
      {offline ? (
        <p role="status" class="notice">
          You are offline. Showing the last feed.
        </p>
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
