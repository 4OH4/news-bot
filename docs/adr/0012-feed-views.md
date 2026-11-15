# ADR-0012: Feed views served in pages by the API

- **Status:** Accepted. Partly superseded by [ADR-0013](0013-feed-views-by-source-query.md): its windows, carry-forward and content check; the paging contract stands.
- **Date:** 2026-11-15
- **Related:** #288 (this change), #289 (client tabs); [ADR-0006](0006-feed-json-schema.md) (`feed.json`, "Versioning"); [ADR-0005](0005-content-retention.md) (article HTML kept 30 days); `config/views.yaml`; `docs/feed.schema.json`.

## Context

`feed.json` is one list: the top 200 by score from the last 72 hours. The owner reads in three ways: the news (news sources, newest first), browsing (today's mix) and something longer (long-form pieces, which score low on recency and topics and rarely reach the top 200). In the 72 hours to 2026-11-15 the store held 1,526 articles, about 1,090 from news sources and about 33 from long-form ones.

## Decision

**Views.** We will define named views in `config/views.yaml`: source tags (any match; none means every source), an order (`newest` or `score`) and a window in hours. The publish stage writes each one to `views/<name>.json` after `feed.json`, as `{"run_id", "generated_at", "items"}` with items in the `feed.json` item shape. A view's candidates are exactly `feed.json`'s (ADR-0006), selected by the same `build_feed` code given only the view's sources, with no 200-item cap; a safety cap of 2,000 items is logged when reached. View files are internal to the backend and are overwritten each run, with no timestamped copies. A failed view write is a stage error and never undoes `feed.json`.

**Paging.** `GET /api/feed?view=<name>&limit=<1-100, default 50>&cursor=<c>` returns one page in the `feed.json` envelope plus `view` and `next_cursor` (null on the last page). These are optional schema fields, an additive change under ADR-0006, so `schema_version` stays 1 and `GET /api/feed` without parameters still returns `feed.json` byte for byte. The cursor is opaque to the client (base64url of `<run_id>:<offset>`). A cursor from an earlier run serves the same offset of the current file, and the page's `run_id` tells the client the list changed; there is no snapshot pinning, because a rare duplicate or missed item is cheap (`CLAUDE.md`, "Cost of failure"). Unknown or malformed view names are 404, a malformed cursor 400, an out-of-range limit 422, and a view requested before any feed exists 503. Pages carry an ETag and answer `If-None-Match` with 304.

**Carry-forward.** Publish still reads 72 hours of articles. A view whose window is longer (Longer, 14 days) is this run's candidates plus the previous file's items that are still eligible: published within the window (no `published_at`, not carried), source enabled, weighted above zero and still tagged for the view, not muted, and, for `article` items, content still stored. A fresh row replaces a carried one with the same `story_id`; an item whose article was read this run is decided by this run alone. Carried items keep their last score. With no readable previous file, publish seeds the view once from a read of its whole window and logs it. A 14-day read every hourly run would be about 7,000 Firestore reads instead of about 1,500, a recurring cost for roughly 180 items.

**Why not filter `feed.json` in the client.** Filtering a top-200 list leaves gaps: low-scoring sources such as long-form magazines rarely reach it, so a client-side Longer tab would be nearly empty.

## Consequences

- The client can page any view without downloading it whole; `feed.json` keeps serving the installed client until it updates.
- Carried items keep a score that is no longer recomputed, and a view's longer window only fills forward: widening it takes effect over the following days unless the file is deleted to force a seed.
- Each run checks that content exists for every view candidate shown as an article, as `feed.json` does for its 200: up to about 1,700 Cloud Storage metadata reads per hourly run (one per blob, shared across views). At the Class B rate of $0.0004 per 1,000 that is under $0.001 a run and about $0.50 a month. Undo: trust `content_ref` for fresh candidates and check only carried items.
- **Revisit** if carried scores drift visibly from fresh ones, if a view needs per-view ranking, or to drop carry-forward (undo: read `max(window_hours)` every run and delete `_carried`, about 5 times the Firestore reads).
