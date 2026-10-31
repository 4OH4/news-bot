#!/usr/bin/env python3
"""Bootstrap GitHub labels, milestones and seed issues for the newsfeed project.

Creates everything in PROJECT_PLAN.md §9.1 and §10 using the GitHub CLI:
  * labels (area:*, size:*, agent-ready, needs-human, blocked)
  * milestones Phase 0 .. Phase 6
  * the 21 seed issues for Phases 0-1, with "Depends on #N" links filled in
    and "Blocks" lists computed for needs-human issues
  * optionally, adds every seed issue to an existing GitHub Project board

Usage:
  python3 scripts/bootstrap_github.py --repo 4OH4/news-bot --dry-run
  python3 scripts/bootstrap_github.py --repo 4OH4/news-bot
  python3 scripts/bootstrap_github.py --repo 4OH4/news-bot --project-owner 4OH4 --project-number 1

Requirements:
  * Python 3.10+ (standard library only)
  * gh CLI, authenticated: `gh auth login`
  * adding to a project needs the project scope: `gh auth refresh -s project`

Safe to re-run: labels are upserted, and existing milestones and issues
(matched by exact title) are reused rather than duplicated. Bodies of issues
that already exist are never overwritten.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Labels and milestones
# --------------------------------------------------------------------------

AREAS = ["ingest", "extract", "rank", "store", "api", "client", "infra", "eval", "docs"]

LABELS: list[tuple[str, str, str]] = [
    ("agent-ready", "0E8A16", "Fully specified; safe for an agent to pick up unsupervised"),
    ("needs-human", "D93F0B", "Needs a human decision, credentials or judgement"),
    ("blocked", "B60205", "Waiting on another issue or an external factor"),
    ("size:S", "C2E0C6", "Small: under a couple of hours"),
    ("size:M", "FEF2C0", "Medium: roughly half a day"),
    ("size:L", "F9D0C4", "Large: split before marking agent-ready"),
    ("tier:A", "C5DEF5", "Low risk: the delivery manager merges it (scripts/tier.py)"),
    ("tier:B", "FBCA04", "Decide and record: agents merge it, the human confirms later"),
    ("tier:C", "B60205", "Reserved area: the human merges it"),
    ("lead-merged", "5319E7", "Merged by the lead under the old Tier A gate"),
    ("agent-merged", "5319E7", "Merged by an agent through scripts/tier.py"),
    ("hold", "000000", "Don't merge: stops agents merging the PR"),
] + [(f"area:{a}", "BFD4F2", f"Area: {a}") for a in AREAS]

MILESTONES: list[tuple[str, str]] = [
    ("Phase 0", "Foundations: scaffold, tooling, CI and conventions"),
    ("Phase 1", "Extraction spike: local pipeline and evaluation harness; ends with go/no-go"),
    ("Phase 2", "Pipeline on GCP and rule-based ranking"),
    ("Phase 3", "Reader client (PWA)"),
    ("Phase 4", "Personalisation"),
    ("Phase 5", "Native Android app and widget"),
    ("Phase 6", "Enhancements"),
]

STANDARD_CRITERIA = [
    "Unit tests cover the new behaviour using fixtures or fakes (no network)",
    "`make lint` and `make test` pass",
]

# --------------------------------------------------------------------------
# Seed issues
# --------------------------------------------------------------------------


@dataclass
class Issue:
    n: int  # seed number from PROJECT_PLAN.md §10
    title: str
    area: str
    size: str
    kind: str  # "agent-ready" | "needs-human"
    phase: str
    context: str
    deps: list[int] = field(default_factory=list)
    # agent-ready fields
    task: str = ""
    acceptance: list[str] = field(default_factory=list)
    standard_criteria: bool = True
    in_scope: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    interfaces: str = "None"
    verify: list[str] = field(default_factory=lambda: ["make lint", "make test"])
    notes: str = ""
    # needs-human fields
    nh_type: str = ""
    needed: str = ""
    done: list[str] = field(default_factory=list)
    agent_prep: str = ""


ISSUES: list[Issue] = [
    # ---------------------------------------------------------------- Phase 0
    Issue(
        n=1,
        title="[infra] Scaffold repo structure, uv project and Dockerfile",
        area="infra", size="S", kind="agent-ready", phase="Phase 0",
        context=(
            "First step of Phase 0 (PROJECT_PLAN.md §6, §8). Everything else builds on this layout. "
            "NFR-1: processing runs as a plain Python Docker container."
        ),
        task="""Create the repository skeleton described in PROJECT_PLAN.md §8:

