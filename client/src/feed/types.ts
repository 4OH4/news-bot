/** Types for `feed.json` (docs/feed.schema.json, ADR-0006). Unknown fields are ignored. */

export interface FeedItem {
  id: string;
  story_id?: string;
  title: string;
  url: string;
  source_id: string;
  /** The source label shown as the publisher (ADR-0006). */
  domain: string;
  author: string | null;
  published_at: string | null;
  summary: string | null;
  lead_image: string | null;
  score: number;
  /** Not shown; listed so the type matches the contract. */
  score_breakdown: Record<string, number>;
  /** Known values: ok, partial, paywalled, failed. Newer pipelines may send others. */
  extraction_status: string;
  display: "article" | "link";
  word_count: number | null;
  content_path: string | null;
  /** The A/B side that placed this item in the Feed (#326); absent when not served interleaved. */
  arm?: "rules" | "personal";
  /** How much of the item the personal ranking could score; not shown yet (#328). */
  coverage?: number;
}

/** The views the API serves (ADR-0012); each is one of the app's tabs. */
export type ViewName = "news" | "feed" | "longer";

export interface Feed {
  schema_version: number;
  generated_at: string;
  run_id: string;
  /** Which view this page belongs to (ADR-0012); absent from the old unparameterised route. */
  view?: string;
  /** The cursor for the next page, or null on the last one (ADR-0012). */
  next_cursor?: string | null;
  items: FeedItem[];
}

/** The key that seen, liked and dismissed state hangs on: stable when the shown row changes. */
export function storyKey(item: FeedItem): string {
  return item.story_id ?? item.id;
}
