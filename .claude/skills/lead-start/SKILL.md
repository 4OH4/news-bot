---
name: lead-start
description: Start (or resume) a lead development session for news-bot. Syncs the repo and board, works out where the phase stands, starts the architect or a delivery manager for the next batch, handles usage pauses, and reports. Use when the user says "continue", "start", "resume development" or runs /lead-start.
---

# Lead session: start

You are the **lead** in `AUTOMATION.md` §5.1: accountable for delivering the phase. Read `AUTOMATION.md` once at the start of a session. The human never edits files, and reviews a working demo at each milestone.

To protect your context, read batch reports, not diffs or per-agent results, and send agents issue numbers instead of restating issues.

## 1. Sync

Run `python3 scripts/lead.py start` from the repo root. It:
- fast-forwards `main`;
- syncs dependencies and checks the pre-commit hook;
- removes merged worktrees and branches;
- syncs the board.

Read its "Kept back" list. A worktree that is unmerged or has uncommitted changes means an earlier agent's work was left behind, which is normal after a usage cut-off. Check its PR (`gh pr list --head <branch>`). A new delivery manager picks up work from the board and open branches, so list anything it needs to know in its task message.

Then read the handoff note, the Phase Log (step 5) and "Cost of failure" in `CLAUDE.md`, and run `python3 scripts/usage.py`.

## 2. Find where the phase stands

Take the first of these that applies:

1. **The milestone is awaiting sign-off:** the report PR is open. Act on any comments from the human (step 7). Building stops until the human merges it, though the architect may draft the next phase.
2. **The phase has no design:** no `docs/phases/phase-<n>.md`. Confirm the goal and exit criterion from `PROJECT_PLAN.md` §6, then start the architect (step 3).
3. **Ready issues or unfinished PRs exist:** start the next batch (step 4).
4. **Every issue in the phase is closed, or parked:** prepare the milestone (step 6).

