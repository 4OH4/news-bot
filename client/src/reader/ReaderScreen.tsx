import { useEffect, useRef, useState } from "preact/hooks";
import { fetchArticle } from "../api/articles";
import { OfflineError } from "../api/client";
import { fetchView } from "../api/feed";
import { MetaLine } from "../feed/MetaLine";
import type { FeedItem } from "../feed/types";
import { readReaderContext } from "../feedback/context";
import { findLiked, isLiked, setLiked } from "../feedback/liked";
import { startReading } from "../feedback/reading";
import { getArticleHtml, loadAllFeeds, putArticleHtml } from "../store/db";
import { cleanArticleHtml } from "./sanitize";

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

type Body =
  | { state: "loading" }
  | { state: "html"; html: string }
  | { state: "expired" }
  | { state: "offline" }
  | { state: "failed" };

/** Finds the item for `id`: the stored views, then the Liked list, and only then the network. */
async function findItem(id: string): Promise<FeedItem | null> {
  try {
    const stored = (await loadAllFeeds()).flatMap((f) => f.items).find((i) => i.id === id);
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
    const result = await fetchView("feed", {});
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
  // null until the stored like state is known, so the button never shows a wrong state.
  const [liked, setLikedState] = useState<boolean | null>(null);
  // Read once on load; only the card that opened this article may speak for it.
  const context = useRef(readReaderContext(id));
  const likedRef = useRef(false);
  const rawHtml = useRef<string | undefined>(undefined);
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
    const stop = startReading(item, context.current);
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
    // Inside the tap, as leaving may follow at once; liked.ts keeps quick taps in order.
    setLiked(item, next, rawHtml.current, context.current).catch((err: unknown) =>
      console.error("could not save like", { article_id: item.id, err }),
    );
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
      <header>
        <h1 class="reader-title">{item?.title ?? "Article"}</h1>
        {item ? (
          <div class="reader-byline">
            <div class="reader-byline-text">
              <MetaLine
                className="reader-meta"
                items={[
                  { key: "domain", content: item.domain },
                  ...(item.author ? [{ key: "author", content: item.author, wrap: true }] : []),
                  ...(date ? [{ key: "date", content: date }] : []),
                  ...(minutes ? [{ key: "read", content: `${minutes} min read` }] : []),
                ].filter((i) => i.content)}
              />
              <p>
                <a href={item.url} target="_blank" rel="noopener noreferrer">
                  Original article
                </a>
              </p>
            </div>
            {liked !== null ? (
              <button
                type="button"
                class="reader-like"
                aria-label="Like"
                aria-pressed={liked}
                onClick={toggleLike}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                  <path d="M12 21s-7.5-4.6-9.6-9.2C.9 8.4 3 4.5 6.7 4.5c2 0 3.7 1.1 5.3 3 1.6-1.9 3.3-3 5.3-3 3.7 0 5.8 3.9 4.3 7.3C19.5 16.4 12 21 12 21z" />
                </svg>
              </button>
            ) : null}
          </div>
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
