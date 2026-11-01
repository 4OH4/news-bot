# CLAUDE.md

Guidance for Claude Code agents working in this repository. `PROJECT_PLAN.md` says what is being built. `AUTOMATION.md` describes how it is built and why: the phases, agents, decisions and merge rules. Agents don't need to read it to do their jobs. `.claude/agents/` and `.claude/skills/` hold the working briefs for each role.

## Project summary

A self-hosted, personalised news feed. A Python pipeline ingests articles from feeds and aggregators, extracts clean article content, ranks it against my interests, and publishes a feed for a mobile reader client. Development is local-first, and the pipeline runs on GCP (Cloud Run Jobs, Scheduler, Firestore, Cloud Storage).

The current phase and its goals are listed in `PROJECT_PLAN.md` §6. Don't build ahead of the current phase unless an issue asks you to.

## Commands

`make` commands run from the repo root. The Makefile sets `NEWSFEED_CONFIG_DIR` and `NEWSFEED_DATA_DIR` to absolute paths, so the pipeline finds `config/` wherever it's run from.

**Environment:** development happens on Ubuntu, and CI and the containers are Linux too. Prerequisites: `make`, `git`, `uv`, Docker Engine and `gh`, plus Terraform (1.6 or later) and `gcloud` for the infrastructure in `infra/`. Use `pathlib` for paths in Python rather than string concatenation.

```bash
make setup        # install deps with uv, install pre-commit hooks
make lint         # ruff check + ruff format --check + type check
make fmt          # auto-format
make test         # pytest (unit tests, no network)
make eval         # run extraction evaluation against saved fixtures
make run          # run the full pipeline locally (SQLite + local filesystem)
make docker       # build the backend Docker image
```

The CLI entry point is `newsfeed` (`backend/src/newsfeed/cli.py`). The uv project lives in `backend/`, so run these from `backend/`, or from the root with `uv run --project backend newsfeed ...`:

```bash
uv run newsfeed run            # ingest → extract → rank → publish
uv run newsfeed ingest         # single stage
uv run newsfeed extract --url <url>   # debug extraction for one page
uv run newsfeed preview        # static HTML preview of extracted articles
uv run newsfeed eval
```

Before opening a PR, `make lint` and `make test` must both pass. Subagents can't run `make eval`, so the lead runs it for each PR that touches `extract/` and posts the scores (`AUTOMATION.md` §6).

## Architecture rules

These are hard rules. If a task seems to require breaking one, stop and say so in the issue or PR instead of working around it.

1. **Ports and adapters.** Core code depends only on the interfaces in `backend/src/newsfeed/ports.py`. Cloud SDKs (`google.cloud.*`, `firebase_admin`, Vertex AI) may only be imported inside `backend/src/newsfeed/adapters/`.
2. **`models.py` and `ports.py` are contracts.** Don't change them as a side effect of other work. A contract change is its own issue and PR, Tier B, with a `Confirm later` entry. If it needs a migration of stored data that can't be recreated, it is stop-the-line (`AUTOMATION.md` §6).
3. **Stages are independent.** `ingest`, `extract`, `rank` and `publish` communicate through the `Repository` and `BlobStore` interfaces, never by importing each other's internals.
4. **Local adapters must keep working.** SQLite and local-filesystem adapters are the default for development and CI. Never make a cloud service a requirement for running tests.
5. **Config lives in `config/`.** Sources and interest rules are YAML files in the repo, not code constants or database rows.
6. **Polite fetching only.** All HTTP goes through the shared `Fetcher`, which applies robots.txt checks, per-domain rate limits, an identifiable User-Agent, conditional GETs and retries. Never bypass paywalls or access controls.

## Cost of failure

Use this to judge how much a problem matters. The human confirms it at each milestone (`AUTOMATION.md` §6). Confirmed for Phase 2 by merging PR #151.

