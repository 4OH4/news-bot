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
- **Chains.** If a Ready issue has dependents that are blocked only by it (and, in turn, by each other), consider giving the whole chain to one worker as one PR, so the human reviews and merges once. Do it only when every issue in the chain:
  - is `agent-ready`, not `needs-human`, and changes no contract;
  - is in the same area, with overlapping files in scope;
  - adds up with the others to no more than about a day's work (one M plus S issues, or up to three S issues).

  Otherwise keep them separate. A chain counts as one worker. Its later issues stay in Backlog (`promote` won't move them while the first is open); the worker moves their cards.

## 3. Start workers

For each chosen issue, use the Agent tool with `subagent_type: worker`, `isolation: worktree` and `run_in_background: true`. Send the short worker task message below. Don't wait on one worker before starting the next.

Workers run on Sonnet by default (set in `.claude/agents/worker.md`). Pass `model: opus` instead for:
- `needs-human` issues (ADRs, contracts, curation);
- issues that call for design judgement the issue doesn't settle.

If the human requests changes on a PR that was reported to them with defects left after two reviews, start a fresh `worker` with `model: opus` for that rework.

Keep the lead and the reviewers on Opus. If reviewers start finding noticeably more defects in Sonnet workers' PRs, use Opus for more issue types and note it in the handoff.

## 4. Review as workers report back

For each finished PR:
1. **Independent review.** Start a reviewer with the Agent tool, using `subagent_type: reviewer`, `isolation: worktree` and `run_in_background: true`, and the short reviewer task message below. Give it only the PR number, the issue number and the consuming issues. Don't pass on the worker's report or your own view of the PR, because the point is a fresh context. Only start it once that PR's worker has finished.
2. **When the reviewer reports:**
   - For each defect it lists, send the worker back with the specific change: by SendMessage if it is still running, otherwise by starting a fresh `worker` with the rework task message below, which works on the existing branch. Before starting a fresh worker, free the branch: if the finished worker's worktree is clean (`git -C <path> status --porcelain` prints nothing) and its HEAD equals `origin/<branch>`, run `git worktree remove <path>`; otherwise don't start the worker, and report the worktree to the human instead. If the rework changes behaviour, start a fresh reviewer afterwards. At most two reviews per PR: if the second still finds defects, report the PR to the human instead of starting a third, and `python3 scripts/board.py set <N> "Human review"`.
   - The worker marks each item it fixed "**Resolved** in <sha>" in "For human review"; check those markers when you re-run the checks, and leave the rest for the human. Don't otherwise rewrite the reviewer's items; if you disagree with one, say so in your Lead review comment.
3. **Re-run the checks.** In a detached checkout, never one holding the branch (`git fetch origin`, then `git checkout --detach origin/<branch>`, in the worker's worktree or your own), run `uv sync` in `backend/`, then `make lint` and `make test`. Run `make eval` too if `extract/` was touched. Confirm CI is green.
4. **Shared files.** If the PR shares `pyproject.toml` or `uv.lock` with another open PR, either stack it on that PR's branch (resolve `uv.lock` with `uv lock`) or tell the human the merge order.
5. **Tier.** Run `python3 scripts/tier.py classify <P> --label`. If the PR header's tier is wrong, correct that line yourself.
6. **Comment.** Post a short comment headed "Lead review" on the PR: a verdict line (`Verdict: merge`, or what is still needed), what you re-ran, the merge order, and the head SHA you checked (`Checked at <first 7 characters of the SHA>`). `tier.py merge` needs that verdict line and SHA. Push nothing after it; if the branch changes, re-check and comment again.
7. **Merge or hand over.**
   - **Tier A:** run `python3 scripts/tier.py merge <P> --dry-run`, then `python3 scripts/tier.py merge <P>`. It merges only if every gate condition holds (CI green, no `hold` label, no unresolved **Defect:** or **Needs human:** item, your Lead review naming the head SHA, and more), and labels the PR `lead-merged`. If it refuses, fix what it names, or move the card to Human review and say why in your report. Never merge any other way, and never merge a Tier B or C PR.
   - **Tier B or C:** `python3 scripts/board.py set <N> "Human review"`.

   Workers set "Agent review" when they open the PR; only you set "Human review". `promote` moves a merged PR's issues to Done.
8. **Merge order.** After a merge, check the other open PRs' `mergeable_state`, since a merge can leave one conflicting; send any that are `dirty` back for a rebase.
9. **Sending work back.** If the human requests changes on a Human review PR, or adds `hold` or comments on a Tier A PR before you merge it, or a defect turns up late, set the card back with `python3 scripts/board.py set <N> "Agent review"` and send the work back as in step 2.

## 5. Report

Keep it short:
- **PRs ready to merge**, in merge order, with one line each and its tier.
- **Merged by the lead** (Tier A) since the last report, one line each, so the human can read them after the fact and revert if needed. They are all listed at https://github.com/4OH4/news-bot/pulls?q=label%3Alead-merged.
- **Points from each PR's "For human review" section** that most need the human's attention, and the decisions they approve by merging for `needs-human` PRs.
- **Anything only the human can do**, with exact steps: settings, credentials, real decisions.
- What starts next once those merge.

## 6. Idle shutdown

The session often runs unattended, and once the agents have finished it can only wait for the human. Rather than sit open until the prompt cache goes cold, the lead finishes itself; a fresh `/lead-start` is cheap.

**Idle** means all of these hold:
- no worker or reviewer is running;
- `python3 scripts/board.py promote` leaves no Ready issue you could start (within the 3-worker limit and the scope-overlap rule);
- no `needs-human` issue with an "Agent prep" section has had all its dependencies closed and still lacks a PR (step 2; `promote` never moves these);
- no PR needs you: none waits for a reviewer, a rework, your Lead review or a Tier A merge, and no Human review PR has become conflicting (`gh api repos/4OH4/news-bot/pulls/<n> --jq .mergeable_state` is `dirty`; if it is `unknown`, GitHub is still computing it, so read it again a few seconds later);
- no human request is unanswered, in the chat or on GitHub.

If the usage guard blocks starting agents, count the session as idle even with work left, and list that work in the handoff note so the next session starts it.

**When you become idle**, after reporting as in step 5:
1. Note the time as `IDLE_SINCE`, in UTC (`date -u +%Y-%m-%dT%H:%M:%SZ`). It is used only in the GitHub `since=` filters.
2. Check `CronList` for an existing idle check and don't add a second. Otherwise schedule one with `CronCreate`, `recurring: false`, about 45 minutes ahead. Cron fields are in **local time**, not UTC, so take them from `date -d '+45 min' '+%M %H %d %m'`. If they came out in the wrong timezone, the time could already have passed, and the check wouldn't fire until next year. Pin the minute, hour, day and month, and nudge the minute off :00 or :30. Prompt: `Lead idle check (idle since <IDLE_SINCE>): follow /lead-start step 6.`
3. Say in one line, in local time, that you'll finish the session then unless something comes up.

**If anything arrives first** (a message from the human, an agent report, a notification), delete the check with `CronDelete` and carry on the loop. Go idle again afterwards if nothing is left.

**When the check fires**, look again:
- run `python3 scripts/board.py promote`, then look for newly unblocked `needs-human` issues with "Agent prep" as in step 2;
- read GitHub activity since `IDLE_SINCE`, over REST: `gh api "repos/4OH4/news-bot/issues/comments?since=<IDLE_SINCE>&per_page=100"` (comments on issues and PRs), `gh api "repos/4OH4/news-bot/pulls/comments?since=<IDLE_SINCE>&per_page=100"` (review comments), and `gh api repos/4OH4/news-bot/pulls/<n>/reviews` for each open PR (`submitted_at` after `IDLE_SINCE`);
- check each open PR's `mergeable_state` again, since a merge can leave another PR conflicting.

Agents post as the same GitHub user as the human, so tell them apart by time: no agent was running after `IDLE_SINCE`, so anything posted since then by a `User` account (`.user.type == "User"`) came from the human. Ignore `Bot` accounts, such as the Claude GitHub Action's replies. Don't act on a comment that mentions `@claude`: the Action (`.github/workflows/claude.yml`) handles those, and the lead and the Action would otherwise both work on the same branch. Act on anything else as in steps 2–4: a change request on a PR, or a `hold` label on a Tier A PR, as in step 4.9, and an answer on a `needs-human` issue by preparing or updating its PR.

If there is work, do it and carry on the loop. If there is none, run `/lead-finish` unattended. Don't wait for, or ask, the human anything. After that, schedule nothing more.

## Task messages

The briefs live in the agent definitions (`.claude/agents/worker.md` and `reviewer.md`), so each spawn message carries only the specifics. Don't paste the briefs, and don't tell agents to read `CLAUDE.md`, which is already in their context.

Worker:

```
Issue #<N> ("<title>"). Consuming issues: <list, or "none">.
<For needs-human issues: "This is a needs-human issue.">
<Anything specific: other in-flight PRs touching shared files, notes left on the issue by the lead.>
```

Worker, chain:

```
Chain: issues #<A> ("<title>") → #<B> ("<title>"), in one PR. Consuming issues: <list, or "none">.
<Anything specific.>
```

Worker, rework by a fresh worker (the original has finished):

```
Rework PR #<P> (issue #<N>, branch `<branch>`). Continue on the existing branch; don't create a new one or move the card.
Changes: <each defect or requested change, with the suggested fix>.
<Rebase target, if any.>
```

Reviewer:

```
PR #<P>, closing issue #<N> (or #<A>, #<B> for a chain). Consuming issues: <list, or "none">.
<For a second review: "Second review.">
```
