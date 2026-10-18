# ADR-0006: The feed.json format and which items show as full articles

- **Status:** Accepted
- **Date:** 2026-10-18
- **Related:** #82 (this decision); #83 (publish stage, built to it); #81 (rank stage, writes `score` and `score_breakdown`); ADR-0001 (ports, blob store); ADR-0003 decision 1 (non-clean items are links); FR-8, NFR-4; `PROJECT_PLAN.md` §5.1, §5.3, §6 Phases 2–3. Schema: `docs/feed.schema.json`. Example: `docs/feed.example.json`.

## Context

The reader client (Phase 3) shows the feed from a pre-built file, `feed.json`, so opening the app needs no live server call (NFR-4, §5.3). The publish stage (#83) writes that file at the end of each run. Its shape is a contract between the pipeline and the client, which are deployed separately, so it needs a written schema and a way to make breaking changes safely.

ADR-0003 decided that an article whose extraction isn't clean is published as headline, summary and link to the original, not as a reader article, and left the exact rule for `partial` to this decision.

Evidence from the Phase 1 live run (719 articles stored, 2026-10-17; counts read from the local database, no text):

- **Volume.** 182 articles were published in the 24 hours before the run, 274 in 48 hours, 355 in 72 hours and 513 in 7 days. Every article had a summary (plain text, up to 500 characters); 649 had a lead image.
- **Content size.** The 711 cleaned HTML blobs total 8.9 MB: median 7 KB, 90th percentile 26 KB, largest 347 KB. Inlining 200 articles would make the feed about 2.5 MB.
- **`partial` items** (51; `partial` means 50–149 words with the default `min_words` of 150). 20 are complete but short by design: Nature news items (12, 86–145 words) and Simon Willison link posts (8, 78–138 words, about 50 of them the blog's sidebar). The other 31 are not a full article: a standfirst under an audio, video or cartoon player (26, 51–148 words), members-only previews (404 Media, 62 and 112), LWN subscriber notices (78 and 102) and a short video page (119). The two groups overlap across the whole range:

| `partial` shown as article from | Complete items shown as article | Standfirsts and previews shown as article | Complete items shown as link |
|---|---|---|---|
| 80 words | 19 | 12 | 1 |
| 100 words | 14 | 11 | 6 |
| 120 words | 8 | 4 | 12 |
| 150 words | 0 | 0 | 20 |

No single word count separates them. Showing a teaser as an article is the failure ADR-0003 exists to prevent; showing a complete short item as a link costs one tap to the original, and the summary often says most of it.

## Decision

### Files and location

- The publish stage writes **one `feed.json`** at the blob key `feed.json`, at the root of the same `BlobStore` that holds the content. Locally that is `data/blobs/feed.json`.
- It **first** writes a copy at `feeds/<run_id>.json`, then the fixed key, so a failed write leaves the previous `feed.json` in place. The copies record what the feed showed at each run, for debugging and for the Phase 4 comparison of ranking methods. In Cloud Storage they are kept for 7 days, by the lifecycle rule ADR-0005 (#73) sets for `feeds/`; locally the copies are kept.
- **Article content is not inlined.** Each article item gives `content_path`, the path of its cleaned HTML relative to `feed.json`: `articles/<id>.html`, the key the extract stage already writes. The client fetches it when the article is opened (or prefetches it for offline reading). This keeps the feed at about 1 KB per item, about 200 KB for a full feed before compression.

### Format

The JSON Schema in `docs/feed.schema.json` (draft 2020-12) is the authoritative definition; the example `docs/feed.example.json` validates against it.

- **Top level:** `schema_version` (integer, `1`), `generated_at` (UTC timestamp), `run_id` (`RunStats.run_id`) and `items`.
- **Each item:** `id` (`Article.id`), `title`, `url` (the canonical URL), `source_id`, `domain` (host of the canonical URL without `www.`), `author`, `published_at`, `summary` (plain text), `lead_image` (the publisher's absolute URL), `score`, `score_breakdown`, `extraction_status`, `display` (`article` or `link`), `word_count` and `content_path`. Every key is always present; values the article lacks are `null`.
- **Source names:** `Source` has no display name today, so the client shows `domain`. Adding one is a contract change to `models.py` (its own `needs-human` issue); the feed would then gain an optional `source_name`, an additive change.
- **Score breakdown is included.** It is a handful of numbers per item, and it lets the client explain a ranking ("why am I seeing this", Phase 6) and lets me tune the rules from the phone. Its keys are the rank stage's (#81: `topic:<name>`, `source`, `recency`); they are for explanation only, and the client never recomputes the score from them.

### Selection and order

- **Candidates:** articles with a score (ranked), from a source that is still enabled in `config/sources.yaml`, whose effective time is within **72 hours** of `generated_at`. The effective time is `published_at`, or `fetched_at` when there is none, the same fallback the rank stage uses for recency.
- **Muted articles are left out entirely.** An article is muted when its `score_breakdown` has a `muted` key (#81 writes it only when a mute applies, and keeps the article stored so the reason can be inspected). The schema rejects a `muted` key in a feed item.
- **Order:** `score` descending, then effective time descending, then `id` ascending, so the same inputs always give the same feed.
- **Limit:** the first **200** items. At the live run's volume that is between one and two days of articles, more than a day's reading.
- Items with non-clean extraction stay in the feed as links; they are not filtered out.

### Display rule

| `extraction.status` | `display` |
|---|---|
| `ok` | `article` |
| `partial` with `word_count` of 150 or more | `article` |
| `partial` under 150 words | `link` |
| `paywalled`, `failed`, `skipped`, `pending` | `link` |

An item is also a `link` when it has no stored content: `content_ref` is empty, or its blob is missing because it has expired under ADR-0005 (#73). For `link` items `word_count` and `content_path` are `null`, and the client shows title, summary and image and opens `url`.

The **`partial` cut-off is a single number, 150 words**, held as a named constant in the publish code. It is the default `min_words`, so a `partial` item becomes an article only on a domain whose `config/extraction.yaml` override raises `min_words` above 150, and in the live run every `partial` item is a link. The table above is why: at 100 words, 11 standfirsts, members-only previews and subscriber notices would open as if they were the article, including both 404 Media previews and an LWN subscriber notice. Domains whose short items really are complete, such as Nature news, are better handled with the per-domain `min_words` override that already exists (`nature.com: min_words: 80` would make all 12 `ok`), a change to `config/extraction.yaml` with its own evaluation run, than with a feed-wide threshold. The schema enforces the cut-off, so changing it means editing this ADR, the constant and the schema together; that is not a breaking change for the client.

### Versioning

- `schema_version` is a single integer that changes **only for a breaking change**: removing or renaming a field, changing a field's type or meaning, a new `display` value, or moving the file or the content layout.
- **Additive changes** (a new field, a new `score_breakdown` key, a new `extraction_status` value) keep the version. The schema file is updated in the same PR as the publisher, and the client must ignore fields it doesn't know and show unknown statuses as a plain label.
- The publisher's output must match the committed schema exactly (unknown fields are rejected), and #83 validates it in tests, so the contract can't change without a schema change in review.
- **Deploy order for a breaking change:** the client that understands version N+1 ships first; then the pipeline writes it. A client that receives a `schema_version` it doesn't support keeps showing its last good cached feed and says an update is needed, rather than failing.

### Alternatives considered

- **Everything in one `feed.json`, content inlined.** Simplest for the client, but about 2.5 MB at 200 items, against NFR-4. Rejected.
- **`feed.json` plus a JSON file per article.** Adds a second copy of each article in a new format; the extract stage already stores clean HTML per article that the client can fetch directly. Rejected.
- **`partial` cut-off of 100 words** (the issue's example). Shows 14 complete short items as articles but also 11 non-articles. Rejected, see the table.
- **Per-domain cut-offs in `config/extraction.yaml`.** A second per-domain number next to `min_words` that would mean almost the same thing. Rejected until the evidence needs it; `min_words` already gives per-domain control.
- **Keep muted articles with a flag.** Lets the client show them on demand, but makes the feed larger for something I asked not to see; the database keeps them for inspection. Rejected.
- **Semantic version string.** Only the major part matters to a client that ignores unknown fields. Rejected for a single integer.

## Consequences

- #83 has a complete spec: selection, order, display rule, file keys and a schema to validate against. It adds a JSON Schema validator as a development dependency for its tests.
- The Phase 3 client can be written against `docs/feed.schema.json` and the example before the pipeline runs on GCP. It needs one extra fetch per opened article, which offline prefetching hides.
- Complete short items (Nature news, Simon Willison link posts) show as links until their domains get a `min_words` override. If that bothers me in daily use, the fix is the override and an evaluation run, not a lower cut-off.
- Paywall detection (ADR-0003 decision 2) moves previews and subscriber notices from `partial` or `ok` to `paywalled`. They are links under both, so this decision doesn't change when it lands.
- Lead images are hotlinked from publishers, so the phone fetches them directly. Caching images in our own store is possible later as an additive change.
- **Revisit** the 72-hour window and the 200-item limit once the pipeline runs hourly on GCP and the feed's real size is known, and the cut-off if a domain shows `partial` items above 150 words that are complete.