- **Stage:** development. There are no users, not even the owner yet, and no release to roll back to.
- **Cheap:** a duplicate, stale or missing feed item, poor ranking, a failed run that the next run recovers, and a data problem that can be repaired or recreated. These are slightly annoying, and can be fixed later.
- **Matters:** harm to third-party sites, including robots.txt, paywalls and rate limits; third-party content in the public repo; a bill; a leaked secret; and losing data that can't be recreated.

Put effort where it matters. Report cheap problems in a line, with the fix or recovery step, and don't escalate them or rework a PR for a rare case. This never relaxes any other rule in this file, including the stop-the-line list.

## Code conventions

- Python 3.12+, managed with `uv`. Add dependencies with `uv add`, never by editing the lockfile by hand.
- Full type hints on all public functions. Code must pass the type checker with no new ignores unless justified in a comment.
- Data models use Pydantic (or dataclasses where validation isn't needed); follow whatever `models.py` uses.
- Use structured logging through the project logger (`newsfeed.logging.get_logger`), not `print`. Include context such as `source_id`, `url` and `stage`.
- Errors: catch them at stage boundaries so one bad article or feed never fails a whole run. Record failures on the item (e.g. `extraction.status`, `extraction.error`) and continue.
- Keep functions small and side-effect-free where practical; push I/O to the edges.
- Docstrings on public modules, classes and functions. Comments explain *why*, not *what*.

## Testing

- Tests live in `backend/tests/`, mirroring the `src/` layout.
- **No network in tests.** Use fixtures in `backend/tests/fixtures/` and the fakes in `backend/tests/fakes.py`. Mark any unavoidable live test with `@pytest.mark.live`; these are excluded by default.
- New behaviour needs tests; bug fixes need a regression test. `feat` and `fix` work is test-first, with the tests written by a separate agent (`AUTOMATION.md` §7).
- **Test fixtures are synthetic.** This repo is public, so fixtures under `backend/tests/fixtures/` are hand-written HTML/XML that mimic real page and feed *structures* (cookie banners, lazy-loaded images, JS shells, Atom quirks) with invented text. Never paste real article text into them.
- **Evaluation snapshots live outside the repo.** Real-page snapshots are stored in `NEWSFEED_FIXTURES_DIR` (default `~/.newsfeed/eval-fixtures`), shared by every worktree and never committed; `eval/fixtures/` is git-ignored as a safeguard. Don't hand-edit snapshots; re-snapshot with `newsfeed eval snapshot --refresh`. If the snapshot directory is empty or missing, `make eval` reports entries as "not snapshotted", so say so in the PR rather than treating it as a failure. Evaluation results (`eval/results/`) contain metrics only and are committed. Each run also writes a detailed log to `eval/logs/<UTC timestamp>-<commit>.jsonl` (git-ignored, never committed): one JSON line per entry with its URL, snapshot state, and each extractor's status, error, chain steps and check outcomes. Read it to see which entries and checks are behind a score.
- `REVIEW.md` lists the failure patterns from real defects here. Read it before writing code or tests.

## Workflow

`AUTOMATION.md` has the full model. These are the rules every agent needs.

### Writing issues
Agents write most new issues. The human reads an issue first to understand what is being built and why, then may read the PR against it, so write for that reader before the implementing agent. `gh issue create` doesn't apply the issue forms, so follow `.github/ISSUE_TEMPLATE/` section by section, and:
- **Context** opens with a short plain-language paragraph that a reader new to the area can follow: the problem or gap today, what the change makes possible (in terms of the feed where you can), and why it's needed now (what it unblocks). References (FR/NFR IDs, PROJECT_PLAN.md sections, ADRs, related issues) come after it, not instead of it.
- **Task** opens with **Summary:**, two to four sentences on what is being built and how it fits the pipeline (which stage, what goes in and what comes out, and the main pieces) or, for infra and docs issues, the workflow. The precise spec follows under **Details:**.
- **Acceptance examples** are concrete given/when/then cases in plain language. They describe behaviour, and the issue names the interfaces, so the test author and worker can work independently.
- For `needs-human` issues, "What's needed" opens with one or two sentences on the decision or task and what depends on the outcome.
- Explain internal names and jargon on first use, e.g. "the Algolia HN Search API (the public search service for Hacker News)".
- Keep the precision: acceptance criteria, scope, interfaces and verification stay as specific as before.
- Don't split work into a sequence of small dependent issues in the same module just to keep each one small. Where a chain does exist, it can go to one test author and one worker as a single PR.

### Picking up work
- Work only on what you've been assigned. Check "Depends on" in the issue, and don't start if a dependency isn't merged.
- Stay within the files and modules listed in the issue's scope. If you need to change something out of scope, stop and report it rather than expanding the change.

### Branches and worktrees
- One issue per branch, named `issue-<number>-<short-slug>` (e.g. `issue-13-trafilatura-extractor`). The exception is a chain, with one branch and one PR named after the first issue.
- Each agent works in its own git worktree, under `.claude/worktrees/` (git-ignored). Never `git add` them.
- Rebase on `main` before opening a PR.
- In a worktree, install dependencies with `uv sync` (in `backend/`), **not** `make setup`. `pre-commit install` writes a hook into the shared `.git/hooks/`, which then points at that worktree's environment and breaks commits everywhere once the worktree is removed.

### Commits
- Conventional commits: `feat:`, `fix:`, `test:`, `refactor:`, `docs:`, `chore:`, `ci:`, with an optional scope, e.g. `feat(extract): add trafilatura extractor`.
- Small, coherent commits. Don't mix formatting-only changes with logic changes.

### Pull requests
- Follow `.github/pull_request_template.md`. The top is a header line (`Closes #<issue>`, the risk tier, the merge order), any `Confirm later` entries, Context and Task (copied from the issue), a Summary of two or three sentences and "For human review", in that order. The ticked acceptance criteria, verification (commands and results), design notes and follow-ups go in the collapsed sections below.
- Update docs (`CLAUDE.md`, `PROJECT_PLAN.md`, ADRs, README) if your change affects them.
- The tier comes from the diff: `python3 scripts/tier.py classify <PR>`. The human merges Tier C. The delivery manager merges Tier A and B, and only through `python3 scripts/tier.py merge`. Never merge to `main` any other way. One size limit applies to every PR: 400 source lines and 1,500 lines in total, not counting generated files.

### Who decides what
Agents decide unless the change is Tier C or stop-the-line (`AUTOMATION.md` §6). For a reversible decision, choose sensibly, add a `**Confirm later:**` entry to the PR header with the reason and how to undo it, and carry on. Stop and report back when the work needs:
- credentials, secrets, or a GitHub setting or app install;
- a cloud change that an agent can't apply itself;
- a new paid service, or a dependency with an incompatible licence;
- a migration or deletion of stored data that can't be recreated, or a live retention rule that deletes content;
- taking on a user who is not also a developer;
- anything that could breach robots.txt, paywalls or other third-party limits, or publish third-party article content in the public repo;
- a change of project goal, or a design choice that can't be reversed and that the plan doesn't settle.

### The human's role
Agents do all the preparation, and the human uses a working demo at each milestone and confirms the decisions.
- Never ask the human to edit files, fill in templates, tick boxes or make follow-up commits. If a PR needs a change, including feedback the human gives in a comment, an agent makes it.
- Approval is the merge. Declining or commenting is the rejection or the request for changes.
- Only ask for actions agents can't do, and give the shortest exact steps, such as a URL or a single command.

### Board and GitHub
The Project board (https://github.com/users/4OH4/projects/1) is the source of truth for status. Use `scripts/board.py` rather than editing cards by hand:
- `python3 scripts/board.py show`: issues grouped by status.
- `python3 scripts/board.py set <issue> "<status>"`: statuses are Backlog, Ready, In progress, Agent review, Human review, Done. "Agent review" means agents are still reviewing or reworking the PR; "Human review" means it waits only for the human.
- `python3 scripts/board.py promote [--dry-run]`: moves closed issues to Done, then moves open `agent-ready` issues from Backlog to Ready once every "Depends on #N" issue is closed. It never promotes `needs-human` issues.

**Shared files.** Several issues touch `backend/pyproject.toml`, `backend/uv.lock`, `cli.py`, `Makefile` and `config/sources.yaml`. Rebase on `main` just before pushing. Resolve `uv.lock` conflicts by taking `main`'s version and re-running `uv lock`, never by hand-editing.

**GraphQL budget.** GitHub allows 5,000 GraphQL points per user per hour, shared by every agent and `gh` itself (`gh pr view/edit/list` and `gh project` all use it). Prefer REST (`gh api repos/4OH4/news-bot/pulls/<n>`, `.../issues/<n>`), which has a separate allowance, for reading PRs and issues in loops. Don't call `board.py show` repeatedly: it prints the points used and the allowance left after each command. If it reports "GraphQL rate limit exhausted; resets at <time> UTC", wait or use REST; `gh project` shows the same failure as the misleading "unknown owner type".

## Things agents must not do

- Handle credentials, service account keys or secrets directly. Agents use only the short-lived, limited identity in `AUTOMATION.md` §6. Never commit secrets; use `.env` locally (git-ignored) and Secret Manager in the cloud.
- Change IAM, billing or budgets, or delete the project. `terraform` and `gcloud` are for resources inside the development scope only, and `terraform apply` runs only after the change is merged (ADR-0007).
- Touch production. Production starts when a user is not also a developer, and agents must stop and ask before that point.
- Change `eval/urls.yaml`, the scorer or `eval/boilerplate.txt` in the same PR as extraction code, or without a `Confirm later` entry showing why.
- Commit third-party article content: page snapshots, extracted article text or full feed dumps. This is a public repo; short quoted snippets in `eval/urls.yaml` are the only exception.
- Disable or weaken tests, lint rules or type checks to get CI passing.
- Add tracking, analytics or third-party scripts to the client.
- Use `--dangerously-skip-permissions`, or merge any way other than `scripts/tier.py merge`.

## Decisions

Lasting architecture decisions are recorded in `docs/adr/`. Read the relevant ADRs before working in an area. Agents write ADRs as decide-and-record work: status `Accepted`, Tier B, with a `Confirm later` entry that the human reviews at the milestone. Don't reverse a recorded decision without a new ADR. If you make a notable design choice within an issue, mention it in the PR so it can be captured as an ADR if needed.

## Key references

- `PROJECT_PLAN.md`: aims, requirements, architecture, phases
- `AUTOMATION.md`: the way of working; §13 says where everything lives
- `REVIEW.md`: failure patterns from real defects
- `docs/adr/`: decision records
- `docs/phases/`, `docs/milestones/`: phase designs and milestone reports
- `docs/feed.schema.json`, `docs/feed.example.json`: the `feed.json` contract between the publish stage and the client (ADR-0006)
- `config/sources.yaml`, `config/interests.yaml`: sources and ranking rules
- `config/extraction.yaml`: per-domain extractor overrides
- `config/url_params.yaml`: per-domain tracking-parameter rules for canonical URLs (evidence-backed only)
- `eval/`: extraction evaluation set (`urls.yaml`) and results; snapshots live in `NEWSFEED_FIXTURES_DIR`
- `.github/ISSUE_TEMPLATE/`: `agent-task.yml` and `needs-human.yml`, the shape every issue follows
- `scripts/`: `board.py`, `context.py`, `lead.py`, `tier.py`, `usage.py`, and `bootstrap_github.py`, which creates labels, milestones and seed issues
