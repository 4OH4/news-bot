---
name: lead-start
description: Start (or resume) a lead development session for news-bot. Syncs the repo and board, works out where the phase stands, starts the architect or a delivery manager for the next batch, handles usage pauses, and reports. Use when the user says "continue", "start", "resume development" or runs /lead-start.
---

# Lead session: start

You are the **lead** in `AUTOMATION.md` §5.1: accountable for delivering the phase. This skill has what you need, so don't read `AUTOMATION.md` routinely: open it only for the rationale behind a rule. The human never edits files, and reviews a working demo at each milestone.

To protect your context, read batch reports, not diffs or per-agent results, and send agents issue numbers instead of restating issues.

These steps are defaults, not barriers. Use judgement, and where a step doesn't fit, propose skipping or reordering it. Once the human agrees in the chat, record it in one line in the Phase Log. Stop-the-line items, the tiers, the architecture rules and merging only through `tier.py` are outside this.

## 1. Sync

Run `python3 scripts/lead.py start` from the repo root. It:
- fast-forwards `main`;
- syncs dependencies and checks the pre-commit hook;
- removes merged worktrees and branches;
- syncs the board.

Read its "Kept back" list. A worktree that is unmerged or has uncommitted changes means an earlier agent's work was left behind, which is normal after a usage cut-off. Check its PR (`gh pr list --head <branch>`). A new delivery manager picks up work from the board and open branches, so list anything it needs to know in its task message.

Also check for an open deploy alert: `gh issue list --repo 4OH4/news-bot --state open --search '"Deploy failed: the Cloud Run Job is running an older image" in:title'` (the title is set in `.github/workflows/deploy.yml`). One means the last deploy failed and the Cloud Run Job runs an older image, so treat it as work to handle before the next batch. It is not on the board.

Read the comments on PRs merged since the last check (the commands are in step 4; the handoff note records the last PR checked), because the owner may have commented before or while merging, and turn any unactioned one into a follow-up issue.

Then read the handoff note, the Phase Log (step 5) and "Cost of failure" in `CLAUDE.md`, and run `python3 scripts/usage.py`.

Last, schedule the recurring status check (see "Status check"). Run `CronList`, and only if it shows no status check, create one with `CronCreate`, `recurring: true`, at minutes off :00 and :30 (for example `7,22,37,52 * * * *`). Prompt: `Lead status check: follow /lead-start "Status check".` A second `/lead-start` in the same session therefore adds nothing. Recurring jobs expire after 7 days and fire only while the session is open and not mid-turn.

## 2. Find where the phase stands

The current phase is the first one after the last phase marked done in `PROJECT_PLAN.md` §6. Take the first of these that applies:

1. **The milestone is awaiting sign-off:** the report PR is open and not a draft. A draft report is a preview: it doesn't mean this, and it isn't an unfinished PR for item 3. Act on any comments from the human (step 7, which points to `/lead-milestone` part 2). Building stops until the human merges it, though the architect may draft the next phase, and that design PR can merge.
2. **The phase has no design:** no `docs/phases/phase-<n>.md`. Phases 0 to 2 predate the architect and need none. Confirm the goal and exit criterion from `PROJECT_PLAN.md` §6, then start the architect (step 3).
3. **Ready issues, other than those agreed to carry forward, or unfinished PRs exist:** start the next batch (step 4).
4. **The phase is waiting on evidence or the human:** for example, an exit criterion that needs elapsed time, until the human says it's enough. Say what it's waiting for, and go idle.
5. **The phase's work is done:** every issue in the phase is closed or parked, or the human has agreed what carries forward, recorded in the Phase Log. The Phase Log itself doesn't count, because the report closes it. Prepare the milestone (step 6, which points to `/lead-milestone` part 1).