- Directories: `docs/adr/`, `config/`, `backend/src/newsfeed/{ingest,extract,rank,publish,adapters,api}/`, `backend/tests/`, `eval/results/`, `client/`, `infra/`. Python packages get `__init__.py`; empty non-Python directories get `.gitkeep`. (Evaluation snapshots live outside the repo; see CLAUDE.md.)
- `backend/pyproject.toml`: a `uv` project, Python 3.12+, src layout, package `newsfeed`, console script `newsfeed = "newsfeed.cli:app"`.
- `backend/src/newsfeed/cli.py`: a Typer app with a `run` command that logs "pipeline not implemented yet" and exits 0.
- `backend/src/newsfeed/logging.py`: `get_logger(name)` returning a logger that emits structured JSON lines (stdlib `logging` with a JSON formatter, or `structlog`; state the choice in the PR).
- `backend/Dockerfile`: multi-stage build using the official `uv` image to install dependencies, a slim Python 3.12 runtime, a non-root user, and `ENTRYPOINT ["newsfeed"]`. Build context is the **repo root** so `config/` can be copied in.
- Check that the existing `.gitignore` (GitHub's Python template, plus a project section) covers `data/` and `eval/fixtures/`, and add anything else the scaffold needs. Add `.dockerignore` and `.env.example`, and extend the existing `README.md` with a short overview pointing to `PROJECT_PLAN.md` and `CLAUDE.md`.""",
        acceptance=[
            "`cd backend && uv run newsfeed run` exits 0 and prints one JSON log line",
            "`docker build -f backend/Dockerfile -t newsfeed .` succeeds from the repo root",
            "`docker run --rm newsfeed run` exits 0",
            "Directory layout matches PROJECT_PLAN.md §8",
            "No secrets or local data paths committed",
        ],
        standard_criteria=False,
        in_scope=["All new files listed in the task", "`.gitignore` and `README.md` (extend, don't replace)", "`.dockerignore`, `.env.example`"],
        out_of_scope=[
            "Lint, type-check and test tooling, and the Makefile ({ref:2})",
            "CI workflow ({ref:3})",
            "Any pipeline logic",
        ],
        verify=[
            "cd backend && uv run newsfeed run",
            "docker build -f backend/Dockerfile -t newsfeed .",
            "docker run --rm newsfeed run",
        ],
        notes="Keep runtime dependencies minimal: Typer, plus structlog if chosen.",
    ),
    Issue(
        n=2,
        title="[infra] Add ruff, mypy, pytest, Makefile targets and pre-commit",
        area="infra", size="S", kind="agent-ready", phase="Phase 0", deps=[1],
        context="Phase 0 tooling (PROJECT_PLAN.md §6). Gives every later issue the `make lint` / `make test` gate that CLAUDE.md relies on.",
        task="""- Add dev dependencies with `uv add --dev`: ruff, mypy, pytest, pytest-cov, pytest-socket, pre-commit.
- ruff config in `backend/pyproject.toml`: line length 100; rules `E,F,W,I,B,UP,SIM,RUF`.
- mypy in strict mode over `backend/src/`.
- pytest config: `testpaths`, a registered `live` marker, and `addopts = -m "not live" --disable-socket --allow-unix-socket` so tests cannot reach the network.
- Root `Makefile` with targets `help` (default), `setup`, `lint`, `fmt`, `test`, `eval`, `run`, `docker`. Until the eval harness lands, `eval` prints "not implemented yet" and exits 0. Export `NEWSFEED_CONFIG_DIR=$(CURDIR)/config` and `NEWSFEED_DATA_DIR=$(CURDIR)/data` so the CLI finds them from `backend/`.
- `.pre-commit-config.yaml`: ruff check --fix, ruff format, end-of-file-fixer, trailing-whitespace, check-yaml, detect-private-key.
- Smoke test `backend/tests/test_cli.py` invoking `run` through Typer's `CliRunner`.""",
        acceptance=[
            "`make setup` works on a clean clone on Ubuntu",
            "A test proves network access is blocked (expects `SocketBlockedError`)",
            "`make docker` builds the image",
            "pre-commit hooks run cleanly on all files",
        ],
        in_scope=["`backend/pyproject.toml`", "`Makefile`", "`.pre-commit-config.yaml`", "`backend/tests/`"],
        out_of_scope=["CI workflow ({ref:3})", "Changes to the package layout"],
        notes="This picks mypy (strict). If there's a strong reason to prefer pyright, raise it on the issue before switching.",
    ),
    Issue(
        n=3,
        title="[infra] CI workflow: lint, test and Docker build on PRs",
        area="infra", size="S", kind="agent-ready", phase="Phase 0", deps=[2],
        context="Phase 0 exit criterion: CI is green (PROJECT_PLAN.md §6). CI is the main guardrail for agent PRs (§9.4).",
        task="""Add `.github/workflows/ci.yml`, triggered on `pull_request` and on `push` to `main`:

- Jobs `lint` and `test`: `astral-sh/setup-uv` with caching, Python 3.12, install dependencies, then `make lint` / `make test`.
- Job `docker`: `docker/setup-buildx-action`, build only (no push), with the GitHub Actions cache.
- A `concurrency` group per ref with `cancel-in-progress: true`.
- Minimal permissions: `contents: read`.
- Actions pinned to major versions.""",
        acceptance=[
            "Workflow is green on the PR that adds it",
            "Job names are exactly `lint`, `test` and `docker` (they become required checks)",
            "A deliberately failing lint change fails the check (show in the PR description; don't leave it committed)",
            "Total runtime under about 5 minutes; no secrets used",
        ],
        standard_criteria=False,
        in_scope=["`.github/workflows/ci.yml`"],
        out_of_scope=["Deployment and GCP auth (Phase 2)", "Branch protection (done by a human in {ref:4})"],
        verify=["Open a PR and confirm all three checks pass"],
    ),
    Issue(
        n=4,
        title="[docs] Commit CLAUDE.md and templates; set up board and branch protection",
        area="docs", size="S", kind="needs-human", phase="Phase 0",
        nh_type="Setup / credentials",
        context="Drafts of `CLAUDE.md`, both issue templates, the PR template and this bootstrap script already exist (PROJECT_PLAN.md §9).",
        needed="""- Review and commit `CLAUDE.md`, `.github/ISSUE_TEMPLATE/*.yml`, `.github/pull_request_template.md` and `scripts/bootstrap_github.py`.
- On the Ubuntu dev machine: install `make`, `uv`, Docker Engine and `gh`, then authenticate (`gh auth login`).
- Run the bootstrap script (labels, milestones, seed issues). It created this issue, so tick that box.
- Create the GitHub Project board and set the Status options to: Backlog, Ready, In progress, Agent review, Human review, Done. Add the seed issues to it.
- Once {ref:3} is merged, protect `main`: require a PR, require checks `lint`, `test` and `docker`, and block force pushes.
- Enable "Automatically delete head branches" in the repo settings.""",
        done=[
            "Docs and templates committed",
            "Labels, milestones and seed issues created",
            "Project board configured with the six Status columns",
            "Branch protection on `main` (after {ref:3})",
        ],
    ),
    Issue(
        n=5,
        title="[docs] ADR-0001: ports and adapters, storage and concurrency model",
        area="docs", size="S", kind="needs-human", phase="Phase 0",
        nh_type="Decision (architecture / approach, usually ends in an ADR)",
        context="PROJECT_PLAN.md §5.3 sets out ports and adapters. The contracts ({ref:6}, {ref:7}) need these decisions recorded first so agents don't re-litigate them.",
        needed="""- Add `docs/adr/0000-template.md` (Status, Context, Decision, Consequences).
- Write `docs/adr/0001-ports-and-adapters.md` recording:
  - core code depends only on `ports.py`; cloud SDKs only inside `adapters/`
  - SQLite plus local filesystem for development and CI; Firestore plus GCS in production
  - article content stored as blobs, metadata in the repository
  - Pydantic v2 for models
  - sync vs async interfaces. Recommendation: synchronous interfaces in Phase 1 with a bounded thread pool, because they're simpler to test and to write against in parallel; revisit if throughput becomes a problem.""",
        done=["ADR template merged", "ADR-0001 merged with status Accepted"],
        agent_prep="An agent can draft both files from PROJECT_PLAN.md §5 for review.",
    ),
    # ---------------------------------------------------------------- Phase 1: contracts
    Issue(
        n=6,
        title="[store] Define models.py: Source, Article, ExtractionResult, Feedback, RunStats",
        area="store", size="S", kind="needs-human", phase="Phase 1", deps=[1],
        nh_type="Contract change (models.py / ports.py)",
        context="The shared data model is the contract between pipeline stages (PROJECT_PLAN.md §5.4, §9.3). Most Phase 1 issues depend on it, so it's reviewed line by line.",
        needed="""Define Pydantic v2 models in `backend/src/newsfeed/models.py`:

- `SourceType` enum (`rss`, `google_news`, `hn`, `reddit`) and `Source` (id, type, url, query, weight=1.0, enabled=True, tags, options dict for type-specific settings)
- `SourceState` (source_id, etag, last_modified, last_fetched_at, last_status)
- `FetchResult` (url, final_url, status_code, headers, content: bytes, not_modified, error, elapsed_ms)
- `ExtractionStatus` enum (`pending`, `ok`, `partial`, `failed`, `paywalled`, `skipped`)
- `ExtractionAttempt` (method, status, error, word_count) and `ExtractionResult` (status, method, title, author, published_at, content_html, text, lead_image, word_count, error, attempts)
- `Article` (id, canonical_url, url, source_id, title, author, published_at, fetched_at, summary, lead_image, extraction status/method/error/word_count, content_ref, score, score_breakdown, cluster_id)
- `FeedbackEvent` enum and `Feedback`; `InterestRules` and `Profile`
- `RunStats` (run_id, started_at, finished_at, counts per stage, extraction outcomes per domain, error count)
- `article_id(canonical_url) -> str` helper: the first 16 hex characters of the URL's SHA-256

All datetimes are timezone-aware UTC.""",
        done=[
            "Models and helper implemented with unit tests (validation, id helper, JSON round-trip)",
            "Reviewed line by line and merged",
        ],
        agent_prep="An agent can draft this PR from PROJECT_PLAN.md §5.4; I review it carefully because every stage depends on it.",
    ),
    Issue(
        n=7,
        title="[store] Define ports.py interfaces and in-memory fakes",
        area="store", size="S", kind="needs-human", phase="Phase 1", deps=[5, 6],
        nh_type="Contract change (models.py / ports.py)",
        context="Stages and adapters are built in parallel against these interfaces (PROJECT_PLAN.md §5.3, §9.3). Shared fakes let every issue test without real adapters.",
        needed="""Define `typing.Protocol` interfaces in `backend/src/newsfeed/ports.py`:

- `Repository`: `upsert_article`, `get_article`, `list_articles(*, status=None, since=None, limit=None)`, `get_source_state` / `save_source_state`, `save_feedback` / `list_feedback(since)`, `save_run_stats`
- `BlobStore`: `put(key, data, content_type) -> ref`, `get(ref) -> bytes`, `exists(ref) -> bool`
- `Fetcher`: `fetch(url, *, etag=None, last_modified=None) -> FetchResult`; `is_allowed(url) -> bool`; and `permit(url)`, a context manager that applies robots.txt and rate limiting for fetchers that don't go through httpx (such as Playwright)
- `Extractor`: `name: str`; `extract(html: str, url: str) -> ExtractionResult`
- `SourceIngester`: `source_type: SourceType`; `ingest(source, fetcher, state) -> tuple[list[Article], SourceState]`

Add in-memory fakes for every port in `backend/tests/fakes.py`, including a `FakeFetcher` that serves canned responses from a dict or fixture directory.

The `Embedder` port is deferred to Phase 4.""",
        done=["Ports defined, consistent with ADR-0001", "Fakes implemented and tested", "Reviewed and merged"],
        agent_prep="An agent can draft this PR once {ref:6} is merged.",
    ),
    # ---------------------------------------------------------------- Phase 1: adapters & ingest
    Issue(
        n=8,
        title="[store] SQLite Repository and local-filesystem BlobStore adapters",
        area="store", size="M", kind="agent-ready", phase="Phase 1", deps=[6, 7],
        context="Local adapters are the default for development and CI (CLAUDE.md rule 4; PROJECT_PLAN.md §5.2).",
        task="""- `backend/src/newsfeed/adapters/sqlite_repository.py`: implements `Repository` with stdlib `sqlite3`. Tables for articles, source_state, feedback and run_stats; nested fields as JSON columns; a `schema_version` table with create/migrate on open; WAL mode; safe for use from multiple threads.
- `backend/src/newsfeed/adapters/localfs_blobstore.py`: implements `BlobStore` under a base directory. Keys are validated against path traversal, and writes are atomic (temp file then rename).
- Configuration through constructor parameters only (paths). Wiring from environment variables happens in {ref:19}.
- Shared **contract test suites** in `backend/tests/contract/` (`test_repository_contract.py`, `test_blobstore_contract.py`), parametrised over the fakes and the real adapters, so the Firestore and GCS adapters can reuse them in Phase 2.""",
        acceptance=[
            "Contract tests pass for both the fakes and the SQLite/local-filesystem adapters",
            "`upsert_article` is idempotent",
            "Data persists across reopening the database",
            "Concurrent writes from 8 threads don't error",
            "A path-traversal key (e.g. `../x`) is rejected",
        ],
        in_scope=["`backend/src/newsfeed/adapters/`", "`backend/tests/contract/`", "`backend/tests/adapters/`"],
        out_of_scope=["Firestore and GCS adapters (Phase 2)", "Changes to `ports.py` or `models.py`"],
        interfaces="Implements: `Repository`, `BlobStore`. Contract changes: none.",
    ),
    Issue(
        n=9,
        title="[ingest] Polite HTTP fetcher: robots.txt, rate limits, conditional GET, retries",
        area="ingest", size="M", kind="agent-ready", phase="Phase 1", deps=[7],
        context="NFR-3 and CLAUDE.md rule 6: all HTTP goes through one polite fetcher.",
        task="""Implement `HttpFetcher` (the `Fetcher` port) in `backend/src/newsfeed/fetching/http_fetcher.py` using httpx:

- Identifiable User-Agent passed in, e.g. `newsfeed-personal/0.1 (+https://github.com/4OH4/news-bot)`.
- robots.txt fetched once per host per process via `urllib.robotparser`. A 4xx response means allow all; a 5xx or timeout means disallow that host for this run.
- Per-host minimum interval between requests (default 2s, overridable per host), thread-safe.
- Conditional GET (`If-None-Match` / `If-Modified-Since`). A 304 returns `not_modified=True`.
- Up to 3 retries with exponential backoff and jitter on connection errors, timeouts, 429 and 502–504. Honour `Retry-After`, capped at 60s.
- Timeouts (connect 10s, read 20s), up to 5 redirects with `final_url` recorded, and a maximum body size (5 MB) that returns an error result if exceeded.
- Never raises for HTTP or network errors; returns a `FetchResult` with `error` set.
- `permit(url)` context manager applying the robots check and rate limit.
- Logs each fetch with url, status, elapsed time and attempt number.
- Inject `clock` and `sleep` callables so tests run instantly.""",
        acceptance=[
            "Tests with `httpx.MockTransport` cover: robots disallow, rate limiting, 304, retry on 503 then success, Retry-After, oversize body, and redirect `final_url`",
            "`permit()` enforces both the robots check and the rate limit",
        ],
        in_scope=["`backend/src/newsfeed/fetching/`", "`backend/tests/fetching/`", "`backend/pyproject.toml` (add httpx)"],
        out_of_scope=["Feed parsing ({ref:10})", "Playwright rendering ({ref:15})", "Changes to `ports.py`"],
        interfaces="Implements: `Fetcher`. Uses: `FetchResult`. Contract changes: none.",
    ),
    Issue(
        n=10,
        title="[ingest] RSS/Atom ingester and config/sources.yaml loader",
        area="ingest", size="M", kind="agent-ready", phase="Phase 1", deps=[6, 7],
        context="FR-1 and FR-3. Uses the `Fetcher` port through `FakeFetcher` in tests, so it can run in parallel with {ref:9}.",
        task="""- Define the `config/sources.yaml` schema and add an example file with 3–5 well-known public feeds, clearly marked as placeholders. The real list comes from {ref:16}.
- `backend/src/newsfeed/config.py`: `load_sources(path) -> list[Source]`, validated with Pydantic and giving clear errors (duplicate ids, rss source without url, unknown type).
- `backend/src/newsfeed/ingest/rss.py`: `RssIngester` implementing `SourceIngester` with feedparser on the bytes returned by the Fetcher:
  - conditional GET using `SourceState`; a 304 yields no articles and leaves the state unchanged
  - entries normalised to `Article` with extraction status `pending`; `canonical_url = url`, because canonicalisation is applied centrally in the pipeline ({ref:12}, {ref:19})
  - summary stripped to plain text and truncated to 500 characters; `published_at` converted to UTC, or None if absent; relative links resolved
  - malformed feeds (feedparser `bozo`) logged, returning whatever parsed""",
        acceptance=[
            "Synthetic fixtures (invented headlines and summaries) for RSS 2.0, Atom 1.0, relative links and a malformed feed all parse as expected",
            "304 Not Modified yields no articles and unchanged state",
            "The loader rejects invalid config with a clear message",
        ],
        in_scope=["`backend/src/newsfeed/config.py`", "`backend/src/newsfeed/ingest/`", "`config/sources.yaml`", "`backend/tests/ingest/`", "`backend/tests/fixtures/feeds/`"],
        out_of_scope=["Google News and Hacker News ingesters ({ref:11})", "URL canonicalisation ({ref:12})", "The real HTTP fetcher ({ref:9})"],
        interfaces="Implements: `SourceIngester`. Uses: `Fetcher` (via `FakeFetcher` in tests), `Source`, `SourceState`, `Article`.",
    ),
    Issue(
        n=11,
        title="[ingest] Google News topic RSS and Hacker News ingesters",
        area="ingest", size="M", kind="agent-ready", phase="Phase 1", deps=[10],
        context="Adds a discovery layer beyond fixed feeds (PROJECT_PLAN.md §3; the discovery gap discussed in planning).",
        task="""- `GoogleNewsIngester` (`google_news`): builds the search RSS URL from `query` plus optional `hl`/`gl`/`ceid` (default `en-GB` / `GB` / `GB:en`) and reuses the RSS parsing from `rss.py`. Refactoring shared helpers within `ingest/` is fine.
  - Links are `news.google.com/rss/articles/...` redirect URLs. Spike whether following redirects through the Fetcher yields publisher URLs (try about 5 live examples manually; tests use fixtures). If it doesn't, keep the Google URL and open a follow-up issue describing the options. **Don't** call undocumented internal Google endpoints.
- `HackerNewsIngester` (`hn`): uses the Algolia HN Search API (`https://hn.algolia.com/api/v1/search?tags=front_page`), with options `min_points` and `max_items`. Items without a URL (Ask HN) link to the HN item page. Put the HN discussion link in the summary.
- Add example entries for both to `config/sources.yaml`.""",
        acceptance=[
            "The Google News URL builder encodes queries correctly (tests)",
            "Tests for both ingesters using synthetic fixtures shaped like the real responses (invented titles, no copied content)",
            "`min_points` filtering works; Ask HN items are handled",
            "Findings from the redirect spike are written up in the PR",
        ],
        in_scope=["`backend/src/newsfeed/ingest/`", "`config/sources.yaml`", "`backend/tests/ingest/`", "`backend/tests/fixtures/feeds/`"],
        out_of_scope=["Reddit (later)", "URL canonicalisation ({ref:12})"],
        interfaces="Implements: `SourceIngester` (x2).",
    ),
    Issue(
        n=12,
        title="[ingest] Canonical URL resolution and dedupe",
        area="ingest", size="S", kind="agent-ready", phase="Phase 1", deps=[6],
        context="FR-4. Article ids are derived from canonical URLs, so this determines whether duplicates are caught.",
        task="""Pure functions only, no fetching:

- `backend/src/newsfeed/ingest/canonical.py`:
  - `normalise_url(url)`: lowercase scheme and host, IDNA host, drop default ports and fragments, strip tracking parameters (`utm_*`, `fbclid`, `gclid`, `mc_cid`, `mc_eid`, `ref`, `ref_src`, `igshid`, `cmpid`, `ocid`, and similar), sort the remaining query parameters, and strip the trailing slash except at the root. Optionally map common AMP URL patterns to their canonical form.
  - `canonical_from_html(html, base_url) -> str | None`: `<link rel="canonical">`, then `og:url`, absolutised and normalised. Cross-domain canonicals are accepted but logged.
- `backend/src/newsfeed/ingest/dedupe.py`: `dedupe(candidates, existing_ids) -> list[Article]`. Within a batch, keep the earliest-published occurrence.""",
        acceptance=[
            "Table-driven tests with at least 25 URL cases",
            "Idempotent: `normalise_url(normalise_url(x)) == normalise_url(x)`",
            "Tests for canonical extraction from HTML, including missing and relative tags",
        ],
        in_scope=["`backend/src/newsfeed/ingest/canonical.py`", "`backend/src/newsfeed/ingest/dedupe.py`", "`backend/tests/ingest/`"],
        out_of_scope=["Near-duplicate story clustering (Phase 4)", "Resolving Google News redirect URLs ({ref:11})"],
        interfaces="Uses: `Article`, `article_id`. Contract changes: none.",
    ),
    # ---------------------------------------------------------------- Phase 1: extraction
    Issue(
        n=13,
        title="[extract] Trafilatura extractor with HTML sanitisation",
        area="extract", size="M", kind="agent-ready", phase="Phase 1", deps=[6, 7],
        context="FR-5. The primary extractor, and the core of the Phase 1 risk test (PROJECT_PLAN.md §6, §7).",
        task="""- `backend/src/newsfeed/extract/sanitize.py`: `sanitize_html(html, base_url)` using `nh3` with an allowlist (`p, h2–h4, ul, ol, li, blockquote, a[href], img[src, alt], figure, figcaption, em, strong, code, pre, table, thead, tbody, tr, th, td, br, hr`). Absolutise `href`/`src`, map lazy-load attributes (`data-src`) to `src`, drop `srcset`, remove empty elements, and add `rel="noopener noreferrer"` to links. Strip everything else, including `script`, `style`, `iframe`, `on*` attributes and `javascript:` URLs. Shared by all extractors.
- `backend/src/newsfeed/extract/trafilatura_extractor.py`: `TrafilaturaExtractor` (`name="trafilatura"`), producing HTML output with formatting, images and links, plus metadata (title, author, date, image). Word count comes from the plain text. Status is `ok` if `word_count >= min_words` (default 150), `partial` if greater than 0, otherwise `failed`. Exceptions produce `failed` with the error recorded.
- Fixtures: at least 5 **synthetic** pages in `backend/tests/fixtures/extract/` that mimic real structures with invented text: a news article with typical publisher chrome (nav, related links, ads markup, JSON-LD metadata), a blog post, a page with cookie-banner markup, a page with lazy-loaded images, and a near-empty JS shell. The repo is public, so don't save real pages here. Real-page quality is measured by the evaluation harness ({ref:18}).""",
        acceptance=[
            "The sanitiser removes scripts, styles, iframes, `on*` attributes and `javascript:` URLs (tests)",
            "The JS-shell fixture returns `failed` or `partial`, not an exception",
            "Metadata is extracted from the news fixture",
            "Output is deterministic across runs",
        ],
        in_scope=["`backend/src/newsfeed/extract/`", "`backend/tests/extract/`", "`backend/tests/fixtures/extract/`", "`backend/pyproject.toml` (trafilatura, nh3)"],
        out_of_scope=["Fallback extractors and the chain ({ref:14})", "Playwright ({ref:15})", "Network fetching"],
        interfaces="Implements: `Extractor`. Uses: `ExtractionResult`. Contract changes: none.",
        notes="Check trafilatura's current API for HTML output and pin the version.",
    ),
    Issue(
        n=14,
        title="[extract] Fallback extractors and extractor chain with per-domain overrides",
        area="extract", size="M", kind="agent-ready", phase="Phase 1", deps=[13],
        context="FR-5 and FR-6. Fallbacks raise the success rate; the evaluation harness compares extractors.",
        task="""- `ReadabilityExtractor` (readability-lxml) and `Newspaper4kExtractor` (newspaper4k), each working on the HTML passed in, with the library's own fetching disabled. Output goes through `sanitize_html`.
- `backend/src/newsfeed/extract/chain.py`: `ExtractorChain(extractors, overrides)` tries extractors in order until one returns `ok`, records every attempt in `ExtractionResult.attempts`, and otherwise returns the partial result with the highest word count.
- `config/extraction.yaml` with per-domain overrides (`www.` stripped, subdomains matched): `order: [...]`, `render: playwright` (used by {ref:15}), `mode: summary_only` (skip extraction and keep the feed summary), `min_words`. Include a validated loader.""",
        acceptance=[
            "Chain order is respected, and the fallback is used when the primary fails (stub extractors)",
            "Domain overrides apply, including subdomain matching",
            "`summary_only` yields status `skipped`",
            "Docker image size before and after is reported in the PR",
        ],
        in_scope=["`backend/src/newsfeed/extract/`", "`config/extraction.yaml`", "`backend/tests/extract/`", "`backend/pyproject.toml`"],
        out_of_scope=["Playwright ({ref:15})", "Changes to `sanitize.py` beyond bug fixes"],
        interfaces="Implements: `Extractor` (x2, plus the chain). Contract changes: none.",
        notes="newspaper4k pulls in heavy dependencies. If it adds more than about 150 MB to the image, put it in an optional dependency group and say so in the PR.",
    ),
    Issue(
        n=15,
        title="[extract] Playwright renderer for JavaScript-heavy domains",
        area="extract", size="M", kind="agent-ready", phase="Phase 1", deps=[13],
        context="FR-6. Some sites only render content with JavaScript. Playwright must not bloat the default image (PROJECT_PLAN.md §11).",
        task="""- `backend/src/newsfeed/extract/playwright_renderer.py`: renders a URL in headless Chromium. It wraps navigation in `Fetcher.permit(url)` for robots and rate limiting, uses the project User-Agent, blocks images, media, fonts and known ad/tracker hosts through route interception, and waits for `domcontentloaded` plus a short `networkidle`, with a timeout (default 20s).
- `PlaywrightExtractor` (`name="playwright+trafilatura"`): renders, then delegates to `TrafilaturaExtractor`.
- Packaging: an optional dependency group `playwright` and a separate Docker build target `playwright` in `backend/Dockerfile`; the default target stays slim. Document local browser install (`uv run playwright install chromium`).
- Used only for domains flagged `render: playwright` in `config/extraction.yaml`. If {ref:14} isn't merged yet, accept an explicit domain list instead.""",
        acceptance=[
            "Unit tests with a mocked renderer (no browser in the default test run)",
            "A test confirms `permit()` is called before navigation",
            "One `@pytest.mark.live` test renders a simple public page",
            "Default image size is unchanged; both sizes are reported in the PR",
        ],
        in_scope=["`backend/src/newsfeed/extract/playwright_*.py`", "`backend/tests/extract/`", "`backend/Dockerfile`", "`backend/pyproject.toml`"],
        out_of_scope=["Running Playwright in GCP (Phase 2)", "Changes to the chain logic"],
        interfaces="Implements: `Extractor`. Uses: `Fetcher.permit`. Contract changes: none.",
        verify=["make lint", "make test", "uv run pytest -m live -k playwright", "make docker"],
    ),
    # ---------------------------------------------------------------- Phase 1: evaluation
    Issue(
        n=16,
        title="[eval] Curate sources and the evaluation URL set (~100 URLs)",
        area="eval", size="M", kind="needs-human", phase="Phase 1",
        nh_type="Content curation (sources, interests, eval expectations)",
        context="NFR-7 and PROJECT_PLAN.md §7. Judging what counts as clean extraction is a human call, and the go/no-go decision rests on it.",
        needed="""- Populate `config/sources.yaml` with the 20–30 feeds I actually read.
- Create `eval/urls.yaml` with about 100 article URLs from those sources, 3–5 per source. Include a mix of short news, long-form, blogs, live blogs, video/gallery pages, known JS-heavy sites, and roughly 10% paywalled.
- Each entry uses this schema:

```yaml
- id: bbc-2026-10-01-energy-prices
  url: https://www.bbc.co.uk/news/articles/...
  source_id: bbc-news
  expected_title: "Energy prices to rise in January"
  must_contain:
    - "a distinctive sentence fragment from the body"
  word_range: [400, 900]
  paywalled: false
  tags: [news]
  notes: ""
```""",
        done=["`config/sources.yaml` has my real feeds", "`eval/urls.yaml` has about 100 checked entries", "Merged"],
        agent_prep="An agent can pull recent items from the feeds and open a draft PR with urls, titles and word estimates pre-filled. I verify the snippets and paywall flags by reading each page.",
    ),
    Issue(
        n=17,
        title="[eval] Fixture snapshot tool",
        area="eval", size="S", kind="agent-ready", phase="Phase 1", deps=[9],
        context="PROJECT_PLAN.md §7: the evaluation runs against saved page snapshots so results are deterministic and sites aren't hit repeatedly.",
        task="""`newsfeed eval snapshot [--refresh] [--only ID]`:

- Reads `eval/urls.yaml` and fetches each URL through the `Fetcher`.
- Writes snapshots **outside the repo** to the directory given by `--fixtures-dir`, else the `NEWSFEED_FIXTURES_DIR` env var, else `~/.newsfeed/eval-fixtures` (resolved with `pathlib.Path.home()`). Layout: `<domain>/<id>.html.gz` plus `<id>.meta.json` (url, final_url, status, fetched_at, content_type, selected headers, sha256).
- Refuses to write inside the git working tree, as a guard against committing third-party content.
- Skips existing snapshots unless `--refresh` is given.
- Non-200 results are recorded in the meta file only, with no HTML saved.
- Prints a summary of fetched, skipped and failed (with reasons).""",
        acceptance=[
            "Tested with `FakeFetcher`, a temporary urls.yaml and a temporary fixtures dir",
            "Gzip round-trip works, and the command is idempotent",
            "Directory resolution order (flag, then env var, then default) is tested",
            "Writing inside the repo is refused (test)",
        ],
        in_scope=["`backend/src/newsfeed/eval/`", "`backend/src/newsfeed/cli.py` (eval subcommand group)", "`backend/tests/eval/`"],
        out_of_scope=["Running the snapshot against the real list. I do this once {ref:16} is merged, because it needs the network", "Evaluation scoring ({ref:18})"],
        interfaces="Uses: `Fetcher`, `FetchResult`.",
    ),
    Issue(
        n=18,
        title="[eval] Evaluation harness and per-domain report (make eval)",
        area="eval", size="M", kind="agent-ready", phase="Phase 1", deps=[13, 17],
        context="NFR-7 and PROJECT_PLAN.md §7. This produces the numbers behind the Phase 1 go/no-go decision.",
        task="""`newsfeed eval run [--extractor NAME] [--domain D] [--compare PATH]`, wired to `make eval`:

- For each `eval/urls.yaml` entry that has a snapshot in the fixtures dir (same resolution as {ref:17}), run every registered extractor, plus the chain if available.
- Checks per result:
  - title match (normalised, `difflib` ratio of at least 0.9)
  - every `must_contain` snippet present (whitespace and quotes normalised)
  - word count within `word_range`
  - boilerplate check: phrases from `eval/boilerplate.txt` (cookie, consent, subscribe, newsletter, advert, and so on) don't exceed a threshold
- An entry is **clean** when all checks pass. Paywalled entries are reported separately and excluded from the headline score.
- Report: headline clean %, a per-extractor table, a domain × extractor matrix, failure reasons by category, and the slowest extractions. Written to `eval/results/<YYYY-MM-DD>-<shortsha>.md` and `.json`, with a summary printed. Reports contain **metrics and short failure reasons only**, never extracted article text, because they're committed to a public repo.
- `--compare PATH` shows the diff against an earlier results JSON, for before/after comparisons in PRs.
- Exits 0 regardless of score; non-zero only on harness errors. Missing fixtures are listed as "not snapshotted", not as failures.""",
        acceptance=[
            "Tests with 3 synthetic entries and stub extractors produce the expected report numbers",
            "The report is deterministic",
            "`make eval` runs the harness",
        ],
        in_scope=["`backend/src/newsfeed/eval/`", "`eval/boilerplate.txt`", "`Makefile` (`eval` target)", "`backend/tests/eval/`"],
        out_of_scope=["Editing `eval/urls.yaml` expectations (human only)", "Live-fetch evaluation mode (a follow-up)"],
        interfaces="Uses: `Extractor`, `ExtractionResult`.",
        verify=["make lint", "make test", "make eval"],
    ),
    # ---------------------------------------------------------------- Phase 1: wiring & review
    Issue(
        n=19,
        title="[ingest] CLI: newsfeed run wiring ingest → extract → store",
        area="ingest", size="M", kind="agent-ready", phase="Phase 1", deps=[8, 10, 12, 13],
        context="Brings the Phase 1 pieces together into a working local pipeline (PROJECT_PLAN.md §5.1, §6).",
        task="""- `backend/src/newsfeed/settings.py` (pydantic-settings): `NEWSFEED_DATA_DIR` (default `./data`), `NEWSFEED_CONFIG_DIR` (`./config`), `NEWSFEED_FIXTURES_DIR` (`~/.newsfeed/eval-fixtures`, reusing the resolution from {ref:17}), `NEWSFEED_STORAGE` (`local`), user agent, concurrency (default 8), per-host interval.
- `backend/src/newsfeed/wiring.py`: builds adapters from settings, an ingester registry keyed by `SourceType`, and the extractor (the chain if available, otherwise Trafilatura).
- `backend/src/newsfeed/pipeline.py` and CLI commands:
  - `newsfeed ingest`: all enabled sources → canonicalise → dedupe against the repository → upsert pending articles; save `SourceState`
  - `newsfeed extract`: pending articles → fetch → refine the canonical URL from HTML, merging if the id changes → extract → content HTML to BlobStore at `articles/<id>.html` → update the article
  - `newsfeed run`: ingest then extract, then save `RunStats`
  - `newsfeed extract --url URL`: debug mode that fetches and extracts one page and prints the metadata and first 500 characters; persists nothing unless `--save`
  - Options: `--source ID` (repeatable), `--limit N`, `--since`
- A bounded `ThreadPoolExecutor` for extraction, with per-item errors caught and recorded.""",
        acceptance=[
            "End-to-end test with `FakeFetcher` (a synthetic RSS fixture plus synthetic article pages) and real SQLite/local-filesystem adapters in a temp dir: articles are stored with content",
            "A second run is idempotent: no duplicates, and 304s are respected",
            "One failing article doesn't fail the run",
            "`RunStats` are saved with stage counts and per-domain extraction outcomes",
            "`make run` works against `config/sources.yaml` (manual check, needs network; paste the summary in the PR)",
        ],
        in_scope=["`backend/src/newsfeed/settings.py`", "`backend/src/newsfeed/wiring.py`", "`backend/src/newsfeed/pipeline.py`", "`backend/src/newsfeed/cli.py`", "`backend/tests/`", "`Makefile` (`run` target)"],
        out_of_scope=["Ranking and publishing (Phase 2)", "Cloud adapters"],
        interfaces="Uses all ports. Contract changes: none.",
    ),
    Issue(
        n=20,
        title="[client] Local static HTML preview of extracted articles",
        area="client", size="S", kind="agent-ready", phase="Phase 1", deps=[19],
        context="Phase 1 output (PROJECT_PLAN.md §6): a way to read the extracted articles and judge quality by eye.",
        task="""`newsfeed preview [--out data/preview] [--no-images]` generates a static site with Jinja2:

- `index.html`: articles newest first, with title, source, published date, extraction status badge, method, word count and a link to the original.
- One page per article, rendering the stored cleaned HTML with readable typography (about 70ch max width, system font stack, light/dark via `prefers-color-scheme`).
- `failures.html`: failed articles with their error reasons.
- No JavaScript and no external CSS, fonts or CDNs. Images hotlink to the originals unless `--no-images` is given.""",
        acceptance=[
            "Generated from SQLite and local-filesystem fixture data in tests",
            "A test asserts the output has no `<script>` tags and no external stylesheet links",
            "Works when opened via `file://`",
        ],
        in_scope=["`backend/src/newsfeed/preview/`", "`backend/src/newsfeed/cli.py` (preview command)", "`backend/tests/preview/`"],
        out_of_scope=["The PWA (Phase 3)"],
        interfaces="Uses: `Repository`, `BlobStore`.",
    ),
    Issue(
        n=21,
        title="[eval] Phase 1 go/no-go review",
        area="eval", size="S", kind="needs-human", phase="Phase 1", deps=[16, 18, 19, 20],
        nh_type="Review (go/no-go, quality check)",
        context="The Phase 1 exit criterion (PROJECT_PLAN.md §6): at least 85–90% clean extraction on non-paywalled articles, with failures understood.",
        needed="""- Snapshot the full evaluation set (`newsfeed eval snapshot`) into the local fixtures dir. Snapshots are **not** committed; only results are.
- Run `make eval` and record the headline clean % for non-paywalled articles.
- Review failure categories, tune `config/extraction.yaml` overrides, and re-run.
- Live check: `newsfeed run` against the real sources. Note any 403s, Cloudflare challenges or blocked domains.
- Read at least 20 articles in the preview for a subjective quality check.
- Record the decision and numbers in an ADR: go, go with scope changes, or stop.
- If go: refine and open the Phase 2 issues.""",
        done=["Eval results (metrics only) committed", "Decision ADR merged", "Phase 2 issues opened (if go)"],
        agent_prep="An agent can run the evaluation and draft a summary of failures grouped by cause and domain, with suggested `extraction.yaml` overrides.",
    ),
]

# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

REF_RE = re.compile(r"\{ref:(\d+)\}")


def render_refs(text: str, numbers: dict[int, int]) -> tuple[str, bool]:
    """Replace {ref:N} with #<github number>. Returns (text, fully_resolved)."""
    resolved = True

    def sub(m: re.Match[str]) -> str:
        nonlocal resolved
        seed = int(m.group(1))
        if seed in numbers:
            return f"#{numbers[seed]}"
        resolved = False
        return f"(seed issue {seed})"

    return REF_RE.sub(sub, text), resolved


def bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "- None"


def checkboxes(items: list[str]) -> str:
    return "\n".join(f"- [ ] {i}" for i in items)


def deps_text(issue: Issue) -> str:
    return "\n".join(f"Depends on {{ref:{d}}}" for d in issue.deps) if issue.deps else "None"


def blocks_of(issue: Issue) -> list[int]:
    return [i.n for i in ISSUES if issue.n in i.deps]


def raw_body(issue: Issue) -> str:
    footer = f"\n\n---\n_Seed issue {issue.n} from PROJECT_PLAN.md §10._"
    if issue.kind == "agent-ready":
        criteria = issue.acceptance + (STANDARD_CRITERIA if issue.standard_criteria else [])
        verify = "\n".join(issue.verify)
        parts = [
            f"### Area\n\n{issue.area}",
            f"### Size\n\n{issue.size}",
            f"### Phase\n\n{issue.phase}",
            f"### Context\n\n{issue.context}",
            f"### Task\n\n{issue.task}",
            f"### Acceptance criteria\n\n{checkboxes(criteria)}",
            f"### Files / modules in scope\n\n{bullets(issue.in_scope)}",
            f"### Out of scope\n\n{bullets(issue.out_of_scope)}",
            f"### Interfaces touched\n\n{issue.interfaces}",
            f"### Dependencies\n\n{deps_text(issue)}",
            f"### How to verify\n\n```bash\n{verify}\n```",
        ]
        if issue.notes:
            parts.append(f"### Notes / hints\n\n{issue.notes}")
    else:
        blocks = ", ".join(f"{{ref:{b}}}" for b in blocks_of(issue)) or "None"
        parts = [
            f"### Type\n\n{issue.nh_type}",
            f"### Area\n\n{issue.area}",
            f"### Phase\n\n{issue.phase}",
            f"### Context\n\n{issue.context}",
            f"### What's needed\n\n{issue.needed}",
            f"### Definition of done\n\n{checkboxes(issue.done)}",
            f"### Blocks\n\n{blocks}",
            f"### Dependencies\n\n{deps_text(issue)}",
        ]
        if issue.agent_prep:
            parts.append(f"### Agent prep (optional)\n\n{issue.agent_prep}")
    return "\n\n".join(parts) + footer


