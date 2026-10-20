# news-bot
A fully custom news feed for Android

A self-hosted, personalised news feed: a Python pipeline ingests articles from feeds and
aggregators, extracts clean article content, ranks it against my interests, and publishes a
feed for a mobile reader client. It runs locally with SQLite and the filesystem, and in
production on GCP (Cloud Run Jobs, Firestore, Cloud Storage).

- [`PROJECT_PLAN.md`](PROJECT_PLAN.md): aims, requirements, architecture and phases
- [`CLAUDE.md`](CLAUDE.md): conventions and workflow for contributors (human and agent)
- [`docs/adr/`](docs/adr/): architecture decision records

## Quick start

Requires Ubuntu (or another Linux), [`uv`](https://docs.astral.sh/uv/) and Docker.

```bash
cd backend
uv run newsfeed run          # run the pipeline locally
uv run newsfeed report       # failing feeds and failed articles by site and reason (--run, --since, --json)

# from the repo root
docker build -f backend/Dockerfile -t newsfeed .
docker run --rm newsfeed run
```

### Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `NEWSFEED_STORAGE` | `local` | `local` uses SQLite and files under `NEWSFEED_DATA_DIR`; `gcp` uses Firestore and Cloud Storage with application default credentials (no key files). |
| `NEWSFEED_GCS_BUCKET` | none | Required when `NEWSFEED_STORAGE=gcp`: the Cloud Storage bucket for blobs. |
| `NEWSFEED_GCS_PREFIX` | empty | Optional key prefix inside the bucket. |
| `NEWSFEED_FIRESTORE_DATABASE` | default database | Optional Firestore database id. |
| `NEWSFEED_GCP_PROJECT` | from credentials | Optional Google Cloud project for both clients. |
| `NEWSFEED_DEDUPE_WINDOW_DAYS` | `14` (at least 1) | Ingest dedupes against articles fetched in this many days, which bounds the reads per run on Firestore. Keep it at least as long as any `--since` you use (a longer `--since` widens it for that run). An older entry that reappears is never allowed to overwrite a stored article, and one the feed dates before the window is skipped as `stale` once the source has been read in full before. |
| `NEWSFEED_RETRY_BACKOFF_HOURS` | `[1, 6, 24]` (JSON list) | Hours to wait after the 1st, 2nd... extraction attempt before retrying a transient failure. Its length is the cap (three retries by default); `[]` turns retries off. |
| `NEWSFEED_RETRY_WINDOW_DAYS` | `3` | Failed articles are only retried within this many days of being fetched. |
| `NEWSFEED_RETRY_PARTIAL` | `false` | Also retry `partial` extractions (usually a short page). |

### Retrying failed extractions

Each run's extract stage also retries failed articles whose failure looks temporary: timeouts (including HTTP 408), connection errors, HTTP 5xx and HTTP 429. It never retries robots.txt refusals, other 4xx errors (401, 403, 404, 410...), "no content" failures, or `paywalled` and `skipped` articles. A retry happens once the backoff for the attempt count has passed since `extraction.last_attempt_at`, up to the cap, and only while the article is inside the retry window. Every extraction attempt increments `extraction.attempts`, and retries are counted as `retried` in the run's extract stage counts. The policy is `should_retry` in `backend/src/newsfeed/extract/retry.py`.

### JavaScript rendering (optional)

Domains flagged `render: playwright` in `config/extraction.yaml` are rendered in headless
Chromium. Playwright is not in the default install or image:

```bash
cd backend
uv sync --group playwright
uv run playwright install chromium   # one-off browser download

# from the repo root: the image with Chromium (the default image stays slim)
docker build -f backend/Dockerfile --target playwright -t newsfeed:playwright .
```

### Emulator tests (optional)

`FirestoreRepository` and `GcsBlobStore` also run the shared contract tests against local Google
Cloud emulators. They are excluded from `make test` (marker `emulator`), and each is skipped unless
its emulator variable is set. Both need Docker.

Firestore (`FIRESTORE_EMULATOR_HOST`):

```bash
docker run -d --name firestore-emulator -p 8080:8080 \
    gcr.io/google.com/cloudsdktool/google-cloud-cli:emulators \
    gcloud emulators firestore start --host-port=0.0.0.0:8080
cd backend
FIRESTORE_EMULATOR_HOST=localhost:8080 uv run pytest -m emulator --allow-hosts=localhost,127.0.0.1
docker rm -f firestore-emulator
```

Cloud Storage (`STORAGE_EMULATOR_HOST`):

```bash
docker run -d --name gcs-emulator -p 4443:4443 fsouza/fake-gcs-server -scheme http -port 4443
cd backend
STORAGE_EMULATOR_HOST=http://localhost:4443 uv run pytest -m emulator --allow-hosts=localhost,127.0.0.1
docker rm -f gcs-emulator
```

## Layout

```
backend/   Python package `newsfeed` (uv project) and Dockerfile
config/    sources, interest rules and extraction overrides (YAML)
eval/      extraction evaluation set, committed results (metrics only) and local per-run logs
docs/adr/  architecture decision records
client/    reader PWA (Phase 3)
infra/     GCP infrastructure: gcloud bootstrap and Terraform (setup steps in infra/README.md)
```

This is a personal project. No third-party article content is stored in this repository.
