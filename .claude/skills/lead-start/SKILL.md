---
name: lead-start
description: Start (or resume) a lead development session for news-bot. Syncs the repo and board, starts worker agents on Ready issues, prepares unblocked needs-human PRs, reviews finished PRs, and reports what is ready to merge. Use when the user says "continue", "start", "resume development" or runs /lead-start.
---

# Lead session: start

You are the **lead** described in CLAUDE.md, "Agent automation". The human only reads and merges; never ask them to edit files.

## 1. Sync

Run `python3 scripts/lead.py start` from the repo root. It:
- fast-forwards `main`;
- syncs dependencies and checks the pre-commit hook;
- removes merged worktrees and branches;
- syncs the board.

Read its "Kept back" list. A worktree that is unmerged or has uncommitted changes means an earlier worker's work was left behind. Check its PR (`gh pr list --head <branch>`) and either resume it or report it.

## 2. Plan

- Read the board status that `lead.py` printed, and the open PRs.
- Count the Ready `agent-ready` issues. Read each one with `gh issue view <n> --repo 4OH4/news-bot --json title,body,labels,state`.
- Find `needs-human` issues whose dependencies are all closed and which have an "Agent prep" section. Run `python3 scripts/board.py show` for the list.
- Choose up to **3 concurrent workers** in total, counting issues already In progress. Never run two issues whose "Files / modules in scope" overlap, except the shared files listed in CLAUDE.md, which are handled by rebasing.

## 3. Start workers

For each chosen issue, use the Agent tool with `subagent_type: general-purpose`, `isolation: worktree` and `run_in_background: true`. Use the worker brief below, filled in. Don't wait on one worker before starting the next.

## 4. Review as workers report back

For each finished PR:
1. Read the diff against the issue's acceptance criteria and CLAUDE.md: scope, contracts, synthetic fixtures only, tests, logging criteria.
2. In the worker's worktree, run `uv sync` in `backend/`, then `make lint` and `make test`. Run `make eval` too if `extract/` was touched.
3. If the PR shares `pyproject.toml` or `uv.lock` with another open PR, either stack it on that PR's branch (resolve `uv.lock` with `uv lock`) or tell the human the merge order.
4. Fix small problems yourself. For larger ones, use SendMessage to send the worker back with specific changes.
5. Post a short "Lead review" comment on the PR: verdict, what you re-ran, the merge order.

## 5. Report

Keep it short:
- **PRs ready to merge**, in merge order, with one line each.
- **Decisions the human approves by merging**, for `needs-human` PRs.
- **Anything only the human can do**, with exact steps: settings, credentials, real decisions.
- What starts next once those merge.

If the human asked for autonomy between merges, use `/loop` with a fallback of 20 minutes or more. Re-run `python3 scripts/board.py promote` each time.

## Worker brief (template)

```
You are a worker agent for the repo 4OH4/news-bot (you are in your own git worktree). Task: implement GitHub issue #<N> ("<title>") end to end and open a merge-ready PR. <For needs-human issues: "This is a needs-human issue; prepare it as a complete, non-draft PR that the human approves by merging. At the TOP of the PR body add 'Decisions you approve by merging', one line per choice the issue didn't dictate.">

Read first: CLAUDE.md (all of it, especially "Agent automation" and "The human's role is to read and merge"), the relevant PROJECT_PLAN.md sections and ADRs in docs/adr/, and the issue: `gh issue view <N> --repo 4OH4/news-bot --json title,body,labels,state`. Also read the bodies of issues that consume your output: <list>.

Environment:
- gh is an old version: `gh issue view`/`gh pr edit` without --json fail. Edit a PR body with `gh api -X PATCH repos/4OH4/news-bot/pulls/<n> -F body=@file`.
- The git remote is HTTPS with gh as the credential helper. Wrap network git/gh calls in `timeout 120`.
- Install dependencies with `cd backend && uv sync`. NEVER run `make setup` in a worktree (it rewrites the shared pre-commit hook).
- Add dependencies with `uv add` (or `uv add --dev`). Fix uv.lock conflicts with `uv lock`, never by hand.
<Anything specific: other in-flight PRs touching shared files, notes left on the issue by the lead.>

Steps:
1. `python3 scripts/board.py set <N> "In progress"`.
2. `git fetch origin`, then create branch `issue-<N>-<slug>` from origin/main.
3. Implement every task item and acceptance criterion, staying within the files in scope. If you must go outside them, or the issue is ambiguous, stop and report back instead of guessing.
4. Verify: `make lint`, `make test`, plus anything in "How to verify". Use real output, and never weaken checks.
5. Conventional commits. End each message with a blank line, then `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
6. Rebase on origin/main, push, and open a non-draft PR with `gh pr create --repo 4OH4/news-bot --base main`, following .github/pull_request_template.md:
   - `Closes #<N>`;
   - acceptance criteria ticked, with evidence;
   - verification output;
   - design notes;
   - follow-ups.
   End the body with a blank line, then `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
7. `python3 scripts/board.py set <N> "In review"`.
8. Never merge or push to main, and don't touch other issues or cards.

Report back: the PR URL, the result of each verification command, and every judgement call.
```
