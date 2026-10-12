# CLAUDE.md

Guidance for Claude Code agents working in this repository. Read this file and `PROJECT_PLAN.md` before starting any task.

## Project summary

A self-hosted, personalised news feed. A Python pipeline ingests articles from feeds and aggregators, extracts clean article content, ranks it against my interests, and publishes a feed for a mobile reader client. Development is local-first; production runs on GCP (Cloud Run Jobs, Scheduler, Firestore, Cloud Storage).

The current phase and its goals are listed in `PROJECT_PLAN.md` §6. Don't build ahead of the current phase unless an issue asks you to.

## Commands

`make` commands run from the repo root. The Makefile sets `NEWSFEED_CONFIG_DIR` and `NEWSFEED_DATA_DIR` to absolute paths, so the pipeline finds `config/` wherever it's run from.

**Environment:** development happens on Ubuntu, and CI and the containers are Linux too. Prerequisites: `make`, `git`, `uv`, Docker Engine and `gh`. Use `pathlib` for paths in Python rather than string concatenation.

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
uv run newsfeed eval
```

Before opening a PR, `make lint` and `make test` must both pass. If you touched `extract/`, also run `make eval` and include the before/after summary in the PR.

## Architecture rules

These are hard rules. If a task seems to require breaking one, stop and say so in the issue or PR instead of working around it.

1. **Ports and adapters.** Core code depends only on the interfaces in `backend/src/newsfeed/ports.py`. Cloud SDKs (`google.cloud.*`, `firebase_admin`, Vertex AI) may only be imported inside `backend/src/newsfeed/adapters/`.
2. **`models.py` and `ports.py` are contracts.** Don't change them as a side effect of other work. A contract change is its own issue labelled `needs-human`.
3. **Stages are independent.** `ingest`, `extract`, `rank` and `publish` communicate through the `Repository` and `BlobStore` interfaces, never by importing each other's internals.
4. **Local adapters must keep working.** SQLite and local-filesystem adapters are the default for development and CI. Never make a cloud service a requirement for running tests.
5. **Config lives in `config/`.** Sources and interest rules are YAML files in the repo, not code constants or database rows.
6. **Polite fetching only.** All HTTP goes through the shared `Fetcher`, which applies robots.txt checks, per-domain rate limits, an identifiable User-Agent, conditional GETs and retries. Never bypass paywalls or access controls.

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
- New behaviour needs tests; bug fixes need a regression test.
- **Test fixtures are synthetic.** This repo is public, so fixtures under `backend/tests/fixtures/` are hand-written HTML/XML that mimic real page and feed *structures* (cookie banners, lazy-loaded images, JS shells, Atom quirks) with invented text. Never paste real article text into them.
- **Evaluation snapshots live outside the repo.** Real-page snapshots are stored in `NEWSFEED_FIXTURES_DIR` (default `~/.newsfeed/eval-fixtures`), shared by every worktree and never committed; `eval/fixtures/` is git-ignored as a safeguard. Don't hand-edit snapshots; re-snapshot with `newsfeed eval snapshot --refresh`. If the snapshot directory is empty or missing, `make eval` reports entries as "not snapshotted", so say so in the PR rather than treating it as a failure. Evaluation results (`eval/results/`) contain metrics only and are committed.

## Workflow

### Picking up work
- Work only on issues in the `Ready` column labelled `agent-ready`, unless I've (or the lead agent has) assigned you something directly. See "Agent automation" below.
- Check "Depends on" in the issue. If a dependency isn't merged, don't start; comment on the issue instead.
- Stay within the files and modules listed in the issue's scope. If you need to change something out of scope, stop and comment explaining why rather than expanding the change.

### Branches and worktrees
- One issue per branch, named `issue-<number>-<short-slug>` (e.g. `issue-13-trafilatura-extractor`).
- When running in parallel with other agents, work in your own git worktree.
- Rebase on `main` before opening a PR.

### Commits
- Conventional commits: `feat:`, `fix:`, `test:`, `refactor:`, `docs:`, `chore:`, `ci:`, with an optional scope, e.g. `feat(extract): add trafilatura extractor`.
- Small, coherent commits. Don't mix formatting-only changes with logic changes.

### Pull requests
- The PR description includes `Closes #<issue>`, a summary of the change, how it was verified (commands and results) and any follow-up issues you suggest.
- Tick off the issue's acceptance criteria in the PR description.
- Update docs (`CLAUDE.md`, `PROJECT_PLAN.md`, ADRs, README) if your change affects them.
- A human reviews and merges every PR. Never merge to `main` yourself.

