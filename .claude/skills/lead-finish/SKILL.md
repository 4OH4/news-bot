---
name: lead-finish
description: End a lead development session for news-bot cleanly. Accounts for a running delivery manager, tidies worktrees and branches, syncs the board, records a handoff note for the next session, and tells the user where the phase stands. Use when the user says they're stopping, wrapping up, switching to a new chat, or runs /lead-finish.
---

# Lead session: finish

## 1. Account for agents

List any delivery manager or architect still running. For each one, either:
- wait for it to report, then handle the report as in `/lead-start`; or
- if the user is leaving now, note it as still running. Its worktrees will be kept and listed next session, and a new delivery manager can pick up from the board.

Never remove a worktree that a running agent is using.

## 2. Tidy

Run `python3 scripts/lead.py finish` from the repo root. It:
- updates `main`;
- checks the pre-commit hook;
- removes merged worktrees and branches;
- syncs the board;
- prints the status.

Anything with uncommitted or unmerged work is **kept and listed**. Never force-delete those. Report them instead.

## 3. Reconcile the Phase Log

Before you replace the handoff note, go through this session's reports to the human. Anything you said would be recorded, any decision or friction you raised, and any correction from the human must be in the Phase Log (`/lead-start` step 5). Add what's missing now, because the handoff note isn't a record and the next finish overwrites it.

## 4. Hand off

Update the `lead-handoff` memory file, replacing its contents. Use absolute dates. Record:
- the phase, its stage (`/lead-start` step 2) and the Phase Log's issue number;
- what the last batch merged and what is parked, and the merge queue (cards in "Human review") with what each merge unlocks;
- what is In progress, in Agent review or kept back, and why;
- any usage pause: which limit and when it resets. A scheduled check dies with the session, so the next `/lead-start` picks the work up;
- the next steps, including the next batch and any decision waiting on the user.

Don't copy information the board, git history or the Phase Log already holds. Record only what the next session needs to act.

## 5. Report

Tell the user, briefly:
- where the phase stands, and the report sections from `/lead-start` that have anything in them (at a milestone, also the milestone report's "Process review", which is step 6 of `/lead-start`, not a report section);
- anything only they can do;
- how to resume: start a new conversation in this folder with `/lead-start`.

If the idle check in `/lead-start` started this finish, no agents are running and the human isn't watching. Don't wait for or ask them anything. Delete any idle check still scheduled (`CronList`, then `CronDelete`). Open the report with a banner that can't be missed when they come back:

```
## ✅ Lead session finished at <HH:MM, date> (idle since <HH:MM>)
Nothing more can be done until you review. Start a new conversation in this folder with /lead-start to resume.
```
