# Phase 2 milestone report

Phase 2's goal: the pipeline runs hourly on GCP, unattended, with rule-based ranking, and publishes a feed snapshot.

## Try it

You need `gcloud` signed in to the project. **No agent has run these commands, because agents have no GCP identity yet, so every step is untested** and your run is the first test. Steps 1, 3 and 4 only read cloud data. Run them from the repo root.

**1. Open the latest feed from the cloud bucket: when it was made, and how many items it has.**

```bash
PROJECT=$(gcloud config get-value project)
mkdir -p ~/.newsfeed
gcloud storage cat gs://$PROJECT-content/feed.json > ~/.newsfeed/feed.json
jq '{generated_at, run_id, items: (.items | length)}' ~/.newsfeed/feed.json
```

**2. See the top of the ranked feed, with why each item scored as it did.**

```bash
jq -r '.items[:15][] | "\(.score)  \(.domain)  \(.title)\n    \(.score_breakdown)"' ~/.newsfeed/feed.json
```

Do these look like things you'd want to read? That's the judgement only you can make.

**3. See the latest run's stats: success rate per site, failing feeds and failed articles.** The first line is needed once, to let local commands use your Google account.

```bash
gcloud auth application-default login
export NEWSFEED_STORAGE=gcp NEWSFEED_GCP_PROJECT=$PROJECT NEWSFEED_GCS_BUCKET=$PROJECT-content NEWSFEED_CONFIG_DIR="$PWD/config"
uv run --project backend newsfeed report
```

**4. Browse the articles as readable pages.** This copies stored article text to your machine, outside the repo, so don't add it to git. It reads every stored article from Firestore, so run it once rather than repeatedly.

```bash
uv run --project backend newsfeed preview --out ~/.newsfeed/preview
xdg-open ~/.newsfeed/preview/index.html
```

**5. Check the cost so far.** Open https://console.cloud.google.com/billing, pick the project's billing account, then Reports. Nothing in the repo reports cost: the budget only sends alerts.

## Goal check

Exit criterion: the pipeline runs hourly unattended for a week within budget, and the feed snapshot is accessible.

**You closed the phase early on 2026-11-01, before the week ran,** because the system is deployed and collecting data and Phase 3 is more useful. So the week is waived, not met. The table shows what is proven and what isn't.

| Criterion | Evidence | State |
|---|---|---|
| Scheduler fires hourly | Scheduler `ENABLED`. You confirmed five runs complete, 10:00 to 14:00 UTC on 2026-11-01 | Shown |
| The pipeline runs and writes a feed | Those five runs predate the first deployed image, and the sample image also completes while writing nothing | **Not checked** |
| Unattended for a week | The 15:00 UTC run on 2026-11-01 should be the first on a deployed image, which nobody has confirmed | **Waived by you** |
| Within budget | No figure. Step 5 shows it, and the budget alert still runs | **Not checked** |
| Feed snapshot accessible | Private bucket with public access blocked, readable with your account. Step 1 is its first test | **Not checked** |
| Live extraction check from GCP (ADR-0003) | Issue #89 needs read access to Firestore. Carried forward | **Not done** |
| Hourly runs finish inside the 50-minute timeout and don't overlap | No evidence. Check from the run history during issue #89 | **Not checked** |

## Decisions to confirm

Merging this report accepts all of these. Comment on any you reject, and it becomes a normal issue.

**Look at these first**

| Decision | Reason | To undo |
|---|---|---|
| PR #141: with `NEWSFEED_RETRY_PARTIAL` on, a kept partial is re-fetched on its backoff schedule after a 403, 404, 402 or robots refusal | Setting is off by default, retries are capped, and `Fetcher` still checks robots.txt. Revisit before turning it on | Make `should_retry` honour `error_type` for partials |
| PR #148: an openly served copy of a paywalled story keeps its text in the feed | Nothing is bypassed | Hide the copy when its target is `paywalled` |
| PR #148: when a target recovers, the item's id and URL change, so a seen story returns as new and feedback keyed on `article_id` won't carry over | Keeps publish simple. Revisit in the Phase 3 design | Give items a stable story id |
| PR #148: older code can't load rows that carry `declared_canonical`, which also affects re-running CI on an older commit | Cheap at this stage | Strip the field |

