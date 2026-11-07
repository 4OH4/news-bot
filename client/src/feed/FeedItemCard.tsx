import { useEffect, useRef, useState } from "preact/hooks";
import {
  dismissStory,
  isHidden,
  muteSource,
  subscribeHidden,
  undoDismiss,
  unmuteSource,
} from "../feedback/hidden";
import { enqueueFeedback } from "../feedback/queue";
import { isSeen, markSeen } from "../feedback/seen";
import "./card-actions.css";
import { relativeTime } from "./time";
import { storyKey, type FeedItem } from "./types";

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
async function recordOpen(item: FeedItem): Promise<void> {
  try {
    await enqueueFeedback(item.id, "open");
    await markSeen(storyKey(item));
  } catch (err) {
    console.error("could not record the open", { article_id: item.id, err });
  }
}

/** One story in the list. `article` items open the reader; `link` items open the site. */
export function FeedItemCard({ item }: { item: FeedItem }) {
  const [imageFailed, setImageFailed] = useState(false);
  const [view, setView] = useState<View>("pending");
  const [seen, setSeen] = useState(false);
  const checks = useRef(0);
  // The action an Undo must follow, so its store write can't land before the action's.
  const acting = useRef<Promise<void>>(Promise.resolve());

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
      .then(setSeen)
      .catch(() => {});
    return subscribeHidden(check);
  }, [item]);

  const isLink = item.display === "link";

  const onOpen = (e: MouseEvent) => {
    setSeen(true);
    // A new tab must open inside the tap, and this page stays alive to finish the write.
    if (isLink || e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) {
      void recordOpen(item);
      return;
    }
    // The reader is a full page load, so the event is stored before leaving.
    e.preventDefault();
    const href = (e.currentTarget as HTMLAnchorElement).href;
    void recordOpen(item).then(() => location.assign(href));
  };

  const act = (kind: "dismissed" | "muted") => {
    setView(kind);
    const done = kind === "dismissed" ? dismissStory(item) : muteSource(item);
    acting.current = done.catch(() => {});
    done.catch((err: unknown) =>
      console.error(`could not record ${kind}`, { article_id: item.id, err }),
    );
  };

  const undo = () => {
    const kind = view;
    const done = acting.current.then(() =>
      kind === "muted" ? unmuteSource(item.source_id) : undoDismiss(item),
    );
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
  return (
    <article class={seen ? "card card-seen" : "card"} data-testid="feed-item">
      {item.lead_image ? (
        <img
          class="card-image"
          hidden={imageFailed}
          src={item.lead_image}
          alt=""
          loading="lazy"
          referrerpolicy="no-referrer"
          onError={() => setImageFailed(true)}
        />
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
        </div>
      </details>
    </article>
  );
}