### When to stop and ask
Stop and leave a comment on the issue, rather than guessing, when:
- the acceptance criteria are ambiguous or contradict this file or the plan;
- the work needs a contract change, a new external service, or a significant new dependency;
- it would touch infrastructure, credentials, IAM or billing;
- you find a design problem the issue doesn't anticipate.

### Agent automation
Development runs as one **lead** session coordinating up to **3 worker** agents, plus a short-lived **reviewer** agent for each finished PR (PROJECT_PLAN.md §9.4).

**Session lifecycle.** Every lead session starts with `/lead-start` and ends with `/lead-finish` (`.claude/skills/`).
- Both run `scripts/lead.py`, which does the mechanical steps: update `main`, check dependencies and the pre-commit hook, remove merged worktrees and branches, sync the board, and print the status.
- The skills add the steps that need judgement: starting workers and reviewers, reviewing PRs, reporting, and writing a handoff note for the next session.
- Workers and reviewers are project subagents defined in `.claude/agents/` (`worker.md`, `reviewer.md`). Their briefs live there, so the lead sends each one only the issue or PR numbers and any specifics.
- Workers run on Sonnet. The lead and reviewers run on Opus, and the lead switches a worker to Opus for `needs-human` or design-heavy issues. The reviewer agent has no file-editing tools, and workers can't start subagents.
- `lead.py` never deletes anything with uncommitted or unmerged work; it lists it instead.

**The human's role is to read and merge.** Agents do all the preparation, so every PR reaches the human ready to merge as it is:
- Never ask the human to edit files, fill in templates, tick boxes or make follow-up commits. If a PR needs a change, including review feedback the human gives in a comment, an agent makes it.
- Approval is the merge. For `needs-human` decisions (ADRs, contracts, curation), the agent writes the final version (ADR status `Accepted`, every definition-of-done item ready) and opens a normal, non-draft PR. Merging it is the sign-off; declining or commenting is the rejection or the request for changes.
- Only ask for actions agents can't do: anything needing credentials or secrets, GitHub settings and app installs, a decision between real alternatives, or a judgement only the human can make. Give the shortest exact steps, such as a URL or a single command.
- Report to the human briefly: which PRs are ready to merge and in what order, and anything that genuinely needs them.

