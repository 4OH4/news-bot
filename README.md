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

# from the repo root
docker build -f backend/Dockerfile -t newsfeed .
docker run --rm newsfeed run
```

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

## Layout

```
backend/   Python package `newsfeed` (uv project) and Dockerfile
config/    sources, interest rules and extraction overrides (YAML)
eval/      extraction evaluation set, committed results (metrics only) and local per-run logs
docs/adr/  architecture decision records
client/    reader PWA (Phase 3)
infra/     infrastructure as code (Phase 2)
```

This is a personal project. No third-party article content is stored in this repository.