**Infrastructure and deployment**

| Decision | Reason | To undo |
|---|---|---|
| PR #140: Terraform pinned at 1.15.7 in CI | 1.6.0 fails at init with an expired signing key | Change the version |
| PR #140: the `Infra` job is not a required check | A path-filtered required check would block other PRs | Add it to the ruleset |
| PR #140: `hashicorp/setup-terraform` v4.0.1, pinned by SHA, MPL-2.0 | Needed for the check | Remove the job |
| PR #145: no build cache | Less-trusted workflows could otherwise feed a privileged build | Add the cache back |
| PR #153: alert uses the `gh` CLI on the runner | No new action to pin | Delete the `alert` job |
| PR #153: an existing alert is found by exact title | No label has to exist. Renaming it opens a new one | Change the lookup |
| PR #154: the `terraform` check must be `success` | `skipped` and `neutral` shouldn't pass | Relax the check |
| PR #157: a `skipped` status is exempt only when fully backticked | Reviewer brief asks for backticks | Change the exemption |

**Extraction and feed**

| Decision | Reason | To undo |
|---|---|---|
| PR #141: a kept retry records the latest `error` and `error_type` | Shows the latest state | Drop those updates in `_keeping_partial` |
| PR #141: an extractor `paywalled` retry replaces a stored partial; 402, 403 and skipped outcomes keep it | Keeps the better result | Change the keep rule in `pipeline.py` |
| PR #141: `extract --url --save` of a stored partial applies the keep rule | Same rule everywhere | Skip it in `extract_saved_url` |
| PR #141: `NEWSFEED_RETRY_WINDOW_DAYS` capped at 36500, rejecting NaN and infinity | Bad values fail early | Remove `le` and `allow_inf_nan` |
| PR #146: a page and its failed canonical target can both stay stored | Readable text is never lost | Remove the `FAILED` check in `_refine_canonical` |
| PR #148: new optional field `declared_canonical` | Publish needs it to show one copy | Remove the field and the publish step |
| PR #148: a partial target under 150 words counts as no readable text | Matches `min_words` | `_can_absorb` returns True for every partial |
| PR #148: with no text on either row, the feed shows the target as a link | One item per story | Drop `a.id == end` from the key in `_one_per_story` |
| PR #148: chains are followed end to end; two chain cases can still show twice, with nothing lost | A duplicate is cheap | Remove the `a.url` entries in `by_address` |
| PR #148: new `duplicates` publish counter | Makes the effect visible | Remove it and `BuiltFeed.duplicates` |

**Process**

| Decision | Reason | To undo |
|---|---|---|
| PR #151: the lead wrote it directly, outside its scope | At your request | None needed |
| The lead doesn't stop at 150k tokens: it rewrites the handoff and carries on | Your decision. Stopping would end unattended work. Revisit after the first mid-batch compaction | External restart |
| PR #165: an `## Evidence` heading above the collapsed blocks ends "For human review" | Simpler than stopping at `<details` | Remove the heading and change `SECTION_RE` |
| Phase 2 closed before the week ran, and before the live extraction check that ADR-0003 adds to its exit | Your decision, 2026-11-01. The check is deferred to issue #89, with no new ADR | Keep watching the runs: nothing is switched off |

**Follow-ups settled.** All of these were undecided in the Phase Log. The lead dropped them because each is cheap and recovers or fails safe. Comment on any you want kept, and it becomes an issue.