**Board.** The Project board (https://github.com/users/4OH4/projects/1) is the source of truth for status. Use `scripts/board.py` to read and move cards rather than editing them by hand:
- `python3 scripts/board.py show`: issues grouped by status.
- `python3 scripts/board.py set <issue> "<status>"`: statuses are Backlog, Ready, In progress, Agent review, Human review, Done. "Agent review" means agents are still reviewing or reworking the PR; "Human review" means it waits only for the human.
- `python3 scripts/board.py promote [--dry-run]`: moves closed issues to Done, then moves open `agent-ready` issues from Backlog to Ready once every "Depends on #N" issue is closed. It never promotes `needs-human` issues.

**Workers** each take one issue:
1. Set the card to In progress.
2. Create a worktree and branch from up-to-date `origin/main`, then implement and verify.
3. Rebase, push, and open the PR.
4. Set the card to Agent review.
5. Report back the PR URL, the verification results and any judgement calls.

Workers don't merge, don't edit other issues, and don't move cards other than their own. If blocked, a worker comments on the issue, leaves the card where it is and reports back.

**Reviewers** give every PR an independent, adversarial review before it reaches the human. The lead starts one per finished PR, with a fresh context: the brief names only the PR and its issue, never the worker's report or the lead's reasoning. Reviewers are **read-only**: they never commit, push or correct anything, including the PR text. A reviewer:
1. Checks the PR text against the diff and its own lint/test run: every claim, ticked criterion and piece of verification evidence must actually hold.
2. Tries to show the change falls short of the issue's goals, then looks at wider effects: downstream issues that consume it, PROJECT_PLAN.md, ADRs, the architecture rules, and assumptions that may not survive real data.
3. **Writes its recommendations and risk areas for the human** in a "For human review" section at the top of the PR body (directly below "Decisions you approve by merging" on `needs-human` PRs), changing nothing else in the body. It covers judgement calls the human is implicitly approving, risks, inaccuracies in the PR text, and open questions, each with a recommendation. Each item is one or two lines; if there's nothing, it says "None".
4. Reports to the lead the findings it considers clear defects.

The lead decides what happens to the defects. It sends the worker back to fix them, and starts a fresh reviewer if the rework changed behaviour. There are at most two reviews per PR; if the second still finds defects, the lead reports the PR to the human instead of looping, and moves its card to Human review. Once a defect is fixed, the worker marks its "For human review" item "**Resolved** in <sha>", and the lead checks those markers when it re-runs the checks; any defect left unfixed stays there for the human to weigh.

Reviewers don't merge, move cards or touch other PRs. They don't count towards the 3-worker limit, and they never run on a PR whose worker is still active.

**The lead** loops:
1. Run `board.py promote`.
2. Start workers for Ready issues, up to 3 at a time. Prefer issues whose scopes don't overlap, and never run two issues touching the same file at once.
3. When a worker reports a PR, start a reviewer for it.
4. When the reviewer reports, send the worker back for any defects. Once the PR is final, re-run the checks and post a short "Lead review" comment on the PR with the verdict and merge order, then move the card to Human review.
5. Tell the human which PRs are ready to merge and which `needs-human` issues are now unblocked.

The lead also starts workers for `needs-human` issues that an agent can prepare, e.g. those with an "Agent prep" section. The worker produces a complete, merge-ready PR (see "The human's role" above), and the lead lists any choices the human is implicitly approving at the top of the PR description.

**Shared files.** Several issues touch `backend/pyproject.toml`, `backend/uv.lock`, `cli.py`, `Makefile` and `config/sources.yaml`. Rebase on `main` just before pushing. Resolve `uv.lock` conflicts by taking `main`'s version and re-running `uv lock`, never by hand-editing.

**Tooling notes.**
- Agent worktrees live under `.claude/worktrees/` (git-ignored). Never `git add` them.
- In a worktree, install dependencies with `uv sync` (in `backend/`), **not** `make setup`. `pre-commit install` writes a hook into the shared `.git/hooks/`, which then points at that worktree's environment and breaks commits everywhere once the worktree is removed. The lead installs the hook once, from the main checkout.
- **GraphQL budget.** GitHub allows 5,000 GraphQL points per user per hour, shared by the lead, every agent and `gh` itself (`gh pr view/edit/list` and `gh project` all use it). Prefer REST (`gh api repos/4OH4/news-bot/pulls/<n>`, `.../issues/<n>`), which has a separate allowance, for reading PRs and issues in loops. Don't call `board.py show` repeatedly: `board.py` prints the points used and the allowance left after each command, and `lead.py start` already loads the board once. If it reports "GraphQL rate limit exhausted; resets at <time> UTC", wait or use REST; `gh project` shows the same failure as the misleading "unknown owner type".

## Things agents must not do

- Handle production credentials, service account keys or secrets. Never commit secrets; use `.env` locally (git-ignored) and Secret Manager in production.
- Run `terraform apply`, `gcloud` commands that change resources, or production deploys. Infrastructure changes are `needs-human`.
- Edit evaluation expectations in `eval/urls.yaml` to make results look better. Changes there require human review.
- Commit third-party article content: page snapshots, extracted article text or full feed dumps. This is a public repo; short quoted snippets in `eval/urls.yaml` are the only exception.
- Disable or weaken tests, lint rules or type checks to get CI passing.
- Add tracking, analytics or third-party scripts to the client.

## Decisions

Architecture decisions are recorded in `docs/adr/`. Read the relevant ADRs before working in an area, and don't reverse a recorded decision without a new ADR approved by me. If you make a notable design choice within an issue, mention it in the PR so it can be captured as an ADR if needed.

## Key references

- `PROJECT_PLAN.md`: aims, requirements, architecture, phases, backlog strategy
- `docs/adr/`: decision records
- `config/sources.yaml`, `config/interests.yaml`: sources and ranking rules
- `config/extraction.yaml`: per-domain extractor overrides
- `config/url_params.yaml`: per-domain tracking-parameter rules for canonical URLs (evidence-backed only)
- `eval/`: extraction evaluation set (`urls.yaml`) and results; snapshots live in `NEWSFEED_FIXTURES_DIR`
- `.github/ISSUE_TEMPLATE/`: `agent-task.yml` and `needs-human.yml`, the shape every issue follows
- `scripts/bootstrap_github.py`: creates labels, milestones and seed issues
