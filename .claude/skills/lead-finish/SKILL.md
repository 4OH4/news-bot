---
name: lead-finish
description: End a lead development session for news-bot cleanly. Waits for or accounts for running workers, tidies worktrees and branches, syncs the board, records a handoff note for the next session, and tells the user what's ready to merge. Use when the user says they're stopping, wrapping up, switching to a new chat, or runs /lead-finish.
---

# Lead session: finish

## 1. Account for workers

List any worker or reviewer agents still running. For each one, either:
- wait for it to report, then continue its PR as in `/lead-start` step 4; or
- if the user is leaving now, note it as still running. Its worktree will be kept and listed next session.

Never remove a worktree that a running worker or reviewer is using.

## 2. Tidy

Run `python3 scripts/lead.py finish` from the repo root. It:
- updates `main`;
- checks the pre-commit hook;
- removes merged worktrees and branches;
- syncs the board;
- prints the status.

Anything with uncommitted or unmerged work is **kept and listed**. Never force-delete those. Report them instead.

## 3. Hand off

Update the `lead-handoff` memory file, replacing its contents. Use absolute dates. Record:
- what merged this session, and which PRs are open and ready to merge (cards in "Human review"), in order;
- what is In progress, in Agent review or kept back, and why;
- the next steps: which issues become Ready after the pending merges, which `needs-human` PRs to prepare next, and any decision waiting on the user.

Don't copy information the board or git history already holds. Record only what the next session needs to act.

## 4. Report

Tell the user, briefly:
- PRs ready to merge, in order;
- anything only they can do;
- how to resume: start a new conversation in this folder with `/lead-start`.
