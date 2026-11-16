# Deferred work

Work or decisions that were looked at and put off on purpose, so they stay in one place instead of moving from report to report. Anything with an issue stays on the board and isn't repeated here.

The lead reviews this file at each milestone. An entry that is due gets a new decision: address it, accept it and remove it, or keep it with a new date.

| Item | Why deferred | Look again when | From |
|---|---|---|---|
| The owner approves PRs and the lead merges, through a machine user | Needs a new account and token, the owner's `gh` login removed from the machine, and a gate change. The lead recommends a machine user over a GitHub App; the GitHub details are unverified | The owner chooses. Phase 3 doesn't need it | Phase 2 |
| Automatic compaction: mid-batch recovery, and whether scheduled checks survive it | No automatic one yet. A manual mid-batch `/compact` on 2026-11-07 lost nothing, and the 15-minute check survived. See `AUTOMATION.md` §16 | The first mid-batch compaction | Phase 2 |
| Pins Dependabot does not update: the `# syntax=` directive in `backend/Dockerfile` and `CLIENT_PLAYWRIGHT_IMAGE` in the `Makefile` | Dependabot reads only `FROM` lines, and the Playwright image must match `@playwright/test` in `client/package.json`. To refresh by hand: `docker buildx imagetools inspect <tag>` gives the digest, then edit the pin; CI's `docker` and `client` jobs prove it | Phase 4 milestone | Phase 3 (#256) |
| Per-article region rules (publisher URL paths, feed categories), finer than the source-level regions of the preferences epic (#331, #335) | Needs a per-article region rule set; Phase 4 tags regions per source instead (`docs/phases/phase-4.md`) | If source-level regions prove too coarse in use | Phase 3 (views) |
| Importance: the publisher's own front-page order, a "breaking" badge | The coverage count is built in Phase 4 (#324, #325); these two need other signals | Phase 4 milestone, with clustering in use | Phase 3 (views) |
| A ranking recipe per view (e.g. News with low topic weight and a short half-life) | News is plain newest-first for now | If News feels noisy or Longer badly ordered | Phase 3 (views) |
| Word-count rules for Longer: a floor to drop short New Yorker pieces (138 of about 183 long-form items in 14 days, median 866 words), and long pieces from general news feeds such as BBC In Depth | Tags only in the first version | 2026-11-22, a week after the tabs reached the phone | Phase 3 (views) |
| `guardian-longread` extracts about 93 words (median; 6 of 10 `partial` in 14 days), so its pieces show as links | Found while sizing views; not yet investigated | With #89, the live extraction check (still 6 of 10 `partial` at the Phase 3 milestone) | Phase 3 (views) |
| Sync view preferences across devices (stored through the API) | One device today | A second device | Phase 3 (views) |
| Authoritative wire sources (Reuters, AP), which have no public RSS | Source question, separate from views | Next source review | Phase 3 (views) |
| `tier.py`: name the line-placement rule in its refusal, parse trailers more loosely, a higher tier for `api/auth.py` | Past the three-issue limit for Phase 3; each gate refusal costs one fix | Phase 4 milestone | Phase 3 |
| Terraform drift: the API service's `scaling` block shows zeros to null in `terraform plan` | Harmless to apply but untested, and nothing depends on it | The next Terraform change to the API service | Phase 3 |
| Bootstrap the interest profile from a Google Takeout export (search and YouTube history), `PROJECT_PLAN.md` §6 Phase 4 | Weeks of reads already exist, and the export is the most sensitive data the project could hold (`docs/phases/phase-4.md`) | Phase 4 milestone, if the profile from reads is weak | Phase 4 design |
