# Deferred work

Work or decisions that were looked at and put off on purpose, so they stay in one place instead of moving from report to report. Anything with an issue stays on the board and isn't repeated here.

The lead reviews this file at each milestone. An entry that is due gets a new decision: address it, accept it and remove it, or keep it with a new date.

| Item | Why deferred | Look again when | From |
|---|---|---|---|
| The owner approves PRs and the lead merges, through a machine user | Needs a new account and token, the owner's `gh` login removed from the machine, and a gate change. The lead recommends a machine user over a GitHub App; the GitHub details are unverified | The owner chooses. Phase 3 doesn't need it | Phase 2 |
| Whether the test author is worth its cost | Only 5 PRs ran through it, and usage per issue isn't recorded | Phase 3 milestone | Phase 2 |
| Automatic compaction: mid-batch recovery, and whether scheduled checks survive it | It hasn't happened yet. See `AUTOMATION.md` §16 | The first mid-batch compaction | Phase 2 |
| Agents running from the main checkout | 2 cases, nothing lost | A third case adds a check to the brief | Phase 2 |
| Pins Dependabot does not update: the `# syntax=` directive in `backend/Dockerfile` and `CLIENT_PLAYWRIGHT_IMAGE` in the `Makefile` | Dependabot reads only `FROM` lines, and the Playwright image must match `@playwright/test` in `client/package.json`. To refresh by hand: `docker buildx imagetools inspect <tag>` gives the digest, then edit the pin; CI's `docker` and `client` jobs prove it | Phase 4 milestone | Phase 3 (#256) |
| Add to `REVIEW.md` under "Behaviour on real data": a URL transform built for identity (dedupe keys) must not be reused to fetch pages without checking what else it changes on real links (trailing slashes, query order, AMP), because each change is a different request to the site | Lesson from the #274 review; the issue's own wording asked for `normalise_url`. Held for the milestone review rather than its own issue | Phase 3 milestone | Phase 3 (#274) |
| Check that Al Jazeera English gives full articles, not teasers | The one article checked for #274 extracted only 227 words. Removing the source is a one-line change to `config/sources.yaml` | After a week of runs with the source (`make inspect-gcp ARGS="articles --source al-jazeera-english --since 7d"`) | Phase 3 (#274) |
| Choose News sources in Settings, sent to the API as a `sources` filter on the view | First version of views kept minimal (owner, 2026-11-15) | After a week of using the views | Phase 3 (views) |
| Region filter (UK, Europe, World) from publisher URL paths and feed categories | Needs a per-article region rule set | Phase 4 planning | Phase 3 (views) |
| Importance: coverage count across sources, the publisher's own front-page order, a "breaking" badge | Coverage count needs Phase 4 story clustering | When Phase 4 clustering lands | Phase 3 (views) |
| A ranking recipe per view (e.g. News with low topic weight and a short half-life) | News is plain newest-first for now | If News feels noisy or Longer badly ordered | Phase 3 (views) |
| Word-count rules for Longer: a floor to drop short New Yorker pieces (138 of about 183 long-form items in 14 days, median 866 words), and long pieces from general news feeds such as BBC In Depth | Tags only in the first version | After a week of using Longer | Phase 3 (views) |
| `guardian-longread` extracts about 93 words (median; 6 of 10 `partial` in 14 days), so its pieces show as links | Found while sizing views; not yet investigated | Next extraction review | Phase 3 (views) |
| Remember the last-used tab as the landing view | Feed is the default | Phase 3 milestone | Phase 3 (views) |
| Sync view preferences across devices (stored through the API) | One device today | A second device | Phase 3 (views) |
| Authoritative wire sources (Reuters, AP), which have no public RSS | Source question, separate from views | Next source review | Phase 3 (views) |
