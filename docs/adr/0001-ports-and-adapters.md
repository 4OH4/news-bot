# ADR-0001: Ports and adapters, storage and concurrency model

- **Status:** Proposed
- **Date:** 2026-10-11
- **Related:** #5; blocks #7 (`ports.py`); informs #6 (`models.py`) and #8 (local adapters). See `PROJECT_PLAN.md` §5.2, §5.3, §8 and §9.3.

## Context

The pipeline (ingest → extract → rank → publish) is developed locally and runs in production on GCP (Cloud Run Jobs, Firestore, Cloud Storage). It must stay portable (NFR-1), cheap (NFR-2) and testable offline (NFR-7), and several agents will build stages and adapters in parallel once the contracts (#6, #7) are merged.

Before those contracts are written we need to settle how core code reaches storage and external services, which backends are used where, where article content lives, how data models are defined, and whether interfaces are synchronous or asynchronous. Recording this now stops each contract or stage issue from re-opening the same questions.

## Decision

**1. Ports and adapters.** Core code (`ingest/`, `extract/`, `rank/`, `publish/`, `fetching/`, `cli.py`) depends only on the interfaces in `backend/src/newsfeed/ports.py` and the models in `models.py`. Cloud SDKs (`google.cloud.*`, `firebase_admin`, Vertex AI) may only be imported inside `backend/src/newsfeed/adapters/`. Concrete adapters are chosen and wired together at the entry point (`cli.py`), not inside stages.

The initial ports are:

| Port | Responsibility | Adapters |
|------|----------------|----------|
| `Repository` | Persist and query sources, articles, feedback, profile and run stats | SQLite (dev/CI), Firestore (prod), in-memory fake (tests) |
| `BlobStore` | Store and retrieve opaque blobs by path: clean article HTML, images, `feed.json` | Local filesystem (dev/CI), GCS (prod), in-memory fake (tests) |
| `Fetcher` | All outbound HTTP, applying robots.txt, per-domain rate limits, User-Agent, conditional GETs and retries | `httpx`-based polite fetcher, `FakeFetcher` (tests) |
| `Extractor` | Turn a fetched page into an extraction result (content, metadata, status) | Trafilatura, then fallbacks and Playwright (#13–#15) |
| `SourceIngester` | Turn one configured source into normalised items | RSS/Atom, Google News, Hacker News, ... (#10, #11) |

`Embedder` is deferred to Phase 4 and will be added to `ports.py` through its own contract-change issue.

**2. Storage backends.** SQLite plus the local filesystem are the default for development and CI. Firestore plus Cloud Storage are used in production. No cloud service or emulator is required to run tests.

**3. Content as blobs, metadata in the repository.** Extracted article content (clean HTML, images) and the published feed snapshot are stored in the `BlobStore`. The `Repository` holds article metadata (URLs, title, source, timestamps, extraction status, scores) plus a `content_ref` pointing at the blob.

**4. Pydantic v2 for models.** `models.py` uses Pydantic v2 models, giving validation at stage boundaries and straightforward JSON serialisation for the repository, blobs and `feed.json`. Dataclasses remain acceptable for internal value objects that need no validation.

**5. Synchronous interfaces in Phase 1, with a bounded thread pool.** Ports are defined as synchronous methods. Where a stage needs concurrency (mainly fetching and extraction), it uses a bounded `concurrent.futures.ThreadPoolExecutor`, with per-domain politeness still enforced inside the `Fetcher`. Synchronous interfaces are simpler to test, simpler to fake and simpler for parallel agents to write against. We will revisit this if throughput becomes a problem.

### Alternatives considered

- **Async (`asyncio`) interfaces throughout.** Better I/O concurrency, and `httpx` supports it, but it makes every port, fake and test async, and mixes poorly with synchronous, CPU-bound libraries such as Trafilatura, which would need to run in a thread pool anyway. At single-user scale (tens to low hundreds of pages per run) a thread pool is sufficient.
- **Firestore emulator in CI instead of SQLite.** Closer to production behaviour, but adds a Java/emulator dependency to CI and local setup, and still ties tests to one vendor. SQLite keeps CI fast and dependency-free; Firestore-specific behaviour is covered by contract tests run against the Firestore adapter separately.
- **Storing article content in the database.** Simpler (one store), but Firestore documents are limited to 1 MiB and charged per read, and the client wants static, cacheable HTML. Blobs keep documents small and let the feed and articles be served straight from storage (NFR-4).
- **Talking to GCP SDKs directly from stages.** Least code up front, but breaks portability, makes tests depend on cloud credentials or heavy mocking, and blocks parallel work on stages and adapters.

## Consequences

**Positive**

- Stages and adapters can be built and tested in parallel against stable contracts, using in-memory fakes and synthetic fixtures with no network.
- The pipeline runs fully offline on a laptop (`make run`) and in CI with no cloud account.
- Moving off Firestore or GCS later means writing a new adapter, not changing stages.
- Small repository documents and static blobs keep production cost low and the client fast.

**Negative**

- The `Repository` interface must be designed to the lowest common denominator. Firestore's query limitations (no joins, limited inequality filters and ordering, composite indexes needed for many queries) must be respected, so queries that are easy in SQLite cannot be exposed if Firestore cannot serve them efficiently.
- Two implementations of each storage port must be kept in step. This is mitigated by a shared contract-test suite that every adapter (SQLite, local filesystem, Firestore, GCS, and the in-memory fakes) must pass.
- Writing content and metadata separately is not atomic. Stages must tolerate a blob without metadata, or metadata whose `content_ref` is missing (e.g. write the blob first, then the metadata).
- A thread pool gives less concurrency than async I/O and needs care with thread safety (e.g. SQLite connections per thread, a thread-safe rate limiter in the `Fetcher`).
- Pydantic v2 becomes a core dependency of every stage.

**Revisit when**

- pipeline runs approach the Cloud Run Job time limit or the scheduling interval because of fetch or extraction throughput (consider async fetching behind the same `Fetcher` port);
- `Embedder` is introduced in Phase 4;
- the `Repository` needs queries that Firestore cannot serve reasonably.