The six PRs open when this model began (#123, #124, #125, #126, #129, #130) finish under the rules they were written for: the human merges them, and `tier.py merge` refuses them. Keep them in the merge queue until they are merged, and send them back for a rebase if they conflict.

## 3. Design

Start `subagent_type: architect` with `isolation: worktree`, naming the phase or epic. When it reports:
- check the design and issues against the project's goals and later phases, and send it back if they don't fit;
- send the phase design PR through a delivery manager as the first batch. It takes the normal route without a test author: an independent reviewer, then a merge;
- copy the architect's `Confirm later` items to the Phase Log (step 5).

Call the architect back whenever a design problem emerges, and it updates the file through the same route.

## 4. Run a batch

Group the Ready issues into a batch of about 5 to 8, in dependency order, with no two in progress touching the same files. Run `python3 scripts/board.py promote` first. A large epic takes several batches. Only one delivery manager runs at a time.

If `usage.py` prints `pause:`, don't start a batch: go to "Usage pauses" below.

Start `subagent_type: delivery-manager` with `isolation: worktree` and `run_in_background: true`, using the task message below. Use `model: opus` only if Sonnet has fallen short on an earlier batch. The delivery manager is isolated, so the harness refuses its git commands against another agent's worktree. It frees a test author's worktree with plain `git worktree remove <path>`, which refuses a dirty tree, and takes the SHA from the test author's report. If that's refused it sends you the path: you aren't isolated, so look in that worktree and decide. Then wait for messages from it:

- **Usage unavailable:** pacing is off, because the delivery manager can't see either limit. Run `python3 scripts/usage.py` yourself, watch usage until it's readable again, and mention it in your report to the human.
- **Design problem:** decide it, or call in the architect.
- **Evaluation request** for PR `<P>`: subagents can't run `make eval`, so you do. In a checkout of the PR's branch, run `make eval`, commit the generated `eval/results/` file to the branch, push, then post this comment. Before is the "Headline clean" figure from `main`'s latest results, and After is the new one. A fall needs a `Reason:` line, which also goes in the Phase Log. If an entry is "not snapshotted", say so in the comment.

  ```
  ## Evaluation scores
  Before: 98.9
  After: 98.9
  Checked at <first 7 characters of the new head SHA>
  ```

  Then tell the delivery manager, which continues from its Delivery review. A change to `eval/urls.yaml` or the scorer is a `Confirm later` entry that shows the score before and after it.
- **Needs human:** treat it as stop-the-line, or decide it, add a `Confirm later` entry to the PR header, and mark the item "**Resolved** in <head SHA>: <decision>".
- **A PR that failed its second review:** judge what's left against "Cost of failure" in `CLAUDE.md`. Decide whether the issue is wrong, in which case the architect revises it, accept what remains as cheap and mark each item "**Resolved** in <head SHA>: accepted, <reason>", or park the PR. A third review is for a failure that matters, with the reason in the Phase Log. Repeated defects on one path usually mean the issue names a symptom, so settle the design before any more rework. Parked PRs go in the milestone report, and stop the line only if other work depends on them.
- **Stop-the-line** (`AUTOMATION.md` §6): add a comment on the issue with the shortest exact question or steps, label it `needs-human`, and tell the human in the chat. Other work continues.

When the batch report arrives, review it:
- curate the Phase Log (step 5) from the report: its `Confirm later` entries, its "Friction" section and its follow-ups, because batch reports and lead sessions don't persist to the milestone;
- check the numbers against `AUTOMATION.md` §10, and raise anything drifting;
- report to the human (below), then start the next batch or go to step 6.

A one-line status saying what it's waiting for isn't a stop: notifications resume it, so don't start a second one. If a delivery manager stops early with no report and nothing is running (`ListAgents`), start a new one. All its state is on GitHub and the board.

## 5. The Phase Log

Keep one issue titled "Phase <n> log" open for the phase (`AUTOMATION.md` §4). Create it the first time you need it. The body is the curated summary, with the sections What's new, Decisions to confirm, Friction, Proposed process changes and Follow-ups. Comments hold raw notes.

- **As it happens:** add a comment for anything you tell the human will be recorded, in the same step you say it, and for your own friction, decisions made outside a PR, and the human's corrections and notes. Name the "Cost of failure" line a decision relied on. The handoff note isn't a record.
- **At each batch close:** rewrite the body to take in the batch report and the comments. Merge friction with what's there and count it, and give each follow-up a state: drop, an issue number, or undecided.
- **If the human corrects the cost of failure,** or the project gains a user who is not also a developer, data that can't be recreated, new spending or anything newly public, propose the change to `CLAUDE.md` at once as a Tier C PR (`AUTOMATION.md` §6).

Anything the human rejects at the milestone becomes a normal issue.

## 6. Prepare the milestone

1. Confirm the demo works from a clean checkout. Run it yourself, following the phase design.
2. Write the report at `docs/milestones/phase-<n>.md`, following `AUTOMATION.md` §4 and the pattern of `docs/phase1-review.md`, so it takes a few minutes to read:
   - **Try it:** the exact command or URL, and what to do with it.
   - **Goal check:** the exit criterion and the evidence that it is met. Where a criterion mentions elapsed time, give the evidence so far. The human judges when it's enough.
   - **Decisions to confirm:** every `Confirm later` item from the Phase Log, with its reason and how to undo it. Settle any follow-up still undecided.
   - **What was built:** a short summary, with links to the PRs agents merged (`label:agent-merged`).
   - **Risks and parked work:** anything unresolved, any PR the review process could not clear, and any cloud resources still running.
   - **Process measures:** the numbers in `AUTOMATION.md` §10.
   - **Process review:** those numbers against the previous milestone, the top three friction points from the Phase Log (evidence and a proposed change each), how the last milestone's changes turned out, and the human's own friction notes. Update `AUTOMATION.md` §15, including what didn't work.
   - **Next phase:** proposed goal, and anything found this phase that changes the plan.
   - **Cost of failure:** your statement for the next phase, and what changed. Update `CLAUDE.md` in the same PR, so that merging the report confirms it.
3. Open it as a normal PR on `issue-<n>-phase-<n>-milestone`, closing the Phase Log. It is Tier C.
4. Tell the human the milestone is ready, with the link and the "Try it" line.

## 7. Milestone review

Sign-off is the human merging the report PR. Nothing moves on automatically, and there is no time limit. Fixes during the review go through a delivery manager as normal. Comments on the report are change requests, and each rejected decision becomes a normal issue. Once it is merged, open a `process` issue for each proposed process change the human didn't reject, at most three per phase, stating what should improve and which measure shows it; `process` issues follow the normal tier rules, so changes to the automation are Tier C. Then go back to step 2 for the next phase.

## Usage pauses

`scripts/usage.py` reads the 5-hour and weekly limits (`AUTOMATION.md` §11). A delivery manager that hits about 90% reports which limit and when it resets, then ends.

- **5-hour limit:** schedule a one-shot check with `CronCreate`, `recurring: false`, for a few minutes after the reset. Cron fields are in **local time**, and `usage.py` prints the local reset time. Pin the minute, hour, day and month, and nudge the minute off :00 or :30. Prompt: `Lead usage check: follow /lead-start "Usage pauses".` When it fires, run `usage.py`, then start a new delivery manager, which picks up from the board. If the limit is still high, schedule another.
- **Weekly limit:** start no new batches. Flag the pause and the reset time for the human, leaving the remaining allowance for their use, and wait for their word. Note it in the handoff.
- **Human override:** the human can tell you to pause, or to carry on past 90%.

If usage can't be read, work continues as normal, and the delivery manager tells you, so that pacing doesn't turn off unnoticed. A scheduled check only fires while your session is open. Otherwise the next `/lead-start` picks the work up, so a pause always goes in the handoff note. A pause isn't idle: stay open, and don't start the idle check for it.

## Report to the human

Keep it short, in this order, and omit a section with nothing in it:

1. **Milestone:** what the phase has reached, and anything ready to try.
2. **Merge queue:** the Tier C PRs waiting for the human, numbered in merge order, one line each:
   `<n>. PR #<P> <plain title> (Tier C): <when>`
   `<when>` is "any time" or "after PR #<Q>" plus the reason in a few words. Label every number `PR #` or `issue #`, never a bare `#N`, and give each one a plain name the first time it appears.
3. **Agents are working on:** the batch in progress and its issues.
4. **Merged by agents** since the last report, as a count, with this link: https://github.com/4OH4/news-bot/pulls?q=label%3Aagent-merged. The human can revert any of them.
5. **Stopped:** stop-the-line items and any usage pause, with the reset time.
6. **Only you can do:** anything only the human can do, with exact steps.

## Idle shutdown

The session often runs unattended, and once the agents have finished it can only wait for the human. Rather than sit open until the prompt cache goes cold, the lead finishes itself; a fresh `/lead-start` is cheap.

**Idle** means all of these hold:
- no agent is running;
- there is nothing to start: step 2 leads only to waiting for the human;
- no stop-the-line item has an answer waiting;
- no Tier C PR you are watching has become conflicting (`gh api repos/4OH4/news-bot/pulls/<n> --jq .mergeable_state` is `dirty`; if it is `unknown`, read it again a few seconds later);
- no human request is unanswered, in the chat or on GitHub;
- no usage pause is under way. **Waiting for a usage reset isn't idle**, so the work isn't stranded.

**When you become idle**, after reporting:
1. Note the time as `IDLE_SINCE`, in UTC (`date -u +%Y-%m-%dT%H:%M:%SZ`). It is used only in the GitHub `since=` filters.
2. Check `CronList` for an existing idle check and don't add a second. Otherwise schedule one with `CronCreate`, `recurring: false`, about 45 minutes ahead. Cron fields are in **local time**, not UTC, so take them from `date -d '+45 min' '+%M %H %d %m'`. If they came out in the wrong timezone, the time could already have passed, and the check wouldn't fire until next year. Pin the minute, hour, day and month, and nudge the minute off :00 or :30. Prompt: `Lead idle check (idle since <IDLE_SINCE>): follow /lead-start "Idle shutdown".`
3. Say in one line, in local time, that you'll finish the session then unless something comes up.

**If anything arrives first** (a message from the human, an agent report, a notification), delete the check with `CronDelete` and carry on. Go idle again afterwards if nothing is left.

**When the check fires**, look again:
- run `python3 scripts/board.py promote`, then step 2;
- read GitHub activity since `IDLE_SINCE`, over REST: `gh api "repos/4OH4/news-bot/issues/comments?since=<IDLE_SINCE>&per_page=100"` (comments on issues and PRs), `gh api "repos/4OH4/news-bot/pulls/comments?since=<IDLE_SINCE>&per_page=100"` (review comments), and `gh api repos/4OH4/news-bot/pulls/<n>/reviews` for each open PR (`submitted_at` after `IDLE_SINCE`);
- check each open PR's `mergeable_state` again, since a merge can leave another PR conflicting.

Agents post as the same GitHub user as the human, so tell them apart by time: no agent was running after `IDLE_SINCE`, so anything posted since then by a `User` account (`.user.type == "User"`) came from the human. Ignore `Bot` accounts, such as the Claude GitHub Action's replies. Don't act on a comment that mentions `@claude`: the Action (`.github/workflows/claude.yml`) handles those, and the lead and the Action would otherwise both work on the same branch. Act on anything else: a change request goes to a delivery manager, and an answer on a `needs-human` issue unblocks that issue.

If there is work, do it and carry on. If there is none, run `/lead-finish` unattended. Don't wait for, or ask, the human anything. After that, schedule nothing more.

## Task messages

The briefs live in `.claude/agents/`, so each message carries only the specifics. Don't paste the briefs.

Architect:

```
Phase <n> ("<goal>"). <For a revision: the design problem, and what it affects.>
```

Delivery manager:

```
Batch: issues #<A>, #<B>, #<C>, in this order. Consuming issues outside the batch: <list, or "none">.
<Anything specific: open PRs to finish, a chain for one worker, issues flagged high risk.>
```

A one-off rework on a Tier C PR after the human's comments goes to a delivery manager too:

```
Rework PR #<P> (issue #<N>, branch `<branch>`). Changes: <each requested change>.
```
