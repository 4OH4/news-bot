# ADR-0005: Content retention: metadata kept, article content expires after 30 days

- **Status:** Accepted
- **Date:** 2026-10-18
- **Related:** #73 (this decision); #74 (GCP setup, applies the lifecycle rule); #77 (GCS `BlobStore`); #82, #83 (`feed.json` and the publish stage); [ADR-0001](0001-ports-and-adapters.md) (content in the `BlobStore`, metadata in the `Repository`); [ADR-0003](0003-phase-1-go-decision.md) (headline-and-link display); [ADR-0004](0004-infrastructure-as-code.md) (Terraform manages the bucket); `PROJECT_PLAN.md` §11 ("Cost creep", "content retention period"); NFR-2.

## Context

Every extracted article is stored twice (ADR-0001): its metadata (URL, title, source, timestamps, extraction status, score and `content_ref`) in the `Repository`, and its cleaned HTML in the `BlobStore` under `articles/<id>.html`. Locally that only fills a disk. From Phase 2 it means third-party content held in Cloud Storage, and metadata in Firestore, for as long as we keep it. `PROJECT_PLAN.md` §11 proposed keeping metadata indefinitely and full content for 30–90 days. The GCP setup (#74) should apply any lifecycle rule from the start, so we decide now.

Volumes from the Phase 1 live run (25 sources, 2026-10-17, local SQLite and filesystem):

- **Articles:** 719 stored in the first run, which took in each feed's whole backlog. Steady state is lower. By publication date, 182 of the 719 appeared in the 24 hours before the run and 513 in the previous 7 days. Some feeds list only their latest items, so the true rate is between about 75 and 180 new articles a day. The estimates below use 180 a day as the upper bound.
- **Content blobs:** 711 files, 8.9 MB in all. Mean 12.5 KB, median 7.0 KB, 90th percentile 25.8 KB, largest 347 KB. No images are stored yet (`lead_image` is a URL).
- **Metadata:** about 785 bytes per article in SQLite. Firestore adds field names and index entries, so we assume about 2 KB per document.
- **Run stats:** about 1.1 KB per run. Feedback: none yet (Phase 3). `feed.json`: not written yet (#83).

| Option | Content held (at 75–180 articles/day) | Metadata |
|--------|---------------------------------------|----------|
| Content for 30 days | 28–68 MB, steady | about 130 MB a year at 180/day |
| Content for 90 days | 84–200 MB, steady | the same |
| Keep everything | 340–820 MB more each year, without limit | the same |

Storage cost doesn't decide this. Cloud Storage Standard costs a few US cents per GB per month, so even keeping everything costs well under $1 a month after a year, and Firestore's free tier includes 1 GiB of storage. What differs is how much third-party content we hold, and for how long, while the reader doesn't need it. The feed shows recent articles, an old article stays reachable through its link, and the dedupe that stops old URLs being ingested again needs only the metadata. Keeping metadata indefinitely does have one running cost: ingest's dedupe currently lists every stored article on each run, and on Firestore each document read is billed, so at hourly runs the reads would outgrow the free tier (50,000 a day) within weeks. #104 bounds that lookup to recently fetched articles before the unattended week, which keeps reads flat as history grows.

## Decision

**1. Article content expires 30 days after it is written.** A Cloud Storage lifecycle rule on the bucket deletes objects under `articles/` once they are 30 days old (condition `age: 30`, action `Delete`, `matchesPrefix` set to the `GcsBlobStore` key prefix from #77 followed by `articles/`). Terraform applies it in #74 (ADR-0004), with the number of days as a variable, `content_retention_days`, defaulting to 30. Any images the pipeline stores later go under `articles/` or another prefix with the same rule. Cloud Storage runs lifecycle rules asynchronously, so a blob may outlive its age by up to about a day.

**2. Expiry is the lifecycle rule only. The pipeline doesn't delete anything and doesn't clear `content_ref`.** When an article's blob is gone, `BlobStore.get` raises `BlobNotFoundError`, and anything reading content (publish, preview, and the API in Phase 3) shows the article as headline, summary and link, just as ADR-0003 shows a non-clean article. ADR-0001 already requires stages to tolerate a `content_ref` with no blob. A missing blob for an article younger than the retention period is still an error and is logged as one. The pipeline knows the period from a setting, `NEWSFEED_CONTENT_RETENTION_DAYS` (default 30), which the Cloud Run Job gets from the same Terraform variable so the two can't drift apart. Clearing `content_ref` would need a "fetched before" query that the `Repository` contract doesn't have, a Firestore read and write for every expired article, and a second deletion path to keep in step with the rule, all to save a lookup that fails cheaply.

**3. Article metadata is kept indefinitely.** It is small, it is what dedupe uses to recognise old URLs, and it keeps the record of what was ingested and how extraction went. Feedback (Phase 3) is also kept indefinitely, since it is the ranking signal. Source state is overwritten on every run, so it doesn't grow.

**4. Run stats are kept indefinitely.** At about 1.1 KB per hourly run that is about 10 MB a year, and the per-domain trends that extraction observability needs (ADR-0003 decision 4) rely on old runs.

**5. `feed.json` is never expired; timestamped copies are kept for 7 days.** The current feed lives at a fixed key that each run overwrites, outside the `articles/` prefix. If the `feed.json` decision (#82) asks for timestamped copies, they go under a prefix of their own (e.g. `feeds/`) with a 7-day lifecycle rule. That is enough to look into a bad run, and at hourly runs it is about 170 small files. The feed's time window must stay shorter than the content retention period, so `feed.json` never offers an article whose content has expired.

**6. Liked articles keep their content (Phase 3).** When feedback arrives, a liked article's content is copied to `saved/<id>.html`, a prefix the lifecycle rule doesn't cover, and its `content_ref` is pointed at the copy. That needs only `BlobStore.put` and `get`, so no contract change. Opening an article doesn't extend its retention, because opening is too weak a signal. The details belong to the Phase 3 feedback issue.

**7. Local development doesn't enforce retention.** The local filesystem has no lifecycle rules, and at the volumes above `data/blobs/` grows by under 1 GB a year. `data/` is git-ignored and can be removed at any time. A local prune command would need a `BlobStore.delete` method, which is a contract change, so we'll add one only if local disk use becomes a problem.

### Alternatives considered

- **Content for 90 days** (the plan's upper bound). Still cheap, but it holds three times as much third-party content, for the rare case of re-reading an unsaved article months later in the reader rather than at its source. Liking an article (decision 6) covers the articles worth keeping.
- **Keep everything.** Simplest, but it holds third-party content indefinitely and storage grows without limit. Not chosen.
- **A pipeline clean-up step, alone or as well as the lifecycle rule.** It would let us clear `content_ref` and enforce retention locally, but it needs a `BlobStore.delete` method and a "fetched before" `Repository` query (both contract changes), and Firestore reads and writes on every run. The lifecycle rule does the same deletion without code.
- **Expire metadata too.** It would save little space and break dedupe: old URLs that reappear in aggregators would be ingested and fetched again.

## Consequences

- #74 adds the `articles/` lifecycle rule (and the `feeds/` rule if #82 calls for timestamped copies), with `content_retention_days` as a Terraform variable passed to the Cloud Run Job as `NEWSFEED_CONTENT_RETENTION_DAYS`. If #74 enables object versioning, it also needs a rule that deletes noncurrent versions, or the deleted content stays in the bucket. The bucket's default soft-delete retention (7 days) keeps deleted blobs a week longer. That is acceptable, and #74 may turn it off.
- One follow-up issue, after #83: treat a missing content blob as expired content in the publish stage and the preview (headline, summary and link; an info log if the article is older than the retention period, a warning otherwise), and add the `NEWSFEED_CONTENT_RETENTION_DAYS` setting. #83's feed window must also be shorter than the retention period.
- Lowering the retention period later takes effect at the next lifecycle pass. Raising it doesn't bring back content already deleted.
- Anything that needs article text after 30 days (embeddings or story clustering in Phase 4) must be computed while the content exists, or re-fetched from the source.
- **Revisit** if Firestore reads per run grow with the number of stored articles rather than staying flat (they shouldn't once #104 lands), if Firestore metadata approaches its 1 GiB free tier (at about 130 MB a year, not for several years), if Phase 3 shows I regularly want unsaved articles older than 30 days, or if Phase 4 needs a backlog of article text.
