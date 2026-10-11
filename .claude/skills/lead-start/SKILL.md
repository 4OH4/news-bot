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
1. **Independent review.** Start a reviewer with the Agent tool, using `subagent_type: general-purpose`, `isolation: worktree` and `run_in_background: true`, and the reviewer brief below. Give it only the PR number, the issue number and the consuming issues. Don't pass on the worker's report or your own view of the PR, because the point is a fresh context. Only start it once that PR's worker has finished.
2. **When the reviewer reports:**
   - For larger defects it lists, use SendMessage to send the worker back with specific changes. Tell it to `git pull` first, because the reviewer may have pushed commits. If the rework changes behaviour, start a fresh reviewer afterwards.
   - Read the reviewer's fix commits and the "For human review" section, and correct anything that is wrong or unclear.
3. **Re-run the checks.** In the worker's worktree, after `git pull`, run `uv sync` in `backend/`, then `make lint` and `make test`. Run `make eval` too if `extract/` was touched. Confirm CI is green.
4. **Shared files.** If the PR shares `pyproject.toml` or `uv.lock` with another open PR, either stack it on that PR's branch (resolve `uv.lock` with `uv lock`) or tell the human the merge order.
5. **Comment.** Post a short "Lead review" comment on the PR: verdict, what you re-ran, the merge order.

## 5. Report

Keep it short:
- **PRs ready to merge**, in merge order, with one line each.
- **Points from each PR's "For human review" section** that most need the human's attention, and the decisions they approve by merging for `needs-human` PRs.
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
   - `## For human review` containing only "Pending independent review" (a reviewer agent fills it in);
   - acceptance criteria ticked, with evidence;
   - verification output;
   - design notes;
   - follow-ups.
   End the body with a blank line, then `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
7. `python3 scripts/board.py set <N> "In review"`.
8. Never merge or push to main, and don't touch other issues or cards.

Report back: the PR URL, the result of each verification command, and every judgement call.
```

## Reviewer brief (template)

```
You are an independent reviewer for the repo 4OH4/news-bot (you are in your own git worktree). Review PR #<P>, which closes issue #<N>, adversarially before a human sees it. Assume the PR has problems until you've checked. You weren't involved in writing it; form your own view from the code, the issue and the project docs.

Read first: CLAUDE.md (all of it, especially "Agent automation" > "Reviewers"), PROJECT_PLAN.md, the ADRs in docs/adr/, the issue (`gh issue view <N> --repo 4OH4/news-bot --json title,body,labels,state,comments`), the PR (`gh pr view <P> --repo 4OH4/news-bot --json title,body,headRefName,comments`, `gh pr diff <P> --repo 4OH4/news-bot`), and the issues that consume this work: <list>.

Set up: `git fetch origin`, then `git checkout --detach origin/<branch>`. Run `cd backend && uv sync`. NEVER run `make setup`. Wrap network git/gh calls in `timeout 120`.

Review:
1. PR text vs. reality. Check every claim in the description, every ticked acceptance criterion and every piece of verification evidence against the diff and against your own `make lint` / `make test` run (and `make eval` if extract/ changed). Flag anything overstated, missing or stale.
2. Issue goals. Does the change achieve what the issue's Context says it is for, not just the letter of the task list? Look for unhandled edge cases, failure modes, silent data loss, and tests that pass without testing the behaviour.
3. Wider implications. Check fit with downstream issues, PROJECT_PLAN.md phases and requirements, ADRs, and the architecture rules and conventions in CLAUDE.md (contracts, ports, config in config/, polite fetching, logging, synthetic fixtures). Look for assumptions that will break on real data or later phases, things that will go stale, and scope creep or premature generality.

Act:
- Easy fixes: make them yourself. Easy means within the issue's scope, with no change to a public interface, contract or design decision, and a test for any behaviour change. Examples: inaccurate PR text, a missing test for a claimed behaviour, a missing log field, a small bug with an obvious fix. Commit with conventional commits ending with a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, then `git push origin HEAD:<branch>`. Never force-push.
- Uncertain points: add or replace a `## For human review` section at the top of the PR body. For needs-human PRs, put it directly below "Decisions you approve by merging". Each item is one or two lines: what the human is implicitly approving, a risk, or an open question, each with your recommendation. Write "None" if there's nothing. Don't repeat what the PR already explains well. Edit the body with `gh pr edit <P> --repo 4OH4/news-bot --body-file <file>`, keeping the rest of the body and correcting anything inaccurate.
- Larger defects: don't fix them. List them in your report for the lead.
- Post one PR comment headed "Independent review", listing the fixes you pushed and the larger defects, if any.

Don't merge, move board cards, or touch other PRs or issues.

Report back: what you fixed (commit SHAs), what you put under "For human review", the larger defects for the lead, and your lint/test results.
```
