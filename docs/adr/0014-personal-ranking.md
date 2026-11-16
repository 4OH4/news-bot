# ADR-0014: Personal ranking: local embeddings, a profile from reads, applied when a view is served

- **Status:** Accepted
- **Date:** 2026-11-16
- **Related:** #320 (this contract), #321 (embedder), #322 (model in the image), #323 (profile), #324 (clustering), #325 (publish clusters), #326 (API ordering), #337 (preferences); `docs/phases/phase-4.md` "Approach"; `PROJECT_PLAN.md` §11 ("embedding provider", now decided), FR-9, FR-10; [ADR-0001](0001-ports-and-adapters.md), [ADR-0002](0002-data-model-validation.md), [ADR-0006](0006-feed-json-schema.md), [ADR-0010](0010-client-access-and-feedback-identity.md), [ADR-0013](0013-feed-views-by-source-query.md).

## Context

Phase 4 adds a feed that learns from what the owner reads. Until now ranking is rules only: topic rules from `config/interests.yaml`, source weight and recency, scored by the rank stage and stored on the article (ADR-0013). Learning needs a way to compare articles by meaning (embeddings), a stored picture of the owner's interests (a profile), and a place to apply it.

Constraints: a bill that grows with each run, and the owner's reading history reaching any third party, both "matter" under `CLAUDE.md` "Cost of failure". Phase 3 gave 78 opens, 69 reads and 5 likes in nine days, so reads, not likes, carry the signal. ADR-0013 never recomputes stored scores, so anything baked in at rank time takes up to 72 hours to reach older items in a view.

## Decision

**1. Embeddings run locally, not on Vertex AI.** An `Embedder` port (`ports.py`) returns one unit-length vector per text. The adapter (#321) runs `BAAI/bge-small-en-v1.5` (MIT licence, 384 dimensions) through `fastembed` on the job's CPU, with the model baked into the image (#322). The text is the title, the summary and the first 2,000 characters of the body. Only articles without an embedding are embedded, about 20 to 40 a run. Vertex AI was rejected: it is a new paid service, a bill that grows each run, and a third party seeing which articles the owner reads. The port keeps a hosted model possible later.

**2. Embeddings are stored on `Article`.** `Article.embedding` (a non-empty list of finite floats) and `Article.embedding_model` (required when there is an embedding, because vectors from different models aren't comparable). The rank stage already reads and rewrites every article in its 72-hour window, so the vector rides on that write at no extra read. SQLite and Firestore store it as packed little-endian float32 bytes, not an array: Firestore indexes each array element separately, which would be 384 index entries per article. Old rows load with no embedding.

**3. The profile is built from reads, keyed by user id.** `Profile(user_id="owner")` holds up to four `InterestCentroid`s (a vector, a weight and the number of events behind it), a `negative` vector from dismissals, and when and from how many events it was built. Signal weights: a read up to 1 (full at 120 seconds), an open 0.3, a like 2, a dismiss to the negative vector; events decay with a 45-day half-life over 180 days; positive stories become centroids by weighted k-means, so separate interests don't blur into one average. The rank stage rebuilds it when there is new feedback, at most every six hours (#323), with the values in `config/personalisation.yaml`. `Repository.get_profile`/`save_profile` store it. The profile is recreated from feedback at will, so it needs no backup, and it never reaches a log line. The user id is `DEFAULT_USER_ID = "owner"`, a slug: the owner's email is never a key (ADR-0010 keeps it private).

**4. Similarity is stored beside the rule score, not in it.** Each embedded article gets `similarity` in its `score_breakdown`: 1 below the median closeness to the profile, rising to 3 for the closest. `Article.score` stays the rule score, so `feed.json`, Longer and the rules side of the A/B check (ADR-0015) are unchanged. Clustering also runs in rank (#324): articles whose embeddings have cosine 0.88 or more within 48 hours form one story (`cluster_id`), and a story covered by several sources gets a `coverage` factor of 1 + 0.25 ln(sources) in its rule score. Publish shows one item per cluster, with an optional `coverage` count in `feed.json` (#325).

**5. Personal order, diversity and preferences are applied in the API when a view is served.** The API already reads each view file per request; it re-orders `score` views from fields each item carries (#326): rules order is `score`, personal order is `score x similarity`, and one source-diversity rule (each further item from one source x0.85) applies to both. Reader preferences plug into the same step (#337). A change shows on the next refresh, the pipeline stays shared, and every factor appears in the served item's breakdown. Personalisation applies to Feed only; News stays newest first and Longer in rules order.

## Consequences

- Each run spends a few CPU seconds embedding new articles and, at most every six hours, one feedback read and one profile write. There is no per-call bill; the image grows by the model (measured in #322).
- The owner's reading history goes to no third party: "the embedding provider the design names" in `CLAUDE.md` "Cost of failure" is none.
- Vectors read back rounded to float32 (about 1e-7 relative), which is far finer than any threshold used; tests compare vectors within 1e-6.
- Rules-only ranking stays intact and measurable, which the A/B check needs.
- Profiles, preferences and feedback carry `user_id`, so another user later means a new key and access (stop-the-line), not a data migration.
- Revisit if the local model's quality is poor on the owner's sources, if the image or memory cost is too high (#322), or if a hosted model becomes free of both a bill and third-party exposure. Switching model means a new `Embedder` adapter and re-embedding, which `embedding_model` makes detectable.
