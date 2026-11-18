import { useRef } from "preact/hooks";
import "./feed-status.css";
import { FeedItemCard } from "./FeedItemCard";
import { relativeTime } from "./time";
import type { ViewName } from "./types";
import { useFeed } from "./useFeed";
import { scrollToTop, useScrollChrome } from "./useScrollChrome";

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

/** Round Settings icon link, before Refresh in the header row (#289; Liked moved to Settings in #307). */
function HeaderLinks() {
  return (
    <>
      <a class="icon-button" href="/settings" aria-label="Settings">
        <svg {...ICON}>
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
      </a>
    </>
  );
}

const COMPARE_LABELS = { rules: "Rules only", personal: "Personalised only" } as const;

/** The `?ranking=` of the address, when it is one the API knows (#328). */
function compareRanking(): "rules" | "personal" | undefined {
  const value = new URLSearchParams(location.search).get("ranking");
  return value === "rules" || value === "personal" ? value : undefined;
}

/** One tab's list:the view shown from the store first, refreshed behind it, paged by Load more. */
export function FeedScreen({ view = "feed" }: { view?: ViewName }) {
  const ranking = compareRanking();
  const { feed, fetchedAt, offline, notice, busy, loadingMore, refresh, loadMore } = useFeed(
    view,
    ranking,
  );

  const headerRef = useRef<HTMLElement>(null);
  const { offset, showButton, snapping } = useScrollChrome(headerRef);

  return (
    <main>
      <header
        class={snapping ? "top snapping" : "top"}
        ref={headerRef}
        style={offset > 0 ? { transform: `translateY(-${offset}px)` } : undefined}
      >
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
      {/* Fixed, so its place in the DOM doesn't matter; before the cards keeps `.card:last-child` true. */}
      <button
        type="button"
        class={showButton ? "back-to-top shown" : "back-to-top"}
        aria-label="Back to top"
        aria-hidden={showButton ? undefined : "true"}
        tabIndex={showButton ? 0 : -1}
        onClick={scrollToTop}
      >
        <svg {...ICON}>
          <path d="M12 19V5" />
          <path d="M5 12l7-7 7 7" />
        </svg>
      </button>
      {ranking ? (
        <p class="compare-label">
          <span>{COMPARE_LABELS[ranking]}</span> · <a href="/">Back to the normal Feed</a>
        </p>
      ) : null}
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
          feed.items.map((item) => <FeedItemCard key={item.id} item={item} view={view} />)
        )
      ) : null}
      {feed?.next_cursor ? (
        <div class="load-more">
          <button
            type="button"
            onClick={() => void loadMore()}
            disabled={offline || loadingMore || busy}
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
