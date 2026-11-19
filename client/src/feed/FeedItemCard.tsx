import { useEffect, useRef, useState } from "preact/hooks";
import {
  dismissStory,
  isHidden,
  muteSource,
  subscribeHidden,
  undoDismiss,
  unmuteSource,
} from "../feedback/hidden";
import { cardContext, writeReaderContext } from "../feedback/context";
import type { FeedbackContext } from "../feedback/event";
import { enqueueFeedback } from "../feedback/queue";
import { isSeen, markSeen } from "../feedback/seen";
import "./card-actions.css";
import { explainBreakdown } from "./explain";
import { relativeTime } from "./time";
import { storyKey, type FeedItem, type ViewName } from "./types";

const STATUS_LABELS: Record<string, string> = {
  ok: "",
  partial: "Partly extracted",
  paywalled: "Paywalled",
  failed: "Could not extract",
};

/** Known statuses get friendly wording; unknown ones show as they came. */
function statusLabel(status: string): string {
  return status in STATUS_LABELS ? STATUS_LABELS[status]! : status;
}

/** "pending" until the store answers; "dismissed" and "muted" are the Undo row after an action. */
type View = "pending" | "shown" | "gone" | "dismissed" | "muted";

/** Records the open; failures are logged so the tap still navigates. */
async function recordOpen(item: FeedItem, context: FeedbackContext): Promise<void> {
  try {
    await enqueueFeedback(item.id, "open", undefined, context);
    await markSeen(storyKey(item));
  } catch (err) {
    console.error("could not record the open", { article_id: item.id, err });
  }
}

/** The score components in words, with which side placed the story when it is known (#328). */
function WhyList({ item }: { item: FeedItem }) {
  const rows = explainBreakdown(item.score_breakdown ?? {}, item.score, item.coverage);
  return (
    <div class="why-list">
      <ul>
        {rows.map((row) => (
          <li key={row.label} class={`why-${row.kind}`}>
            <span>{row.label}</span> <span class="why-value">{row.value}</span>
          </li>
        ))}
      </ul>
      {item.arm ? (
        <p class="why-arm">
          Placed by: {item.arm === "personal" ? "personalised" : "rules"}
        </p>
      ) : null}
    </div>
  );
}

/** One story in the list.`article` items open the reader; `link` items open the site. */
export function FeedItemCard({ item, view: screen }: { item: FeedItem; view?: ViewName }) {
  const context = cardContext(screen, item);
  const [imageFailed, setImageFailed] = useState(false);
  const [view, setView] = useState<View>("pending");
  const [seen, setSeen] = useState(false);
  const [why, setWhy] = useState(false);
  const checks = useRef(0);

  useEffect(() => {
    const check = () => {
      const n = ++checks.current;
      isHidden(item)
        .catch(() => false)
        .then((hidden) => {
          if (n !== checks.current) return;
          // This card's own Undo row stays until the screen next loads.
          setView((v) => (!hidden ? "shown" : v === "dismissed" || v === "muted" ? v : "gone"));
        });
    };
    check();
    isSeen(storyKey(item))
      .then((s) => setSeen((prev) => prev || s))
      .catch(() => {});
    return subscribeHidden(check);
  }, [item]);

  const isLink = item.display === "link";

  const onOpen = (e: MouseEvent) => {
    setSeen(true);
    // A full page load follows, so the reader's copy is written inside the click.
    if (!isLink) writeReaderContext(item.id, context);
    // A new tab must open inside the tap, and this page stays alive to finish the write.
    if (isLink || e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) {
      void recordOpen(item, context);
      return;
    }
    // The reader is a full page load, so the event is stored before leaving.
    e.preventDefault();
    const href = (e.currentTarget as HTMLAnchorElement).href;
    void recordOpen(item, context).then(() => location.assign(href));
  };

  const act = (kind: "dismissed" | "muted") => {
    setView(kind);
    // Inside the tap, as a full page load may follow at once; hidden.ts keeps the writes in order.
    const done = kind === "dismissed" ? dismissStory(item, context) : muteSource(item, context);
    done.catch((err: unknown) =>
      console.error(`could not record ${kind}`, { article_id: item.id, err }),
    );
  };

  const undo = () => {
    const kind = view;
    const done = kind === "muted" ? unmuteSource(item, context) : undoDismiss(item, context);
    setView("shown");
    done.catch((err: unknown) => console.error("could not undo", { article_id: item.id, err }));
  };

  if (view === "pending" || view === "gone") return null;
  if (view === "dismissed" || view === "muted") {
    return (
      <p class="card-undo" role="status">
        {view === "muted" ? `Hidden: muted ${item.domain}.` : "Hidden."}{" "}
        <button type="button" onClick={undo}>
          Undo
        </button>
      </p>
    );
  }

  const time = relativeTime(item.published_at);
  const status = statusLabel(item.extraction_status);
  const coverage = item.coverage ?? 0;
  return (
    <article class={seen ? "card card-seen" : "card"} data-testid="feed-item">
      {item.lead_image ? (
        // The headline stays the story's one accessible link; this one is a bigger tap target only.
        <a
          class="card-image-link"
          href={isLink ? item.url : `/article/${item.id}`}
          target={isLink ? "_blank" : undefined}
          rel={isLink ? "noopener noreferrer" : undefined}
          tabindex={-1}
          aria-hidden="true"
          hidden={imageFailed}
          onClick={onOpen}
        >
          <img
            class="card-image"
            src={item.lead_image}
            alt=""
            loading="lazy"
            referrerpolicy="no-referrer"
            onError={() => setImageFailed(true)}
          />
        </a>
      ) : null}
      <h2 class="card-title">
        {isLink ? (
          <a href={item.url} target="_blank" rel="noopener noreferrer" onClick={onOpen}>
            {item.title}
          </a>
        ) : (
          <a href={`/article/${item.id}`} onClick={onOpen}>
            {item.title}
          </a>
        )}
      </h2>
      <p class="card-meta">
        <a href={item.url} target="_blank" rel="noopener noreferrer">
          {item.domain}
        </a>
        {time ? <span> · {time}</span> : null}
        {coverage >= 2 ? <span> · {coverage} sources</span> : null}
      </p>
      {item.summary ? <p class="card-summary">{item.summary}</p> : null}
      {isLink || status ? (
        <p class="card-flags">
          {isLink ? <span>Opens the site</span> : null}
          {status ? <span>{status}</span> : null}
        </p>
      ) : null}
      <details class="card-menu">
        <summary aria-label={`Actions for ${item.title}`}>⋯</summary>
        <div class="card-menu-items">
          <button type="button" onClick={() => act("dismissed")}>
            Dismiss
          </button>
          <button type="button" onClick={() => act("muted")}>
            Mute {item.domain}
          </button>
          <button type="button" aria-expanded={why} onClick={() => setWhy((w) => !w)}>
            Why this story?
          </button>
        </div>
        {why ? <WhyList item={item} /> : null}
      </details>
    </article>
  );
}
