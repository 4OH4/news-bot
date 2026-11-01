---
name: lead-milestone
description: Prepare a news-bot phase milestone, or act on the human's sign-off. Runs the demo, writes the milestone report with a status and recommendation for every friction item and proposal, opens the report PR, and after the human merges it closes the phase and opens the process issues. Use when the phase's work is done (`/lead-start` step 2, item 5), when the milestone report is awaiting sign-off, or when the human has merged it.
---

# Lead session: milestone

You are the **lead** in `AUTOMATION.md` §5.1. `/lead-start` sends you here at two points: when the phase's work is done (part 1, "Prepare the milestone"), and when the human has reviewed or merged the report (part 2, "Milestone review"). Open `AUTOMATION.md` §4 and §10 for the rationale behind the report and its measures.

The Phase Log (`/lead-start` step 5) is the source for the report. Agent reports and lead sessions don't persist, so anything not in the log is not in the report.

## 1. Prepare the milestone

1. Run as much of the demo as you can from a clean checkout, following the phase design, or `PROJECT_PLAN.md` §6 if there's none. Where you can't, such as on the human's phone or without a cloud identity, the report says which steps are untested, and the human's own run tests them.
2. Write the report at `docs/milestones/phase-<n>.md`, following `AUTOMATION.md` §4 and the pattern of `docs/phase1-review.md`, so it takes a few minutes to read:
   - **Try it:** the exact command or URL, and what to do with it.
   - **Goal check:** the exit criterion and the evidence that it is met. Where a criterion mentions elapsed time, give the evidence so far. The human judges when it's enough.
   - **Decisions to confirm:** every `Confirm later` item from the Phase Log, with its reason and how to undo it. Give any follow-up still undecided a state; carried forward is fine.
   - **What was built:** a short summary, with links to the PRs agents merged (`label:agent-merged`).
   - **Deferred work:** review `DEFERRED_WORK.md` at every milestone. Each entry due for a look gets a status: address, accept (with the reason), or stays deferred (with a new date to look again). Remove resolved entries from the file.
   - **Risks and parked work:** anything unresolved, any PR the review process could not clear, anything carried forward to the next phase, and any cloud resources still running.
   - **Process measures:** the numbers in `AUTOMATION.md` §10.
   - **Process review:** those numbers against the previous milestone, a status table (below), how the last milestone's changes turned out, and the human's own friction notes. Update `AUTOMATION.md` §15, including what didn't work.
   - **Next phase:** proposed goal, and anything found this phase that changes the plan.
   - **Cost of failure:** your statement for the next phase, and what changed. Update `CLAUDE.md` in the same PR, so that merging the report confirms it.
3. **The status table.** Every friction item, follow-up and proposal in the Phase Log gets one row and one status, so the owner never has to ask "so what?":
   - **address:** the recommended change and the measure that will show it worked;
   - **accept:** the reason it is left as it is;
   - **defer:** an entry in `DEFERRED_WORK.md` at the repo root, saying why it is deferred and when to look at it again. The row points to that entry.

   The table replaces a "top three friction points" list. The limit of three `process` issues per phase still applies (part 2), so choose which addressed items become issues; the others stay in the table with their status.
4. Update the phase's status in `PROJECT_PLAN.md` §6 in the same PR, and mark the phase **done** there: `/lead-start` step 2 relies on that word. Open it as a draft PR on `issue-<n>-phase-<n>-milestone`, closing the Phase Log. It is Tier C. Start `subagent_type: reviewer` on it, to check the report's claims against the Phase Log and the evidence, clear what it finds, then mark the PR ready.
5. Tell the human the milestone is ready, with the link and the "Try it" line.

## 2. Milestone review

Sign-off is the human merging the report PR. Nothing moves on automatically, and there is no time limit. Fixes during the review go through a delivery manager as normal. Comments on the report are change requests, and each rejected decision becomes a normal issue in the next phase, which doesn't block sign-off unless the human says so.

Once it is merged:
1. Close the phase's GitHub milestone, and move carried items to the next phase's milestone.
2. Open a `process` issue for each item the report marked **address** that the human didn't reject, at most three per phase, stating what should improve and which measure shows it. `process` issues follow the normal tier rules, so changes to the automation are Tier C. Only process fixes that the next phase relies on go first, and the rest carry forward.
3. Check that each **defer**red item has its entry in `DEFERRED_WORK.md`.
4. Go back to `/lead-start` step 2 for the next phase.
