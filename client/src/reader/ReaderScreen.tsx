import { useEffect, useRef, useState } from "preact/hooks";
import { fetchArticle } from "../api/articles";
import { fetchFeed } from "../api/feed";
import type { FeedItem } from "../feed/types";
import { cleanArticleHtml } from "./sanitize";
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
  | { state: "failed" };

/** Goes back in history when the list is the previous page, so its scroll position returns. */
function goBack(event: MouseEvent) {
  const fromApp = document.referrer.startsWith(location.origin) && history.length > 1;
  if (!fromApp) return;
  event.preventDefault();
  history.back();
}

/** The reading screen for /article/<id>. Third-party HTML is shown only after cleanArticleHtml. */
export function ReaderScreen() {
  const id = decodeURIComponent(location.pathname.replace(/^\/article\//, "").replace(/\/$/, ""));
  const [item, setItem] = useState<FeedItem | null>(null);
  const [body, setBody] = useState<Body>({ state: "loading" });
  const [label, setLabel] = useState(() => themeLabel(currentTheme()));
  const bodyRef = useRef<HTMLDivElement>(null);

  // Opened directly (shared feed state arrives with #183): find the item's details in the feed.
  useEffect(() => {
    fetchFeed()
      .then((result) => {
        if (result !== "not-modified") setItem(result.feed.items.find((i) => i.id === id) ?? null);
      })
      .catch(() => {});
  }, [id]);

  // The only place the article text is loaded; #216 adds the offline read here.
  useEffect(() => {
    let cancelled = false;
    fetchArticle(id)
      .then((html) => {
        if (cancelled) return;
        setBody(
          html === null ? { state: "expired" } : { state: "html", html: cleanArticleHtml(html) },
        );
      })
      .catch(() => {
        if (!cancelled) setBody({ state: "failed" });
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

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
      {body.state === "failed" ? <p role="status">Could not load the article. Try again.</p> : null}
    </main>
  );
}
