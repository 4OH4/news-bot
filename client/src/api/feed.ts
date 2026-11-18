import type { Feed, ViewName } from "../feed/types";
import { apiFetch } from "./client";

export const SUPPORTED_SCHEMA_VERSION = 1;

/** The feed's `schema_version` is not one this client understands (ADR-0006). */
export class UnsupportedFeedError extends Error {
  constructor(readonly version: unknown) {
    super(`Unsupported feed schema version: ${String(version)}`);
    this.name = "UnsupportedFeedError";
  }
}

/** The API has no feed yet (503), or this view is not written yet (404). */
export class FeedNotReadyError extends Error {
  constructor() {
    super("The first feed is not ready");
    this.name = "FeedNotReadyError";
  }
}

/** Any other unexpected answer from the API. */
export class FeedHttpError extends Error {
  constructor(readonly status: number) {
    super(`Feed request failed with status ${status}`);
    this.name = "FeedHttpError";
  }
}

export interface ViewRequest {
  /** The last etag of this view's first page, for a conditional GET. */
  etag?: string;
  /** The `next_cursor` of the previous page; omitted for page 1. */
  cursor?: string;
  limit?: number;
  /** Compare-ranking pages (#328): serve the rules-only or personalised-only order. */
  ranking?: "rules" | "personal";
}

/**
 * Fetches one page of a view. A 304 gives "not-modified". A 404 means the view's file is not
 * written yet (the hour after a deploy), so it counts as "not ready" like a 503.
 */
export async function fetchView(
  view: ViewName,
  { etag, cursor, limit = 50, ranking }: ViewRequest,
): Promise<{ feed: Feed; etag: string } | "not-modified"> {
  const params = new URLSearchParams({ view, limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  if (ranking) params.set("ranking", ranking);
  const response = await apiFetch(`/api/feed?${params.toString()}`, {
    headers: etag ? { "If-None-Match": etag } : {},
  });
  if (response.status === 304) return "not-modified";
  if (response.status === 503 || response.status === 404) throw new FeedNotReadyError();
  if (!response.ok) throw new FeedHttpError(response.status);
  const body: unknown = await response.json();
  const version = (body as { schema_version?: unknown } | null)?.schema_version;
  if (version !== SUPPORTED_SCHEMA_VERSION) throw new UnsupportedFeedError(version);
  const feed = body as Feed;
  if (!Array.isArray(feed.items)) throw new UnsupportedFeedError(version);
  return { feed, etag: response.headers.get("ETag") ?? "" };
}
