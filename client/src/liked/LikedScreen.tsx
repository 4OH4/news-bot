import { useEffect, useState } from "preact/hooks";
import type { FeedItem } from "../feed/types";
import { listLiked } from "../feedback/liked";

/**
 * The Liked list at /liked (#216): liked stories from the phone's store, newest like first. They
 * stay after leaving the feed, and each opens the reader, whose text was kept when it was liked.
 */
export function LikedScreen() {
  const [items, setItems] = useState<FeedItem[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    listLiked()
      .then((list) => {
        if (!cancelled) setItems(list);
      })
      .catch((err: unknown) => {
        console.error("could not read the Liked list", err);
        if (!cancelled) setItems([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main>
      <h1 class="liked-title">Liked</h1>
      {items === null ? null : items.length === 0 ? (
        <p>Nothing liked yet. Like a story from the reader to keep it here.</p>
      ) : (
        items.map((item) => (
          <article class="card" key={item.id}>
            <h2 class="card-title">
              <a href={`/article/${encodeURIComponent(item.id)}`}>{item.title}</a>
            </h2>
            <p class="card-meta">{item.domain}</p>
            {item.summary ? <p class="card-summary">{item.summary}</p> : null}
          </article>
        ))
      )}
    </main>
  );
}
