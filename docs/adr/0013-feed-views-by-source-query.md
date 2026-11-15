# ADR-0013: Feed views by source query, with stored scores and no copy checks

- **Status:** Accepted
- **Date:** 2026-11-15
- **Related:** #297 (this change), #296 (`Repository.list_articles(source_ids=...)` and its Firestore indexes), #288 and PR #292 (the first views), #293 (superseded); partly supersedes [ADR-0012](0012-feed-views.md) (windows, carry-forward, content check); amends [ADR-0006](0006-feed-json-schema.md) (display rule on a missing blob); [ADR-0005](0005-content-retention.md) (article copies kept 30 days); `config/views.yaml`; `DEFERRED_WORK.md` "A ranking recipe per view"; `docs/phases/phase-3.md` "Revision: simpler feed views".

## Context

ADR-0012 gave each view a time window. Longer's 14 days were kept by carrying items forward from the previous run's file, seeded once by a 14-day read, and every item shown as an article had its stored copy checked first. The owner found that too complex for what it does (Phase Log on #187, 2026-11-15). News showing a 96-hour-old item does no harm, and the copy check guards nothing the client doesn't already handle: when a copy is missing, the reader shows the summary and "Read it on the original site" (`client/src/reader/ReaderScreen.tsx`, the expired state).

## Decision

**A view is tags, an order and a maximum.** Each view in `config/views.yaml` has `tags` (a source matches if it has any of them; none means every source), `order` (`newest` or `score`) and `max_items` (1 to 1,000). The defaults are News (`[news]`, `newest`, 300), Feed (every source, `score`, 500) and Longer (`[long-form]`, `score`, 200). `window_hours` is gone, and a leftover one is a config error.

**Reads.** A tagged view's sources are the configured sources that are enabled, have weight above zero and have any of its tags. With none, the view is written empty and nothing is read. Otherwise publish makes exactly one call, `list_articles(source_ids=<those ids>, limit=max_items)`, with no time limit: its sources' newest `max_items` articles by fetch time. The view without tags reuses the 72-hour read `feed.json` already makes, so it costs no extra read. A failed read fails that view only: it is logged and the previous file stays, as a failed write does. The previous view file is never read.

**Eligibility and order.** Candidates pass `feed.json`'s rules (scored, not muted, source enabled with weight above zero, a valid item, one item per story) with no time window. `newest` orders by effective time (`published_at` capped at `fetched_at`), then score, then id; `score` orders as `feed.json` does. The list is cut to `max_items`; fewer is fine, because muted or unscored rows are dropped after the read.

**Scores are the stored ones.** Views use the `Article.score` the rank stage stored; publish never recomputes a score. Rank re-scores only articles fetched in the last 72 hours, so an older article keeps the score it had when it was last ranked, at about 72 hours old, with recency at most 0.125 at the default 24-hour half-life (recency is `0.5 ** (age / half_life)` from `published_at`, which is never later than `fetched_at`). In Longer, the last three days' pieces therefore sit above older ones of similar topic relevance, and older pieces order among themselves by relevance and source weight. Mutes and interest changes are applied at rank time (`rank/scoring.py`), so they don't reach items older than 72 hours either: a topic muted in `interests.yaml` still shows in Longer and in older News items until they drop out of the view. Disabling a source or setting its weight to 0 in `sources.yaml` does take effect at once, because publish checks sources on every run. Recomputing at publish would need each article's text from the blob store and the rank stage's scoring inside publish, which breaks architecture rule 3 (stages are independent); a per-view ranking recipe is already deferred in `DEFERRED_WORK.md`.

**No copy checks.** Publish makes no `BlobStore.exists` call, for views or for `feed.json`. An article whose extraction qualifies under ADR-0006 is shown as `article` when it has a `content_ref` and as `link` when it has none. This amends ADR-0006's sentence that an item is a `link` when "its blob is missing because it has expired": such an item is now an `article` whose `GET /api/articles/<id>` answers 404, which the reader shows as its expired state. Within today's numbers that can't happen in `feed.json` (72 hours against 30 days' retention) and is unlikely in Longer (200 long-form items is about 15 days). Where rows of one story compete for its place, the one with a `content_ref` that qualifies wins, as before, without a blob check.

**Unchanged.** The view file shape, the paging API and its cursors and ETags (ADR-0012 "Paging"), `docs/feed.schema.json`, `feed.json`'s selection, order and 200-item cap, and the rule that a failed view never undoes `feed.json`.

## Consequences

- Each hourly run reads about 1,500 Firestore documents for `feed.json` (unchanged) plus at most 500 for the tagged views (News 300, Longer 200), about 2,000 in all; the extra 500 are about $0.0002 a run at $0.03 to $0.06 per 100,000 reads, roughly $0.15 a month. Removing the copy checks saves up to about 1,900 Cloud Storage metadata reads a run ($0.0008 a run at $0.0004 per 1,000, about $0.55 a month), so the change is a net saving.
- Views fill at once after a config change: there is no carried state to rebuild, and a deleted view file is rewritten on the next run.
- News and Longer need #296's composite indexes in Firestore. Before they are `READY`, those two views fail each run with `FailedPrecondition` (logged; `feed.json` and the Feed view are unaffected) and recover by themselves.
- Older items in a view keep a stored score with recency at most 0.125, and mutes added after an item was last ranked don't hide it.
- **Revisit** if Longer's order feels wrong (a per-view ranking recipe, `DEFERRED_WORK.md`), or if a tag grows past 30 sources (Firestore's `in` filter takes 30 values, so one extra query per 30). Undo the copy-check removal by restoring `_content_exists` in `publish_feed` (about 200 Cloud Storage reads a run for `feed.json`).
