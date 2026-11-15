import "./feed-status.css";
import { FeedItemCard } from "./FeedItemCard";
import { relativeTime } from "./time";
import type { ViewName } from "./types";
import { useFeed } from "./useFeed";

const TITLES: Record<ViewName, string> = { news: "News", feed: "Feed", longer: "Longer" };

const ICON = {
  viewBox: "0 0 24 24",
  width: "24",
  height: "24",
  fill: "none",
  stroke: "currentColor",
  "stroke-width": "2",
  "stroke-linecap": "round",
  "stroke-linejoin": "round",
  "aria-hidden": "true",
} as const;

/** Round icon links to Liked and Settings, before Refresh in the header row (#289). */
function HeaderLinks() {
  return (
    <>
      <a class="icon-button" href="/liked" aria-label="Liked">
        <svg {...ICON}>
          <path d="M12 20s-7-4.35-7-10a4 4 0 0 1 7-2.65A4 4 0 0 1 19 10c0 5.65-7 10-7 10z" />
        </svg>
      </a>
      <a class="icon-button" href="/settings" aria-label="Settings">
        <svg {...ICON}>
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
      </a>
    </>
  );
}

/** One tab's list: the view shown from the store first, refreshed behind it, paged by Load more. */
export function FeedScreen({ view = "feed" }: { view?: ViewName }) {
  const { feed, fetchedAt, offline, notice, busy, loadingMore, refresh, loadMore } = useFeed(view);

  return (
    <main>
      <header class="top">
        <div class="top-title">
          <h1>{TITLES[view]}</h1>
          {fetchedAt ? (
            <p class="updated">Updated {relativeTime(new Date(fetchedAt).toISOString())}</p>
          ) : null}
        </div>
        <div class="top-actions">
          <HeaderLinks />
          <button
            type="button"
            class="icon-button refresh"
            aria-label="Refresh"
            onClick={() => void refresh()}
            disabled={busy}
          >
            <svg {...ICON} class={busy ? "refresh-icon spinning" : "refresh-icon"}>
              <path d="M20 12a8 8 0 1 1-2.34-5.66" />
              <path d="M20 4v5h-5" />
            </svg>
          </button>
        </div>
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
          <p class="empty">Nothing here yet.</p>
        ) : (
          feed.items.map((item) => <FeedItemCard key={item.id} item={item} />)
        )
      ) : null}
      {feed?.next_cursor ? (
        <div class="load-more">
          <button
            type="button"
            onClick={() => void loadMore()}
            disabled={offline || loadingMore}
            aria-busy={loadingMore}
          >
            Load more
          </button>
          {offline ? <p class="load-more-note">More when you're back online</p> : null}
        </div>
      ) : null}
    </main>
  );
}
