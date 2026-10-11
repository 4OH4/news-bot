# ADR-0002: Data model validation conventions

- **Status:** Accepted
- **Date:** 2026-10-11
- **Related:** #6 and #29 (`models.py`); builds on ADR-0001 (Pydantic v2, content as blobs). Affects #8, #10, #13, #14 and #19. See `PROJECT_PLAN.md` §5.4.

## Context

`backend/src/newsfeed/models.py` is the contract between pipeline stages and adapters (CLAUDE.md rule 2). Several agents will build against it in parallel, so how it treats bad or ambiguous input has to be settled once, not decided differently in each stage.

Two forces pull in opposite directions:

- **Our own data should fail loudly.** A naive timestamp, a mistyped field name in `config/sources.yaml`, or an article whose id doesn't match its URL is a bug. It is cheapest to catch where it is introduced, not several stages later in the repository or the feed.
- **Third-party data must not fail an article.** Publishers' metadata is messy. Trafilatura returns publication dates as date-only strings (`2026-10-11`), and many meta tags omit the timezone offset. CLAUDE.md says errors are caught at stage boundaries and recorded on the item, so a validation error in one metadata field would mark an otherwise clean extraction as `failed`. That would lower the Phase 1 go/no-go score for no benefit.

## Decision

**1. Strict for our own timestamps, lenient for third-party dates.** All datetimes are timezone-aware UTC and serialise as ISO 8601 with `Z`. Aware values in other zones are converted to UTC. There are two types:

- `UtcDatetime`, for timestamps our code produces (`fetched_at`, `started_at`, `finished_at`, `last_fetched_at`, `created_at`). It **rejects** naive values. Code must use `datetime.now(UTC)`.
- `LenientUtcDatetime`, for dates that come from publishers (`published_at` on `Article` and `ExtractionResult`). It **coerces** instead of rejecting: a naive value is assumed to be UTC, a date-only value becomes midnight UTC, RFC 2822 and epoch values are accepted, and anything unparseable becomes `None`.

**2. `Article.extraction` is a nested model.** `ArticleExtraction` holds `status`, `method`, `error` and `word_count`, and defaults to status `pending`. This matches the `extraction: {…}` grouping in §5.4 and maps directly to a SQLite JSON column or a Firestore map. The extracted content itself stays in the `BlobStore` (ADR-0001).

**3. `Article.id` is derived and enforced.** When `id` is omitted or `None`, it is set to `article_id(canonical_url)`, the first 16 hex characters of the URL's SHA-256. A mismatched id is rejected. `id` and `canonical_url` are frozen, so changing the canonical URL means creating a new article and retiring the old id, not editing in place.

**4. Unknown fields are rejected and assignments are validated.** Every model uses `extra="forbid"` and `validate_assignment=True`. A typo in YAML or code, or an invalid value assigned after construction, raises immediately.

### Alternatives considered

- **Reject naive datetimes everywhere** (the first draft of #29). It is simple and uniform, but every extractor would then need its own date clean-up. If one forgets, articles fail silently in evaluation rather than in tests, because synthetic fixtures tend to use full ISO dates.
- **Assume UTC for naive datetimes everywhere.** This never fails, but it hides real bugs in our own code (e.g. `datetime.now()`), which would then be off by the machine's UTC offset with no signal.
- **Flat extraction fields on `Article`** (`extraction_status`, ...). They query slightly more easily in SQL, but put four related fields at the top level and drift from §5.4.
- **Derive `id` but don't enforce it**, or leave `canonical_url` mutable. Either allows an article whose id no longer matches its URL, which silently breaks deduplication (FR-4).
- **`extra="ignore"`** (Pydantic's default). It accepts typos such as `enable: false` in `sources.yaml` without complaint, which is exactly the kind of mistake config-as-code should catch.

## Consequences

**Positive**

- Bugs in our own code and config fail at the point they are introduced, with a clear message.
- Messy publisher metadata costs at most a slightly wrong or missing `published_at`, never a failed article.
- Article ids are always consistent with canonical URLs, so deduplication can rely on them.
- Adapters store and load the models as they are, with no per-adapter datetime or id handling.

**Negative**

- A lenient `published_at` can be wrong by a few hours (assumed UTC) or vague (midnight on a date-only value). Freshness ranking has to tolerate that. The pipeline should prefer the feed's `published_at`, which usually has a real time, and fall back to the extracted one (noted on #19).
- `model_copy(update=...)` skips validation in Pydantic, so it can bypass every rule above, including the id check. Use `Model.model_validate({...})` when changing validated fields. #19 has a note on re-canonicalising.
- Pydantic applies an assignment before the model-level checks run. A rejected assignment that only a model-level validator catches (e.g. `url=None` on an rss `Source`) can leave the instance changed. Sources are loaded from config and not edited, so this is accepted.
- Strict models make adapters brittle to schema drift. Data written by an older model version with an extra field won't load until it is migrated. That is intended; the SQLite adapter's `schema_version` (#8) handles migration.

**Revisit when**

- a third-party field other than `published_at` turns out to be messy enough to need the lenient treatment;
- stored data needs to tolerate extra fields during a migration (consider `extra="ignore"` on load paths only).
