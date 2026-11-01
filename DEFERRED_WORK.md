# Deferred work

Work or decisions that were looked at and put off on purpose, so they stay in one place instead of moving from report to report. Anything with an issue stays on the board and isn't repeated here.

The lead reviews this file at each milestone. An entry that is due gets a new decision: address it, accept it and remove it, or keep it with a new date.

| Item | Why deferred | Look again when | From |
|---|---|---|---|
| The owner approves PRs and the lead merges, through a machine user | Needs a new account and token, the owner's `gh` login removed from the machine, and a gate change. The lead recommends a machine user over a GitHub App; the GitHub details are unverified | The owner chooses. Phase 3 doesn't need it | Phase 2 |
| Whether the test author is worth its cost | Only 5 PRs ran through it, and usage per issue isn't recorded | Phase 3 milestone | Phase 2 |
| Automatic compaction: mid-batch recovery, and whether scheduled checks survive it | It hasn't happened yet. See `AUTOMATION.md` §16 | The first mid-batch compaction | Phase 2 |
| Agents running from the main checkout | 2 cases, nothing lost | A third case adds a check to the brief | Phase 2 |
