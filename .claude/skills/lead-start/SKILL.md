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

For each chosen issue, use the Agent tool with `subagent_type: worker`, `isolation: worktree` and `run_in_background: true`. Send the short worker task message below. Don't wait on one worker before starting the next.

Workers run on Sonnet by default (set in `.claude/agents/worker.md`). Pass `model: opus` instead for:
- `needs-human` issues (ADRs, contracts, curation);
- issues that call for design judgement the issue doesn't settle;
- rework after a second review still found defects.

Keep the lead and the reviewers on Opus. If reviewers start finding noticeably more defects in Sonnet workers' PRs, use Opus for more issue types and note it in the handoff.

## 4. Review as workers report back

For each finished PR:
1. **Independent review.** Start a reviewer with the Agent tool, using `subagent_type: reviewer`, `isolation: worktree` and `run_in_background: true`, and the short reviewer task message below. Give it only the PR number, the issue number and the consuming issues. Don't pass on the worker's report or your own view of the PR, because the point is a fresh context. Only start it once that PR's worker has finished.
2. **When the reviewer reports:**
   - For each defect it lists, use SendMessage to send the worker back with the specific change. If the rework changes behaviour, start a fresh reviewer afterwards. At most two reviews per PR: if the second still finds defects, report the PR to the human instead of starting a third, and `python3 scripts/board.py set <N> "Human review"`.
   - In "For human review", mark items fixed by rework as resolved, and leave the rest for the human. Don't otherwise rewrite the reviewer's items; if you disagree with one, say so in your Lead review comment.
3. **Re-run the checks.** In the worker's worktree, run `uv sync` in `backend/`, then `make lint` and `make test`. Run `make eval` too if `extract/` was touched. Confirm CI is green.
4. **Shared files.** If the PR shares `pyproject.toml` or `uv.lock` with another open PR, either stack it on that PR's branch (resolve `uv.lock` with `uv lock`) or tell the human the merge order.
5. **Comment.** Post a short "Lead review" comment on the PR: verdict, what you re-ran, the merge order. Then `python3 scripts/board.py set <N> "Human review"`. Workers set "Agent review" when they open the PR; only you set "Human review".

## 5. Report

Keep it short:
- **PRs ready to merge**, in merge order, with one line each.
- **Points from each PR's "For human review" section** that most need the human's attention, and the decisions they approve by merging for `needs-human` PRs.
- **Anything only the human can do**, with exact steps: settings, credentials, real decisions.
- What starts next once those merge.

If the human asked for autonomy between merges, use `/loop` with a fallback of 20 minutes or more. Re-run `python3 scripts/board.py promote` each time.

## Task messages

The briefs live in the agent definitions (`.claude/agents/worker.md` and `reviewer.md`), so each spawn message carries only the specifics. Don't paste the briefs, and don't tell agents to read `CLAUDE.md`, which is already in their context.

Worker:

```
Issue #<N> ("<title>"). Consuming issues: <list, or "none">.
<For needs-human issues: "This is a needs-human issue.">
<Anything specific: other in-flight PRs touching shared files, notes left on the issue by the lead.>
```

Reviewer:

```
PR #<P>, closing issue #<N>. Consuming issues: <list, or "none">.
```