| Follow-up | Why dropped |
|---|---|
| A kept 402 stores `http_4xx` while run stats count it `paywalled` | A label difference in stats |
| After a blob failure following a re-key, `content_path` breaks | The next retry recovers it |
| The `[:500]` cap is untested; a tiny positive backoff passes validation | No harm if either is wrong |
| The self-canonical case in the cleared-link `return` has no test (PR #148) | A duplicate at worst |
| Two `lead.py` test gaps (PR #157); PR #165's test holds its own copy of the heading | Process scripts, not a regression |
| The template comment holds "Pending independent review" | It fails the gate, so it fails safe |
| `worker.md` doesn't fetch `milestone`; PRs from the lead or delivery manager get none | The milestone field drives nothing |
| PR #157's body has no Verification section | The PR comments hold the results |
| `ubuntu-latest` moving to Ubuntu 26 | Accepted: a break shows in CI, and pinning is then one line |

**Also accepted earlier:** PR #124 raised the LaTeX entry's word range in `eval/urls.yaml`, taking the `chain` score from 98.9% to 100%. PR #133's own `Confirm later` entries were accepted when you merged it, and aren't repeated here.

## What was built

The pipeline is deployed to GCP. Cloud Scheduler starts a Cloud Run Job every hour, which ingests, extracts, ranks with the rules in `config/interests.yaml`, and publishes `feed.json` to Cloud Storage, using Firestore for stored articles. A merge to `main` deploys automatically, and a failed deploy opens an issue, which was proven by forcing a failure (issue #158). Paywalls and member previews are detected, non-clean items show as a headline, summary and link, failed extractions retry with a cap, and `newsfeed report` shows failures by site and reason.

Agent-merged PRs: [#141](https://github.com/4OH4/news-bot/pull/141) (retries keep the better result), [#146](https://github.com/4OH4/news-bot/pull/146) (canonical links keep readable text), [#148](https://github.com/4OH4/news-bot/pull/148) (one copy of a story). Earlier in the phase, the lead merged [#122](https://github.com/4OH4/news-bot/pull/122). You can revert any of them. You merged the other PRs in the phase.

## Risks and parked work

- **Closing early leaves the cloud unproven.** Nobody has yet confirmed a feed written by a deployed image, the cost, or whether datacenter IPs get blocked. Phase 3 builds a reader on this feed, so run steps 1 and 5 before signing off. They take a few minutes. If the feed is thin, that's the first thing to fix.
- **Parked PRs:** none.
- **Carried forward to Phase 3:**
  - Issue #89, the live extraction check, which also covers the run-length check. You run it, or you create the development service account. If overall `ok` is below 85%, ADR-0003 says to revisit with a new ADR.
  - Issue #92, page headline as title. It waits until a source without feed titles is enabled.
- **Issue #50, Google News:** still open. robots.txt disallows `/rss/search` and `/rss/articles/`, so Google News stays disabled. The lead recommends closing it as not planned. It carries forward until you do.
- **PR #160 merged before its review finished,** without the milestone flag you approved, which issue #161 added. Its late review found one defect, fixed by issue #162: the gate could wrongly refuse a clean PR, but let nothing unsafe through.
- **History rewrite:** 76 `refs/pull` refs keep the old commits on GitHub, with the old address, unless GitHub Support purges them.
- **Rollback:** older code can't load rows with `declared_canonical`. Strip the field to recover.
- **Running in the cloud:** Cloud Run Job, Scheduler (hourly, and left on), Firestore, Cloud Storage, Artifact Registry and Secret Manager, with a budget alert.

## Process measures

Counted from PR #133 onwards, when this way of working began. Phase 1 had no equivalent, so there is no earlier milestone to compare against.

| Measure | Target | Phase 2 |
|---|---|---|
| PRs merged by agents | At least 80% | 3 of 18 (17%), **well below target**. The 15 others were all automation, CI or infrastructure, which is Tier C by rule. All 3 pipeline code PRs were agent-merged |
| Stop-the-line events | Few, with reasons | 1: issue #134, permissions only you could apply. Issues #89 and #50 are older |
| Agent-merged PRs reverted or needing a behaviour fix | At most 1 in 10 | 0 of 3 reverted. PR #146's design needed PR #148 as a planned follow-up, so 1 of 3 by a strict reading |
| Decisions you overturn | At most 1 in 5 | Set by this review |
| Rework rounds per PR | Falling | Falling: #131 four reviews, #146 five rounds, #147 two, later batches one each, PR #157 none |
| Defects review caught that tests should have | Falling | At least 5 PRs in the phase (#146, #148, #153, #154, #160), 0 in PR #157 |
| Issues reworked because the plan was wrong | Reported | 2 (#131, #146: the issues named a symptom) |
| Usage pauses, usage per issue | Reported | None recorded. No source for usage per issue |

**Thresholds (§10).** Keep them all but one. "PRs merged by agents" should count pipeline code only, because this phase's automation work was Tier C by rule, so the measure says nothing about trust in agents' code. Three PRs is too few to judge the "1 in 10" rate, so keep it and look again after Phase 3.

**Test author's cost (§14).** Cost can't be measured, because nothing records usage per issue. No usage pause was recorded, so it did not visibly push against the limits. The benefit is unproven: PR #146's first two tests expected a design that was later dropped, and the defects that mattered were still caught by review, not tests. 5 PRs ran through it, 3 of them pipeline code. Keep it through Phase 3, which has real feature work, and judge it again then.

**Compaction (§16).** The hook passed on a manual `/compact`. An automatic mid-batch compaction hasn't happened, so that recovery, and whether scheduled checks survive it, are still unknown. Context grew about 55k tokens a batch, from 44k to 210k. Keep the current approach, and leave the question open.

## Process review

**Top friction**

1. **Compound commands refused:** about 15 in batch 1, then every batch, in every role. A refused compound can apply only its earlier part. All five briefs now state the real limit, so no change is proposed. Watch whether refusals fall in batch reports.
2. **An owner merge can overtake review work:** PR #160 merged during its review, and PR #167 merged with your comment unactioned. PRs #164 and #165 went through as drafts, which worked, though a draft didn't stop PR #167's comment being missed. Proposed: all Tier C PRs stay drafts until the Delivery review, and the lead checks for your comments before queueing and after each merge. Shows up as zero overtaken reviews.
3. **Review notes left for you to work out:** PR #164 ended with a recommendation nobody had actioned, and a Resolved item that keeps its note still reads as open. Proposed: `worker.md`, `reviewer.md` and the delivery manager brief require every item to be resolved or accepted in one line. Shows up as your friction notes.

**Changes from the last milestone:** none to review, since this is the first under this way of working. PRs #151 and #167 changed the process during the phase: the cost-of-failure statement and the firm review limit are too recent to judge, since later PRs were small automation work. Rework fell to two rounds on #147, before them, because its design was settled first. The Phase Log stopped friction being lost.

**The lead's phase-boundary gap:** the architect hasn't run yet, because Phase 2 predates it. PR #167 fixed its brief, so Phase 3 is its first real test.

**Your notes:** the Phase Log comments of 2026-11-01. Cost of failure and warnings scaled to the stage, delivered in PR #151. PRs readable without the issue, delivered in PR #160. Process steps as defaults, delivered in PR #167. Review notes are item 3 above.

**For you to decide, no work until then:** you approve and the lead merges. It needs a machine user, a token you set up, your `gh` login removed from this machine, and a gate change. The lead recommends a machine user over a GitHub App, though the GitHub details are unverified. It removes one source of friction, item 2, at the cost of an account to keep. No decision is needed for Phase 3 to start.

**Carried forward, smaller:** the reviewer's `Closes #<N>` check on `Refs` PRs; whether `tier.py` should count reviews; the §10 change above; a `REVIEW.md` line that a new item in a first-match list must be checked against those above it.

`AUTOMATION.md` §15 and §16 are updated in this PR.

## Next phase

Proposed goal: Phase 3, the reader client, an installable PWA on Android with an offline cache, and feedback capture through a thin API restricted to you.

Found this phase that affects the plan:

- **Reading the feed:** the bucket is private, so the client needs a route to it. The Phase 3 design decides this.
- **Feedback identity:** PR #148's changing item ids need settling before feedback is keyed on `article_id`.
- **Framework:** the PWA framework is still an open ADR.
- **Evidence still owed:** issue #89 and the cost check run alongside Phase 3, so a blocked-source problem doesn't surface only when you read the feed.

## Cost of failure

For Phase 3, updated in `CLAUDE.md` in this PR, and merging confirms it. Still development, with no user who isn't also a developer. What changes:

- **Stage:** you become a user once the reader works, so "not even the owner yet" goes.
- **Newly matters:** your feedback, which is the first stored data that can't be recreated, and the new API or client being reachable by anyone but you.
- **Still cheap:** poor ranking, a duplicate item, and a failed run the next run recovers, though you will now see them.