def labels_for(issue: Issue) -> list[str]:
    return [issue.kind, f"area:{issue.area}", f"size:{issue.size}"]


# --------------------------------------------------------------------------
# GitHub CLI helpers
# --------------------------------------------------------------------------


def gh(*args: str, input_text: str | None = None) -> str:
    # Explicit UTF-8: on Windows the default code page would mangle characters
    # such as "→" and "≥" in issue bodies.
    result = subprocess.run(
        ["gh", *args], input=input_text, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    if result.returncode != 0:
        sys.exit(f"gh {' '.join(args[:3])} ... failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def ensure_labels(repo: str) -> None:
    for name, color, desc in LABELS:
        gh("label", "create", name, "--repo", repo, "--color", color, "--description", desc, "--force")
        print(f"  label  {name}")


def ensure_milestones(repo: str) -> None:
    existing = {
        m["title"]
        for m in json.loads(gh("api", f"repos/{repo}/milestones?state=all&per_page=100"))
    }
    for title, desc in MILESTONES:
        if title in existing:
            print(f"  milestone  {title} (exists)")
            continue
        gh("api", f"repos/{repo}/milestones", "-f", f"title={title}", "-f", f"description={desc}")
        print(f"  milestone  {title} (created)")


def existing_issues(repo: str) -> dict[str, int]:
    out = gh("issue", "list", "--repo", repo, "--state", "all", "--limit", "1000", "--json", "number,title")
    return {i["title"]: i["number"] for i in json.loads(out)}


def create_issue(repo: str, issue: Issue, body: str) -> int:
    args = ["issue", "create", "--repo", repo, "--title", issue.title, "--body-file", "-",
            "--milestone", issue.phase]
    for label in labels_for(issue):
        args += ["--label", label]
    url = gh(*args, input_text=body)
    return int(url.rstrip("/").rsplit("/", 1)[-1])


def edit_issue_body(repo: str, number: int, body: str) -> None:
    gh("issue", "edit", str(number), "--repo", repo, "--body-file", "-", input_text=body)


def add_to_project(repo: str, owner: str, project: int, number: int) -> None:
    url = f"https://github.com/{repo}/issues/{number}"
    gh("project", "item-add", str(project), "--owner", owner, "--url", url)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def validate() -> None:
    seeds = {i.n for i in ISSUES}
    assert len(seeds) == len(ISSUES), "duplicate seed numbers"
    for i in ISSUES:
        assert i.kind in ("agent-ready", "needs-human"), i.n
        assert i.area in AREAS, i.n
        assert i.size in ("S", "M", "L"), i.n
        assert i.phase in {m[0] for m in MILESTONES}, i.n
        for d in i.deps:
            assert d in seeds and d < i.n, f"seed {i.n}: bad dependency {d}"
        for ref in REF_RE.findall(raw_body(i)):
            assert int(ref) in seeds, f"seed {i.n}: bad ref {ref}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True, help="OWNER/REPO")
    parser.add_argument("--dry-run", action="store_true", help="print what would be created; no gh calls")
    parser.add_argument("--project-owner", help="user or org that owns the Project board")
    parser.add_argument("--project-number", type=int, help="Project board number (from its URL)")
    args = parser.parse_args()

    # Windows consoles and redirected output may not default to UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    validate()

    if args.dry_run:
        fake = {i.n: i.n for i in ISSUES}  # pretend seed N becomes #N
        for issue in ISSUES:
            body, _ = render_refs(raw_body(issue), fake)
            print("=" * 78)
            print(f"#{issue.n}  {issue.title}")
            print(f"labels: {', '.join(labels_for(issue))}   milestone: {issue.phase}")
            print("-" * 78)
            print(body)
        print("=" * 78)
        print(f"{len(LABELS)} labels, {len(MILESTONES)} milestones, {len(ISSUES)} issues (dry run)")
        return

    print("Labels")
    ensure_labels(args.repo)
    print("Milestones")
    ensure_milestones(args.repo)

    print("Issues")
    by_title = existing_issues(args.repo)
    numbers: dict[int, int] = {}
    needs_second_pass: list[Issue] = []
    for issue in ISSUES:
        if issue.title in by_title:
            numbers[issue.n] = by_title[issue.title]
            print(f"  #{numbers[issue.n]}  {issue.title} (exists, left unchanged)")
            continue
        body, resolved = render_refs(raw_body(issue), numbers)
        numbers[issue.n] = create_issue(args.repo, issue, body)
        print(f"  #{numbers[issue.n]}  {issue.title}")
        if not resolved:
            needs_second_pass.append(issue)

    # Forward references (e.g. "out of scope: #14") can only be filled in once
    # every issue exists, so re-render those bodies now.
    for issue in needs_second_pass:
        body, _ = render_refs(raw_body(issue), numbers)
        edit_issue_body(args.repo, numbers[issue.n], body)
        print(f"  #{numbers[issue.n]}  references updated")

    if args.project_owner and args.project_number:
        print("Project board")
        for issue in ISSUES:
            add_to_project(args.repo, args.project_owner, args.project_number, numbers[issue.n])
        print(f"  added {len(ISSUES)} issues to project {args.project_number}")

    print("Done.")


if __name__ == "__main__":
    main()
