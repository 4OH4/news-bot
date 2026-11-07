import { useEffect, useRef, useState } from "preact/hooks";
import { fetchArticle } from "../api/articles";
import { OfflineError } from "../api/client";
import { fetchFeed } from "../api/feed";
import type { FeedItem } from "../feed/types";
import { findLiked, isLiked, setLiked } from "../feedback/liked";
import { startReading } from "../feedback/reading";
import { getArticleHtml, loadFeed, putArticleHtml } from "../store/db";
import { cleanArticleHtml } from "./sanitize";
import { openedFromList } from "./scroll";
import { currentTheme, toggleTheme, type Theme } from "./theme";

const WORDS_PER_MINUTE = 200;

/** Whole minutes to read, at least one; null when the word count is unknown. */
export function readingMinutes(wordCount: number | null): number | null {
  if (!wordCount || wordCount <= 0) return null;
  return Math.max(1, Math.round(wordCount / WORDS_PER_MINUTE));
}

function formatDate(iso: string | null): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  return Number.isNaN(t) ? "" : new Date(t).toLocaleDateString(undefined, { dateStyle: "medium" });
}

function themeLabel(theme: Theme): string {
  return theme === "dark" ? "Switch to light theme" : "Switch to dark theme";
}

type Body =
  | { state: "loading" }
  | { state: "html"; html: string }
  | { state: "expired" }
  | { state: "offline" }
  | { state: "failed" };

/** Goes back in history when the list is the previous page, so its scroll position returns. */
function goBack(event: MouseEvent) {
  if (!openedFromList()) return;
  event.preventDefault();
  history.back();
}

/** Finds the item for `id`: the stored feed, then the Liked list, and only then the network. */
async function findItem(id: string): Promise<FeedItem | null> {
  try {
    const stored = (await loadFeed())?.feed.items.find((i) => i.id === id);
    if (stored) return stored;
  } catch {
    // No usable store: try the others.
  }
  try {
    // A liked article may have left the feed.
    const liked = await findLiked(id);
    if (liked) return liked;
  } catch {
    // As above.
  }
  try {
    const result = await fetchFeed();
    if (result !== "not-modified") return result.feed.items.find((i) => i.id === id) ?? null;
  } catch {
    // Offline or refused: the reader shows what it has.
  }
  return null;
}

type Loaded = { state: "html"; html: string } | { state: "expired" | "offline" | "failed" };

/**
 * The article's raw HTML: the stored copy when there is one, otherwise fetched and stored. A
 * fetched copy is stored kept if the article was liked meanwhile, so pruning can't drop it.
 */
async function loadText(id: string, keep: () => boolean): Promise<Loaded> {
  try {
    const stored = await getArticleHtml(id);
    if (stored !== null) return { state: "html", html: stored };
  } catch {
    // No usable store: fall through to the network.
  }
  let html: string | null;
  try {
    html = await fetchArticle(id);
  } catch (err) {
    return { state: err instanceof OfflineError ? "offline" : "failed" };
  }
  if (html === null) return { state: "expired" };
  try {
    await putArticleHtml(id, html, { keep: keep() });
  } catch {
    // Shown anyway; it just won't open offline.
  }
  return { state: "html", html };
}

/** The reading screen for /article/<id>. Third-party HTML is shown only after cleanArticleHtml. */
export function ReaderScreen() {
  const id = decodeURIComponent(location.pathname.replace(/^\/article\//, "").replace(/\/$/, ""));
  const [item, setItem] = useState<FeedItem | null>(null);
  const [body, setBody] = useState<Body>({ state: "loading" });
  const [label, setLabel] = useState(() => themeLabel(currentTheme()));
  // null until the stored like state is known, so the button never shows a wrong state.
  const [liked, setLikedState] = useState<boolean | null>(null);
  const likedRef = useRef(false);
  const rawHtml = useRef<string | undefined>(undefined);
  const likeChain = useRef<Promise<void>>(Promise.resolve());
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    void findItem(id).then((found) => {
      if (!cancelled) setItem(found);
    });
    return () => {
      cancelled = true;
    };
  }, [id]);

  // The only place the article text is loaded: stored text first, the network for the rest.
  useEffect(() => {
    let cancelled = false;
    void loadText(id, () => likedRef.current).then((loaded) => {
      if (cancelled) return;
      if (loaded.state === "html") {
        rawHtml.current = loaded.html;
        setBody({ state: "html", html: cleanArticleHtml(loaded.html) });
      } else {
        setBody(loaded);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [id]);

  // Like state and reading time both need the item, so they start once it is known.
  useEffect(() => {
    if (!item) return;
    let cancelled = false;
    isLiked(item)
      .then((value) => {
        if (cancelled) return;
        likedRef.current = value;
        setLikedState(value);
      })
      .catch(() => {
        if (!cancelled) setLikedState(false);
      });
    const stop = startReading(item);
    return () => {
      cancelled = true;
      stop();
    };
  }, [item]);

  function toggleLike() {
    if (!item || liked === null) return;
    const next = !liked;
    likedRef.current = next;
    setLikedState(next);
    // One at a time and in order, so a quick like then unlike are queued in the order pressed.
    likeChain.current = likeChain.current
      .then(() => setLiked(item, next, rawHtml.current))
      .catch((err: unknown) => console.error("could not save like", { article_id: item.id, err }));
  }

  // Broken images hide. `error` does not bubble, so listen in the capture phase.
  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    const onError = (e: Event) => {
      if (e.target instanceof HTMLImageElement) e.target.hidden = true;
    };
    el.addEventListener("error", onError, true);
    return () => el.removeEventListener("error", onError, true);
  }, [body.state]);

  const minutes = readingMinutes(item?.word_count ?? null);
  const date = formatDate(item?.published_at ?? null);

  return (
    <main class="reader">
      <nav class="reader-bar">
        <a href="/" onClick={goBack}>
          Back to the feed
        </a>
        <button type="button" onClick={() => setLabel(themeLabel(toggleTheme()))}>
          {label}
        </button>
        {item && liked !== null ? (
          <button type="button" class="reader-like" aria-pressed={liked} onClick={toggleLike}>
            {liked ? "Liked" : "Like"}
          </button>
        ) : null}
      </nav>
      <header>
        <h1 class="reader-title">{item?.title ?? "Article"}</h1>
        {item ? (
          <>
            <p class="reader-meta">
              {[item.domain, item.author, date, minutes ? `${minutes} min read` : ""]
                .filter(Boolean)
                .join(" · ")}
            </p>
            <p>
              <a href={item.url} target="_blank" rel="noopener noreferrer">
                Original article
              </a>
            </p>
          </>
        ) : null}
      </header>
      {body.state === "html" ? (
        <div ref={bodyRef} class="reader-body" dangerouslySetInnerHTML={{ __html: body.html }} />
      ) : null}
      {body.state === "expired" ? (
        <section class="reader-body">
          {item?.summary ? <p>{item.summary}</p> : null}
          <p>The full text is no longer kept here. Read it on the original site.</p>
        </section>
      ) : null}
      {body.state === "offline" ? (
        <section class="reader-body">
          {item?.summary ? <p>{item.summary}</p> : null}
          <p role="status">Not available offline. Open it again when you are back online.</p>
        </section>
      ) : null}
      {body.state === "failed" ? <p role="status">Could not load the article. Try again.</p> : null}
    </main>
  );
}
