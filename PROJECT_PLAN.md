# Personal News Feed — Project Plan

> Repo: [`4OH4/news-bot`](https://github.com/4OH4/news-bot) (public, MIT). Python package: `newsfeed`.
> Working title. This document is the source of truth for the project's aims, requirements, architecture and phased delivery. It is written to be loaded into Claude Code as context alongside `CLAUDE.md`.

---

## 1. Aim

Build a self-hosted, personalised news feed that replaces the Google Discover widget on Android. It should:

1. **Curate** a ranked list of articles from third-party sources, tailored to my interests.
2. **Extract** the core content of each article so it can be read in a clean, fast reader view with no pop-ups, cookie banners, trackers or ads.
3. **Remain fully under my control**, including sources, ranking logic, data and hosting.

## 2. Motivations

- **Control:** decide exactly which sources, topics and outlets appear, with hard rules ("never show X", "always surface Y") rather than opaque inference.
- **Reading experience:** existing feeds link out to pages full of consent dialogs, ads and autoplay video. A reader view removes all of that.
- **Incentives:** optimise for what I value (depth, novelty, fewer duplicates), not engagement or ad revenue.
- **Privacy:** my reading history stays in infrastructure I own.
- **Learning:** a practical project for exploring extraction, ranking/embeddings, GCP serverless, and multi-agent development with Claude Code.

## 3. Scope

### In scope
- Single user (me), with possible light multi-device use.
- Ingestion from RSS/Atom, Google News topic RSS, and open aggregator endpoints (e.g. Hacker News, Reddit JSON).
- Server-side article extraction to clean HTML/Markdown.
- Rule-based ranking first, then embedding-based personalisation driven by feedback.
- A mobile-first reader client (PWA first, Android widget later).

### Out of scope / non-goals
- Bypassing paywalls or circumventing access controls.
- Public redistribution of extracted content. This is for personal use only. Because the repo is public, no third-party article content (page snapshots, extracted text) is ever committed to it.
- Multi-tenant or SaaS features.
- Matching Google's web-scale discovery. We borrow breadth via topic feeds and aggregators instead.

## 4. Key Requirements

### Functional
| ID | Requirement |
|----|-------------|
| FR-1 | Configure sources (feeds, topic queries, aggregators) via a version-controlled config file. |
| FR-2 | Fetch all sources on a schedule (target: every 30–60 min). |
| FR-3 | Normalise items into a common article model (URL, canonical URL, title, source, published time, summary). |
| FR-4 | Deduplicate by canonical URL; later, cluster near-duplicate stories across outlets. |
| FR-5 | Extract main content (title, author, date, body, lead image) from article pages. |
| FR-6 | Fall back to a headless browser for JavaScript-rendered pages, and record extraction failures with a reason. |
| FR-7 | Score and rank articles by interest rules (topics, keywords, source boosts and mutes). |
| FR-8 | Serve a ranked feed and individual cleaned articles to a client. |
| FR-9 | Capture feedback (opened, read time, like, dismiss, mute source) and feed it into ranking. |
| FR-10 | (Later) Embedding-based relevance scoring from a learned interest profile. |
| FR-11 | (Later) Optional LLM summaries, tags, and story clustering. |
| FR-12 | (Later) Android home-screen widget and optional push for high-scoring stories. |

### Non-functional
| ID | Requirement |
|----|-------------|
| NFR-1 | **Portable:** processing runs as a plain Python Docker container. Cloud-specific code sits behind adapters. |
| NFR-2 | **Low cost:** target near-zero monthly spend at single-user scale, with budget alerts configured. |
| NFR-3 | **Polite fetching:** respect robots.txt, use a rate limit per domain, send an identifiable User-Agent, and use conditional GETs (ETag/Last-Modified). |
| NFR-4 | **Fast client:** the feed loads from pre-built data, with no cold-start wait on open. |
| NFR-5 | **Observable:** structured logs plus per-run metrics (items fetched, extraction success rate by domain, run duration). |
| NFR-6 | **Secure:** only I can access the API and client. Secrets live in Secret Manager. |
| NFR-7 | **Testable:** extraction quality is measured against a fixed evaluation set, not judged ad hoc. |

## 5. Architecture

### 5.1 Overview

```
Cloud Scheduler
      │  (cron trigger)
      ▼
Cloud Run Job  ── Python container ────────────────────────────┐
  1. ingest     fetch feeds/aggregators → normalised items     │
  2. dedupe     canonical URL + (later) story clustering       │
  3. extract    Trafilatura → fallback extractors → Playwright │
  4. rank       rules → (later) embeddings + feedback          │
  5. publish    write articles + build feed snapshot           │
      │                                                        │
      ▼                                                        │
Firestore (articles, sources, profile, feedback, run stats)    │
Cloud Storage (clean article HTML, images, feed.json snapshot) ◄┘
      │
      ▼
Cloud Run Service (thin API)  and/or  Firebase Hosting (PWA)
      │
      ▼
Phone: PWA → later native Android app + widget
      │
      └── feedback events → API → Firestore → next pipeline run
```

### 5.2 Components

| Component | Responsibility | Tech (agnostic) | GCP deployment |
|-----------|---------------|-----------------|----------------|
| **Ingest** | Fetch and parse feeds and aggregators; normalise items | Python, `httpx`, `feedparser` | Cloud Run Job |
| **Extract** | Fetch pages, extract main content, sanitise HTML | Trafilatura (primary), readability-lxml / newspaper4k (fallbacks), Playwright (JS fallback) | Same job, or a separate job/service if Playwright makes the image heavy |
| **Rank** | Score articles via rules, then embeddings and feedback | Python; embeddings via a provider interface | Same job; Vertex AI embeddings (optional) |
| **Store** | Persist articles, profile, feedback, run stats | `Repository` interface with Firestore and SQLite/local-file adapters | Firestore |
| **Blob store** | Clean HTML, images, feed snapshot | `BlobStore` interface with GCS and local-filesystem adapters | Cloud Storage |
| **API** | Serve feed and articles; accept feedback | FastAPI | Cloud Run Service |
| **Client** | Reader UI, feed list, feedback capture, offline cache | PWA first (framework TBD), Kotlin/Compose later | Firebase Hosting |
| **Scheduler** | Trigger pipeline runs | cron | Cloud Scheduler |
| **Secrets / auth** | API keys, client auth | env vars locally | Secret Manager, Firebase Auth or IAP |
| **CI/CD** | Test, build, deploy | GitHub Actions | Artifact Registry + Workload Identity Federation (no long-lived keys) |
| **Infra** | Reproducible environments | Terraform (preferred) or scripted `gcloud` | — |

### 5.3 Design principles
- **Ports and adapters.** Core logic depends on interfaces (`Repository`, `BlobStore`, `Embedder`, `Fetcher`), never on GCP SDKs directly. Local development uses SQLite and the local filesystem.
- **Pipeline stages are pure and composable.** Each stage reads inputs and writes outputs through interfaces, so stages can be developed and tested independently. This also makes them well suited to parallel agent work.
- **Pre-compute for the client.** The pipeline publishes a ranked `feed.json` snapshot, so the client rarely needs a live API call to show the feed.
- **Config as code.** Sources, interest rules and mutes live in `config/` as YAML in the repo.

### 5.4 Core data model (initial sketch)

The authoritative definitions are the Pydantic models in `backend/src/newsfeed/models.py` (#6); this sketch is kept for orientation.

```
Source       id, type (rss|google_news|hn|reddit), url/query, weight, enabled
Article      id (hash of canonical_url), canonical_url, url, source_id, title,
             author, published_at, fetched_at, summary, lead_image,
             extraction: {status, method, error, error_type, word_count,
                          attempts, last_attempt_at, declared_canonical},
             content_ref (blob path), score, score_breakdown, cluster_id
Feedback     id, article_id, event (open|read|like|dismiss|mute_source),
             value (e.g. read seconds), created_at
Profile      empty placeholder; learned fields such as embedding centroids
             are designed in Phase 4 (interest rules are config, in
             config/interests.yaml, not part of the profile)
SourceState  source_id, etag, last_modified, last_fetched_at, last_status,
             consecutive_failures, last_error, last_success_at
RunStats     run_id, started_at, finished_at, counts per stage,
             extraction success by domain, stage durations, failures by
             reason, median word count by domain
```

## 6. Phased Delivery

Each phase has a clear exit criterion. Later phases may be re-planned based on what earlier ones teach us.

### Phase 0: Foundations
- Repository scaffold, Python project (`uv`), Dockerfile, lint/format/type-check (`ruff`, `mypy` or `pyright`), `pytest`.
- `CLAUDE.md` with conventions; issue and PR templates; GitHub Project board.
- CI: lint, test and Docker build on every PR.
- **Exit:** the empty pipeline runs locally in Docker and CI is green.

### Phase 1: Extraction spike (backend, local only), the key risk test
- Ingest from 20–30 real feeds I actually read.
- Build an **extraction evaluation set**: about 100 URLs across those sources, each with a hand-checked expectation (title, a body text snippet, approximate word count, whether it is paywalled).
- Implement the extractor chain: Trafilatura → fallbacks → Playwright for flagged domains.
- An evaluation harness reports success rate, failure reasons and per-domain results, and compares extractors.
- Output: local SQLite plus HTML files, and a simple local HTML index for eyeballing the results.
- **Exit:** at least 85–90% clean extraction on the evaluation set for non-paywalled articles, with failures understood and categorised. A go/no-go decision on the concept is made here.
- **Status (2026-10-17): done, go.** 98.9% clean on the evaluation set; decision and Phase 2 scope changes in ADR-0003, failure analysis in `docs/phase1-review.md`.

### Phase 2: Pipeline on GCP and rule-based ranking
- Terraform (or scripts) for the project, Artifact Registry, Cloud Run Job, Scheduler, Firestore, GCS, Secret Manager, and a budget alert.
- Firestore and GCS adapters; GitHub Actions deployment via Workload Identity Federation.
- Dedupe by canonical URL; rule-based scoring from `config/interests.yaml`.
- Publish `feed.json` snapshot; run stats and structured logging.
- Added by ADR-0003: paywall and teaser detection; publish non-clean items as headline, summary and link; measure live extraction from GCP against the Phase 1 baseline.
- **Exit:** the pipeline runs hourly unattended for a week within budget, and the feed snapshot is accessible.
- **Milestone demo:** open the published `feed.json` from the cloud bucket, or a preview built from it, and see ranked articles from the latest hourly run, with that run's stats and the week's cost so far.

### Phase 3: Reader client (PWA)
- Feed list, article reader view (typography, dark mode), offline cache.
- Feedback capture through a thin API (Cloud Run + FastAPI); authentication restricted to me.
- Installable on the Android home screen.
- **Exit:** I use it daily in place of the Google feed for two weeks.
- **Milestone demo:** install the PWA on my Android phone, read today's feed, open an article, like one, and read it again offline.

### Phase 4: Personalisation
- Embedding provider interface (Vertex AI or a local model); interest centroids built from liked and read articles.
- Optional bootstrap from a Google Takeout export of search and YouTube history.
- Hybrid score combining rules, similarity, freshness, source weight and a diversity penalty, with a transparent score breakdown per article.
- Near-duplicate story clustering.
- **Exit:** a measurable lift in open and read rate versus rules-only (simple A/B by day or by feed slot).
- **Milestone demo:** read the same day's feed with and without personalisation, open one article's score breakdown, and see the A/B results so far.

### Phase 5: Native Android and widget (optional)
- Kotlin/Compose app or Trusted Web Activity wrapper; a home-screen widget showing the top stories.
- Optional FCM push for high-scoring breaking stories.
- **Milestone demo:** install the app and add the widget to the home screen, then open a top story from it.

### Phase 6: Enhancements (backlog of ideas)
- LLM summaries and tags; a "why am I seeing this" view; a "what's trending" discovery layer; source discovery suggestions; full-text search.
- **Milestone demo:** each enhancement chosen for the phase, shown in the reader client on today's feed.

## 7. Extraction Evaluation Approach

Extraction quality is the riskiest assumption, so it gets a dedicated, repeatable harness:
- `eval/urls.yaml` lists each URL with source, expected title, a body snippet that must appear, an expected word-count range, and `paywalled: bool`.
- Saved HTML **snapshots** (fetch each page once) so evaluation is deterministic and doesn't hit sites repeatedly. Snapshots are third-party content, so they live **outside the public repo** in `NEWSFEED_FIXTURES_DIR` (default `~/.newsfeed/eval-fixtures`), shared by all local worktrees; `eval/fixtures/` is git-ignored as a safeguard. They can be backed up to a private GCS bucket in Phase 2.
- `make eval` runs every extractor against the snapshots and outputs a per-domain matrix and an overall score. It runs locally, not in CI, because CI has no snapshots.
- Unit tests use **synthetic** HTML/XML fixtures in `backend/tests/fixtures/` that mimic real page structures with invented text.
- The live-fetch evaluation is run separately and occasionally, to catch bot-blocking and JavaScript rendering issues.
- Results are committed to `eval/results/` so the trend can be tracked over time. Each run also writes a git-ignored per-entry log to `eval/logs/` (JSON Lines: status, chain steps and check outcomes for each entry and extractor, no article text), for tracing a score back to the entries behind it.

## 8. Repository Structure (proposed)

```
/
├── CLAUDE.md                 # conventions & guidance for Claude Code agents
├── PROJECT_PLAN.md           # this document
├── AUTOMATION.md             # how the project is built: phases, agents, merge rules
├── REVIEW.md                 # failure patterns from real defects
├── docs/
│   ├── adr/                  # architecture decision records (0001-*.md)
│   ├── phases/               # phase designs: phase-<n>.md
│   └── milestones/           # milestone reports: phase-<n>.md
├── config/
│   ├── sources.yaml
│   ├── extraction.yaml       # per-domain extractor overrides
│   ├── url_params.yaml       # per-domain tracking-parameter rules for canonical URLs
│   └── interests.yaml
├── backend/
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── src/newsfeed/
│   │   ├── models.py         # shared data model (the contract between stages)
│   │   ├── ports.py          # Repository, BlobStore, Fetcher, Extractor, SourceIngester (+ Embedder later)
│   │   ├── fetching/         # shared polite HTTP fetcher
│   │   ├── ingest/
│   │   ├── extract/
│   │   ├── rank/
│   │   ├── publish/
│   │   ├── adapters/         # sqlite, localfs, firestore, gcs, vertex
│   │   ├── api/              # FastAPI (Phase 3)
│   │   └── cli.py            # `newsfeed run|ingest|extract|eval`
│   └── tests/
├── eval/
│   ├── urls.yaml
│   ├── boilerplate.txt       # phrases that indicate unclean extraction
│   ├── results/
│   └── logs/             # per-run detailed logs (git-ignored)
├── client/                   # PWA (Phase 3)
├── infra/                    # Terraform
├── scripts/
│   └── bootstrap_github.py   # creates labels, milestones and seed issues
└── .github/
    ├── workflows/
    ├── ISSUE_TEMPLATE/
    └── pull_request_template.md
```

## 9. Development Workflow and Multi-Agent Strategy

### 9.1 Where the backlog lives
**GitHub Issues + a GitHub Project board** is the backlog. It is visible to me, to Claude Code locally (via the `gh` CLI) and to Claude in GitHub Actions, and every issue links naturally to its branch and PR.

- **Project board columns:** `Backlog` → `Ready` → `In progress` → `Agent review` → `Human review` → `Done`. A PR sits in `Agent review` while agents review and rework it, and moves to `Human review` when it waits only for me: a Tier C PR, once the delivery manager has posted its review.
- **Milestones** = phases (`Phase 0`, `Phase 1`, ...).
- **Labels:**
  - `area:ingest|extract|rank|store|api|client|infra|eval|docs`
  - `size:S|M|L` (L means split it before an agent picks it up)
  - `agent-ready`: fully specified and safe for an agent to take unsupervised
  - `needs-human`: requires a decision, credentials, or judgment
  - `blocked`, plus a "Depends on #N" line in the issue body
- **ADRs** in `docs/adr/` record decisions so agents don't re-litigate them.

### 9.2 Issue template (agent-ready)
Every `agent-ready` issue must contain:
- **Context:** why the work matters, written for me first. It opens with a short plain-language paragraph (the problem or gap today, what the change makes possible, and what it unblocks), followed by links to the relevant plan section, requirement IDs and ADRs.
- **Task:** what to build. It opens with a **Summary** of two to four sentences (what is being built and how it fits the pipeline, or the workflow for infra and docs issues), followed by **Details**, the precise spec.
- **Acceptance examples:** concrete given/when/then cases in plain language, which the test author turns into tests.
- **Acceptance criteria:** testable checkboxes.
- **Files / modules in scope**, and anything explicitly *out of scope*, to limit collisions between agents.
- **Interfaces touched**, noting any change to `models.py` or `ports.py`.
- **Dependencies:** "Depends on #N".
- **How to verify:** the commands to run (`make test`, `make eval`, ...).

### 9.3 Making work parallelisable
- **Contracts first.** `models.py` and `ports.py` are defined and merged early, by a human or a single agent. After that, stages and adapters can be built in parallel against stable interfaces.
- **Vertical slices by module.** Issues are scoped to one `area`/directory wherever possible, so parallel agents rarely edit the same files.
- **Contract changes are serialised.** Any change to `models.py`/`ports.py` is its own issue and Tier B PR, merged before the dependent work.
- **Fixtures over network.** Tests use synthetic fixtures and fakes, so agents can work offline and deterministically. Evaluation snapshots sit in a shared folder outside the repo, so parallel worktrees all see the same set.

### 9.4 How agents run
The way of working is in `AUTOMATION.md`, which replaced the lead-and-workers model on 2026-10-31. In brief: I agree each phase's goal, and agents build the phase. A lead session owns delivery, an architect turns the phase into a design and issues, and a delivery manager runs batches of issues through test author, worker, CI and an independent reviewer, then merges through `scripts/tier.py`. I review a working demo and the recorded decisions at each milestone.

Claude in GitHub Actions is still available: an `@claude` mention on an issue or PR handles small, well-specified follow-ups.

### 9.5 What stays human
`AUTOMATION.md` §6 is the full list. Agents make reversible decisions and record them for the milestone review. I set the intent, sign off each milestone by merging its report, merge Tier C PRs, and handle what agents can't: credentials, cloud project setup, IAM, billing, paid services, and anything that can't be reversed. I can revert any merge, or add `hold` to stop one. I don't edit files unless I'm fixing a mistake.

### 9.6 CLAUDE.md should cover
- Project summary and a pointer to this plan.
- Commands: setup, test, lint, eval, run locally, build Docker image.
- Conventions: typing, logging, error handling, test layout, branch and commit naming.
- Architecture rules: no GCP SDK imports outside `adapters/`; models and ports are contracts.
- Workflow: pick only `Ready` + `agent-ready` issues, stay within listed files, update docs and tests, and open a PR linking the issue.

## 10. Initial Backlog (seed issues)

The full issue bodies (task, acceptance criteria, scope, verification) live in `scripts/bootstrap_github.py`, which creates these issues, labels and milestones on GitHub. Once the issues exist, GitHub is the source of truth and this list is a summary.

**Phase 0**
1. Scaffold repo structure, `uv` project and Dockerfile. `infra` `S` `agent-ready`
2. Add ruff, mypy, pytest, Makefile targets and pre-commit. `infra` `S` `agent-ready` (depends on 1)
3. CI workflow: lint, test, Docker build on PR. `infra` `S` `agent-ready` (depends on 2)
4. Commit `CLAUDE.md` and templates; set up labels, board and branch protection. `docs` `S` `needs-human`
5. ADR-0001: ports and adapters, storage and concurrency model. `docs` `S` `needs-human`

**Phase 1**
6. Define `models.py` (Source, Article, ExtractionResult, Feedback, RunStats). `store` `S` `needs-human` (depends on 1)
7. Define `ports.py` (Repository, BlobStore, Fetcher, Extractor, SourceIngester) and in-memory fakes. `store` `S` `needs-human` (depends on 5, 6)
8. SQLite Repository and local-filesystem BlobStore adapters, with shared contract tests. `store` `M` `agent-ready` (depends on 6, 7)
9. Polite HTTP fetcher (rate limit, robots.txt, conditional GET, retries). `ingest` `M` `agent-ready` (depends on 7)
10. RSS/Atom ingester plus `config/sources.yaml` loader. `ingest` `M` `agent-ready` (depends on 6, 7; uses `FakeFetcher`, so it runs in parallel with 9)
11. Google News topic RSS and Hacker News ingesters. `ingest` `M` `agent-ready` (depends on 10)
12. Canonical URL resolution and dedupe. `ingest` `S` `agent-ready` (depends on 6)
13. Trafilatura extractor with HTML sanitisation. `extract` `M` `agent-ready` (depends on 6, 7)
14. Fallback extractors and extractor chain with per-domain overrides (`config/extraction.yaml`). `extract` `M` `agent-ready` (depends on 13)
15. Playwright renderer for flagged domains (separate image target). `extract` `M` `agent-ready` (depends on 13)
16. Curate sources and the evaluation URL list (about 100 URLs). `eval` `M` `needs-human`
17. Fixture snapshot tool. `eval` `S` `agent-ready` (depends on 9)
18. Evaluation harness and per-domain report (`make eval`). `eval` `M` `agent-ready` (depends on 13, 17)
19. CLI: `newsfeed run` wiring ingest → extract → store. `ingest` `M` `agent-ready` (depends on 8, 10, 12, 13)
20. Local static HTML preview of extracted articles. `client` `S` `agent-ready` (depends on 19)
21. Phase 1 go/no-go review. `eval` `S` `needs-human` (depends on 16, 18, 19, 20)

Once 6 and 7 are merged, issues 8–10, 12 and 13 can run in parallel. 11, 14, 15 and 17 follow as soon as their single dependency lands, and 16 can be done by me at any time.

## 11. Risks and Open Questions

| Risk / question | Mitigation / next step |
|-----------------|------------------------|
| Extraction quality varies by site | Phase 1 evaluation harness; per-domain extractor overrides; accept that some sites will only show a summary and link |
| Datacenter IPs blocked by Cloudflare or publishers | Measure in Phase 2; polite fetching; Playwright; drop or replace problem sources |
| Paywalled content | Show feed summary plus link; flag as paywalled; never bypass |
| Playwright bloats the container and cold starts | Separate image or job for JavaScript-heavy extraction, invoked only for flagged domains |
| Firestore lock-in | Repository interface; SQLite adapter kept working in CI |
| Cost creep (Vertex AI, storage) | Budget alerts; cache embeddings; image size limits and retention policy |
| Copyrighted content leaking into the public repo | Snapshots outside the repo; `eval/fixtures/` and `data/` git-ignored; synthetic test fixtures; rule in `CLAUDE.md` |
| Occasional commits from Windows introducing CRLF line endings | `.gitattributes` enforces LF; development itself happens on Ubuntu |
| Agents colliding on shared files | Contracts first; area-scoped issues; serialised contract changes |
| **Open:** PWA framework choice | Decide at the start of Phase 3 (ADR) |
| **Decided:** Terraform vs scripted gcloud | Terraform, after a one-off `gcloud` bootstrap of the project and state bucket ([ADR-0004](docs/adr/0004-infrastructure-as-code.md)); agents apply changes inside a limited development scope, and production is out of scope for now ([ADR-0007](docs/adr/0007-agent-infrastructure-scope.md)) |
| **Decided:** content retention period | Metadata and run stats kept indefinitely; article content deleted after 30 days by a Cloud Storage lifecycle rule, then shown as headline and link ([ADR-0005](docs/adr/0005-content-retention.md)) |
| **Open:** embedding provider (Vertex AI vs local) | Decide in Phase 4 based on cost and quality |
