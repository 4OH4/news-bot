import type { Feed } from "../feed/types";
import { apiFetch } from "./client";

export const SUPPORTED_SCHEMA_VERSION = 1;

/** The feed's `schema_version` is not one this client understands (ADR-0006). */
export class UnsupportedFeedError extends Error {
  constructor(readonly version: unknown) {
    super(`Unsupported feed schema version: ${String(version)}`);
    this.name = "UnsupportedFeedError";
  }
}

/** The API has no feed yet (503): the pipeline has not published one. */
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

/** Fetches the feed. Pass the last etag for a conditional GET; a 304 gives "not-modified". */
export async function fetchFeed(
  etag?: string,
): Promise<{ feed: Feed; etag: string } | "not-modified"> {
  const response = await apiFetch("/api/feed", {
    headers: etag ? { "If-None-Match": etag } : {},
  });
  if (response.status === 304) return "not-modified";
  if (response.status === 503) throw new FeedNotReadyError();
  if (!response.ok) throw new FeedHttpError(response.status);
  const body: unknown = await response.json();
  const version = (body as { schema_version?: unknown } | null)?.schema_version;
  if (version !== SUPPORTED_SCHEMA_VERSION) throw new UnsupportedFeedError(version);
  const feed = body as Feed;
  if (!Array.isArray(feed.items)) throw new UnsupportedFeedError(version);
  return { feed, etag: response.headers.get("ETag") ?? "" };
}