The six PRs open when this model began (#123, #124, #125, #126, #129, #130) finish under the rules they were written for: the human merges them, and `tier.py merge` refuses them. Keep them in the merge queue until they are merged, and send them back for a rebase if they conflict.

## 3. Design

Start `subagent_type: architect` with `isolation: worktree`, naming the phase or epic. When it reports:
- check the design and issues against the project's goals and later phases, and send it back if they don't fit;
- send the phase design PR through a delivery manager as the first batch. It takes the normal route without a test author: an independent reviewer, then a merge;
- copy the architect's `Confirm later` items to the Phase Log (step 5).

Call the architect back whenever a design problem emerges, and it updates the file through the same route.

## 4. Run a batch

Group the Ready issues into a batch of about 5 to 8 issues and about 2 hours of work, in dependency order, with no two in progress touching the same files. Run `python3 scripts/board.py promote` first. A large epic takes several batches. Only one delivery manager runs at a time. A batch is fixed when it starts: do not add issues to a running delivery manager. Issues that become Ready mid-batch wait for the next batch, and you start the next delivery manager as soon as the current one reports.

If `usage.py` prints `pause:`, don't start a batch: go to "Usage pauses" below.

Add the batch's issues and the start time of the last status check (`SINCE`) to the handoff note, then start `subagent_type: delivery-manager` with `isolation: worktree` and `run_in_background: true`, using the task message below. Use `model: opus` only if Sonnet has fallen short on an earlier batch. The delivery manager is isolated, so the harness refuses its git commands against another agent's worktree. It frees a test author's worktree with plain `git worktree remove <path>`, which refuses a dirty tree, and takes the SHA from the test author's report. If that's refused it sends you the path: you aren't isolated, so look in that worktree and decide. If you start a one-off worker or reviewer while the batch runs, `SendMessage` the delivery manager so it counts towards the `parallel:` limit. Then wait for messages from it:

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
- **Needs human:** for the stop-the-line list, treat it as stop-the-line, or decide it, add a `Confirm later` entry to the PR header, and mark the item "**Resolved** in <head SHA>: <decision>". A residual risk that matters, at any review, always goes to the human by the referral below, and you can't settle it.
- **A PR that failed its second review:** judge what's left against "Cost of failure" in `CLAUDE.md`. Choose one of: the issue is wrong, so the architect revises it; accept what remains as cheap and mark each item "**Resolved** in <head SHA>: accepted, <reason>"; park the PR; refer to the human (below); or rework plus a third review. A third review is for a failure that matters, with the reason in the Phase Log. A defect that matters is never merged on an unreviewed fix and never accepted as cheap: its fix gets a third review, on the rework only. Only cheap defects can be accepted without one. If the third review finds another defect that matters, decide between one more narrow review of that fix, with the reason in the Phase Log, and referring it to the human. A fix for a defect that matters is still never unreviewed. Repeated defects on one path usually mean the issue names a symptom, so settle the design before any more rework. Parked PRs go in the milestone report, and stop the line only if other work depends on them.
- **Referral to the human:** a residual risk that matters, which rework can only reduce, always goes this way, at any review. Add the `hold` label, ask in the chat with what remains, your recommended answer and which figures are estimates, record the answer in the Phase Log and mark the item resolved. After the human answers, you remove `hold`. A Tier C PR stays a draft until then, and the delivery manager doesn't mark a held PR ready.
- **Stop-the-line** (`AUTOMATION.md` §6): add a comment on the issue with the shortest exact question or steps, label it `needs-human`, and tell the human in the chat. Other work continues.

Tier C PRs stay drafts until the Delivery review, so a ready PR means agents are done with it. The delivery manager reads a PR's comments (`gh api repos/4OH4/news-bot/issues/<n>/comments`, `.../pulls/<n>/comments` for inline review comments and `.../pulls/<n>/reviews` for review bodies) before `gh pr ready`, and sends you any unactioned owner comment. Turn it into a change (rework) or a follow-up issue. Agents and the owner post as the same GitHub user, so tell an owner comment from an agent's by time (no agent was running when it was posted) or, failing that, by content (an agent's carries a heading such as "Independent review" or "Delivery review", or its attribution line). Rework or a push puts the PR back to draft (`gh pr ready <P> --undo`) and the card back to Agent review (`python3 scripts/board.py set <N> "Agent review"`). A reviewer is always started with `isolation: worktree`.

When the batch report arrives, review it:
- curate the Phase Log (step 5) from the report: its `Confirm later` entries, its "Friction" section and its follow-ups, because batch reports and lead sessions don't persist to the milestone;
- check the numbers against `AUTOMATION.md` §10, and raise anything drifting;
- run `python3 scripts/lead.py context`. If it prints `checkpoint:`, or it prints `context unavailable` and this session has run three batches, rewrite the handoff note as in `/lead-finish` step 4, and tell the human they can restart to save usage. Then carry on: don't stop for this, because work must continue unattended;
- report to the human (below), then start the next batch or go to step 6.

A one-line status saying what it's waiting for isn't a stop: notifications resume it, so don't start a second one. If a delivery manager stops early with no report and nothing is running (`ListAgents`), start a new one. All its state is on GitHub and the board.

## 5. The Phase Log

Keep one issue titled "Phase <n> log" open for the phase (`AUTOMATION.md` §4). Create it the first time you need it. The body is the curated summary, with the sections What's new, Decisions to confirm, Friction, Proposed process changes and Follow-ups. Comments hold raw notes.

- **As it happens:** add a comment for anything you tell the human will be recorded, in the same step you say it, and for your own friction, decisions made outside a PR, and the human's corrections and notes. Name the "Cost of failure" line a decision relied on. The handoff note isn't a record.
- **At each batch close:** read the comments on PRs merged since the last check (`/lead-start` step 4), then rewrite the body to take in the batch report and the comments. Merge friction with what's there and count it, and give each follow-up a state: drop, an issue number, carried forward, or undecided. At the milestone (`/lead-milestone`), a follow-up with an issue carries forward as that issue, drop is accept, and the rest need a status.
- **If the human comments on work in progress,** in the chat or here, turn each comment into an issue or a follow-up. They can try it at any time.
- **If the human corrects the cost of failure,** or the project gains a user who is not also a developer, data that can't be recreated, new spending or anything newly public, propose the change to `CLAUDE.md` at once as a Tier C PR (`AUTOMATION.md` §6).

Anything the human rejects at the milestone becomes a normal issue.

## 6. Prepare the milestone

Run `/lead-milestone` part 1, "Prepare the milestone": the demo, the report with its status table, and the draft PR. It has the detail.

## 7. Milestone review

Run `/lead-milestone` part 2, "Milestone review", for the human's comments and for what to do once the report is merged. Then come back to step 2 for the next phase.

## Usage pauses

`scripts/usage.py` reads the 5-hour and weekly limits (`AUTOMATION.md` §11). A delivery manager that hits about 90% reports which limit and when it resets, then ends.

- **5-hour limit:** schedule a one-shot check with `CronCreate`, `recurring: false`, for a few minutes after the reset. Cron fields are in **local time**, and `usage.py` prints the local reset time. Pin the minute, hour, day and month, and nudge the minute off :00 or :30. Note the pause in the handoff. Prompt: `Lead usage check: follow /lead-start "Usage pauses".` When it fires, run `usage.py`, then start a new delivery manager, which picks up from the board. If the limit is still high, schedule another.
- **Weekly limit:** start no new batches. Flag the pause and the reset time for the human, leaving the remaining allowance for their use, and wait for their word. Note it in the handoff. Delete the recurring status check (`CronDelete`) so that nothing runs against the owner's allowance, and re-create it (step 1) when work resumes.
- **Human override:** the human can tell you to pause, or to carry on past 90%.

If usage can't be read, work continues as normal, and the delivery manager tells you, so that pacing doesn't turn off unnoticed. A scheduled check only fires while your session is open. Otherwise the next `/lead-start` picks the work up, so a pause always goes in the handoff note. A pause isn't idle: stay open. During a 5-hour pause the status checks keep firing but start no new work, and they don't count towards idle shutdown. During a weekly pause there is no status check (see above). The prompt cache lasts about an hour, so a 15-minute check mostly re-reads cached context and is cheap; a weekly pause leaves the remaining allowance to the owner, so nothing runs then.

## After compaction

A hook tells you when your context has just been compacted. Agents keep running through it, so carry on without restarting anything:

1. Read the handoff note and the Phase Log.
2. Run `ListAgents`, and don't start a second delivery manager if one is running.
3. Run `CronList`. If the status check is gone (and no weekly pause is under way), create it again as in step 1. If the handoff records a usage pause and its check is gone, schedule that again too.
4. Check the open PRs for anything waiting on you, such as an "Evaluation scores" comment that was requested and is missing.

If the handoff is out of date, trust the board and GitHub.

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

## Status check

The recurring check (step 1) fires every 15 minutes while the session is open: during batches, while waiting for the human, and during usage pauses. It refreshes your view of GitHub so that a merge unblocks work, and a comment gets answered, without the human having to prompt. Keep it cheap: don't re-read files or the Phase Log, and use REST only (the GraphQL budget). `board.py promote` is the one GraphQL call.

Take the time at the **start** of each check (`date -u +%Y-%m-%dT%H:%M:%SZ`) and let `SINCE` be the time taken at the start of the previous check, so nothing posted during a check is missed. Keep it in the session, and write it to the handoff note when a batch starts and in `/lead-finish`. After a restart or compaction, take it from the handoff note or, failing that, 30 minutes ago.

1. Read what changed since `SINCE`:
   - PRs merged: `gh api "repos/4OH4/news-bot/pulls?state=closed&sort=updated&direction=desc&per_page=30" --jq '.[] | select(.merged_at != null and .merged_at > "<SINCE>") | .number'`, then read each one's comments as in step 4;
   - comments on issues and PRs: `gh api "repos/4OH4/news-bot/issues/comments?since=<SINCE>&per_page=100"`;
   - review comments: `gh api "repos/4OH4/news-bot/pulls/comments?since=<SINCE>&per_page=100"`;
   - reviews: `gh api repos/4OH4/news-bot/pulls/<n>/reviews` for each open PR (`submitted_at` after `SINCE`).

   Keep comment bodies out of your context. Read each call with a `--jq` projection of the author type, time, URL and first line only (for example `--jq '.[] | [.user.type, .updated_at, .html_url, (.body | split("\n")[0])] | @tsv'`), and skip comments whose first line is an agent heading ("## Independent review", "## Delivery review", "## Evaluation scores") or that carry the attribution line. Open the full body only for a comment that looks like the owner's, using the content cues in step 4 ("Run a batch") for telling the owner's comments from an agent's.
2. Run `python3 scripts/board.py promote`, which moves issues unblocked by a merge to Ready.
3. Check each open Tier C PR's `mergeable_state` (`gh api repos/4OH4/news-bot/pulls/<n> --jq .mergeable_state`; `dirty` means conflicting; if it is `unknown`, read it again a few seconds later).
4. Check for an open deploy alert (step 1).
5. Act on comments that haven't been actioned yet. Unless a usage pause is under way, start or continue work that a merge unblocked, but only one delivery manager runs at a time: if one is running, tell it (`SendMessage`) instead of starting another. Turn owner comments into rework or follow-ups, and answer replies on `needs-human` issues. A conflicting Tier C PR goes to a delivery manager to rebase.

Agents post as the same GitHub user as the human, so tell them apart by time where you can (no agent was running when it was posted) and otherwise by content, as in step 4: an agent's comment carries a heading such as "Independent review" or "Delivery review", or its attribution line. Ask if unsure. Ignore `Bot` accounts, such as the Claude GitHub Action's replies. Don't act on a comment that mentions `@claude`: the Action (`.github/workflows/claude.yml`) handles those, and the lead and the Action would otherwise both work on the same branch.

**Reporting.** A check that finds nothing says nothing to the human, or at most one short line. Report only what you did because of it.

## Idle shutdown

The session often runs unattended, and once the agents have finished it can only wait for the human. Rather than sit open until the prompt cache goes cold, the lead finishes itself; a fresh `/lead-start` is cheap.

**Idle** means all of these hold:
- no agent is running;
- there is nothing to start: step 2 leads only to waiting for the human;
- no stop-the-line item has an answer waiting;
- no Tier C PR you are watching has become conflicting;
- no human request is unanswered, in the chat or on GitHub;
- no usage pause is under way. **Waiting for a usage reset isn't idle**, so the work isn't stranded.

**Three quiet checks.** Count consecutive status checks that found nothing to do and left you idle. Any check that finds work, any message from the human and any agent report resets the count to zero. When the third quiet check in a row fires, which is 30 to 45 minutes after the work ran out, run `/lead-finish` unattended. Don't wait for, or ask, the human anything. `/lead-finish` deletes the recurring check, and you schedule nothing more.

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
