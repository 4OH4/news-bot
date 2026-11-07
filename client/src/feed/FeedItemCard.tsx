import { useState } from "preact/hooks";
import { relativeTime } from "./time";
import type { FeedItem } from "./types";

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

/** One story in the list. `article` items open the reader; `link` items open the site. */
export function FeedItemCard({ item }: { item: FeedItem }) {
  const [imageFailed, setImageFailed] = useState(false);
  const isLink = item.display === "link";
  const time = relativeTime(item.published_at);
  const status = statusLabel(item.extraction_status);
  return (
    <article class="card" data-testid="feed-item">
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
          <a href={item.url} target="_blank" rel="noopener noreferrer">
            {item.title}
          </a>
        ) : (
          <a href={`/article/${item.id}`}>{item.title}</a>
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
    </article>
  );
}
