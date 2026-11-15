# Phase 3 milestone report

Phase 3's goal: an installable PWA on your Android phone with an offline cache, and feedback capture through a thin API that only you can use.

## Try it

You have used the app daily since 7 November, so most of this is a check that nothing has slipped since. Steps 1 to 4 are on the phone, and step 5 is on the laptop from the repo root.

1. **Open the app from the home screen.** Settings should show commit `5f19e64` (PR #306, the last merge) and its build time. If it says `dev` or an older commit, close and reopen the app once: it updates silently.
2. **Read and react.** Try the News, Feed and Longer tabs. Open a story, Like it with the heart beside the meta, and switch light and dark in Settings.
3. **Offline.** Turn on aeroplane mode, close and reopen the app. The feed shows, and the liked story opens with its text. Turn aeroplane mode off.
4. **Stranger check.** Open the app's address in a browser signed in with any other Google account. You should get Google's "You don't have access" page. To find the address: `gcloud run services describe newsfeed-api --region=europe-west2 --format='value(status.url)'`.
5. **Your feedback reached the store:**

   ```bash
   make inspect-gcp ARGS="feedback --since 24h"
   ```

   The like from step 2 is at the top.

6. **Cost.** Open https://console.cloud.google.com/billing, pick the billing account, then Reports. Agents can't read billing, so the last figure on record is from 7 November (below).

**What agents checked on 2026-11-15:** an unauthenticated request to the service, for `/` and for `/api/feed`, is sent to Google sign-in (302) and never reaches the app. The service runs the image built from `5f19e64`. All 256 hourly job runs since 5 November succeeded. Steps 1 to 3 and the signed-in part of step 4 need your phone and your account, so agents haven't run them; you ran all of them on 7 November when the app went live.

## Goal check

Exit criterion: you use it daily in place of the Google feed for two weeks.

**You closed the phase on 2026-11-15, after nine days of the fourteen,** because a week of use has taught what it will at this stage. So the two weeks are waived, not met. The evidence so far:

| Criterion | Evidence | State |
|---|---|---|
| Installable on Android | You installed it on 7 November (#179) | Shown |
| Read today's feed, open an article | Feedback in the live store has events on every day from 7 to 15 November (reads every day, opens from 8 November): 78 opens and 69 reads across 14 sources, 4 to 15 events a day in the first week and 31 to 33 a day from 13 November | Shown |
| Like one, read it again offline | 5 like events (3 likes standing, 2 retracted) and 1 dismiss in the store. Offline reading checked by you on 7 November; the offline cache has e2e tests | Shown |
| Only you can use it | Unauthenticated requests are redirected to sign-in (checked today); you saw another account refused on 7 November | Shown |
| Daily, for two weeks | 9 consecutive days | **Waived by you** |
| In place of the Google feed | Only you can say. You raised at least 6 requests from daily use (#271, #272, #290, #300, #304, #307), which suggests you used it as your feed | Your judgement |

**The pipeline under it** also held: 256 of 256 hourly runs succeeded from 5 to 15 November, averaging 4.3 minutes (the latest 24 averaged 4.0, the longest took 6), well inside the 50-minute timeout.

## Decisions to confirm

Merging this report accepts all of these. Comment on any you reject, and it becomes a normal issue. PRs you merged yourself (#193, #200, #202, #209, #222, #234, #245, #261, #277, #278, #281, #287, #295, #305, #306) had their `Confirm later` entries accepted when you merged them, and aren't repeated.

**Look at these first**

| Decision | Reason | To undo |
|---|---|---|
| Views use the score stored at rank time, never recomputed (PR #303, ADR-0013). Mutes and `interests.yaml` changes don't reach items older than 72 hours; disabling a source or setting its weight to 0 does apply at once | Recomputing every view each run costs reads for a cheap gain. Phase 4 changes ranking anyway | A per-view ranking recipe that re-scores |
| No copy check when publishing `feed.json` or views (PRs #299, #303). An expired copy opens the reader's "Read it on the original site" state | The reader already handles a missing copy; the check guarded nothing | Restore `_content_exists`, about 200 Cloud Storage reads a run |
| News 300, Feed 500, Longer 200 items, `max_items` at most 1,000 (PR #303); about 2,000 Firestore reads a run | Your simpler design | Edit `config/views.yaml` |
| `bbc-future` tagged `long-form`, so it shows in Longer; ADR-0012 stands where ADR-0013 doesn't replace it (PR #292) | Longer needed more than one long-form source | Remove the tag in `config/sources.yaml` |
| `Repository.list_articles(source_ids=...)`, an additive contract change; more than 30 sources are queried in groups of 30; one extra index (status plus source) unused today (PR #301) | Lets each view read only its sources | Remove the parameter and filter in publish; drop the index |
| `story_id` in the feed, `Feedback` unchanged (PR #197). A story can show once more as new if its target ages out or declares another canonical | Simple, and a repeat is cheap | Drop the field; the client falls back to `id` |
| `BlobStore.ref_for` contract change (PR #198) | The API needs a ref it can serve | Document refs as keys, remove `ref_for`, change the fake |
| Saved copies at `saved/<id>.html`, amending ADR-0005 decision 6; `like` with value 0 retracts; 413 for over-size bodies (PR #223) | Liked articles keep their text past retention | A repository method that updates one field |
| Al Jazeera English added; SMH and The Age left out as paywalled (PR #274) | Your decision on 8 November. Their hidden text would be a paywall bypass | Edit `config/sources.yaml` |
| The fetch URL is the link with only tracking parameters removed, not the dedupe key (PR #274) | Each other change would be a different request to the site | Call `normalise_url` again |

**The Phase 3 design.** The 26 rows in `docs/phases/phase-3.md`, "Decisions to confirm later" (PRs #186, #219), each with its undo: IAP as the gate with no `allUsers`; one origin, client in the image; the IAP verifier through `google-auth`; IAP's OAuth settings and your grant outside Terraform; Reconnect by navigation; sign-out clearing local data then IAP's cookie; one guarded sign-out; 503 when the verifier can't check; `X-Requested-With` on feedback; JWT on static files; the CSP pinned by tests; Preact, TypeScript and Vite; dismiss and mute in the client only; 50 articles prefetched; DOMPurify as a second pass; client tooling in Docker; 0 to 1 API instance; the mocked API with a shared fixture; a separate feedback database; Like on the reader only; undo in place of the card; #178 before `serve`. Recorded as ADR-0009 and ADR-0010 (PR #199, numbered 0009 and 0010).

**API and backend**

| Decision | Reason | To undo |
|---|---|---|
| PR #208: `google-auth` and `requests` (Apache-2.0); `email_verified` set for IAP; a blank audience refused; keys fetched each check; transport error 503, other failures 401 | One user makes few calls | Verify with PyJWT and a key cache |
| PR #212: `fastapi` (MIT); `..` paths 404; `no-cache` by default; 10 s clock skew; the auth log leaves out the token, messages and email | Safe defaults | Change `api/` settings |
| PR #220: `uvicorn` (BSD-3); 127.0.0.1 without auth locally, 0.0.0.0 on `PORT` with IAP; ASCII-only owner email | Local runs can't be reached from outside | Change `serve` |
| PR #233: the client directory refused above the working directory; `0.0.0.0` refused as Host locally, and in IAP mode | Closes a `.env` exposure | Remove the checks |
| PR #221: an undo cancels the earlier event of the same kind on the same article; muted sources use all feedback | Matches what you see | Change `report` |
| PR #246: `--since` limits the whole feedback section of `report` | Consistent output | Limit only the list |

**Client**

| Decision | Reason | To undo |
|---|---|---|
| PR #227: `idb` (ISC), `fake-indexeddb` (Apache-2.0), `happy-dom` (MIT); events ordered by `seq`; which HTTP answers move an event to `rejected` | Standard, small libraries | Replace the storage module |
| PR #262: a `localStorage` outbox, one key per event, surviving a sign-out Discard; legacy pending reads stamped when moved | A tap survives leaving at once | Back to IndexedDB only |
| PR #235: events queued during sign-out are kept; delete by id; 30 s POST limit; a bottom-sheet dialog | No feedback lost unsent | Change `signOut` |
| PR #248: a duplicate `read` rather than a lost one if the page dies mid-hand-off | Losing feedback matters; a duplicate is cheap | Drop the hand-off |
| PR #251: only the title link sends `open`; a `<details>` card menu; cards wait for one IndexedDB read | One event per tap | Change `FeedItemCard.tsx` |
| PR #291: the image link is hidden from Tab and screen readers, so each card has one link | Accessibility | Make it a normal link |
| PR #263: list origin in `history.state`; theme choice in Settings | Back returns to the right list | Remove the flag |
| PR #224: 50 articles prefetched, images online-only; two test-author tests fixed in their own commit | Cache size | Change the constant |
| PR #226: DOMPurify 3.4.17 (MPL-2.0 or Apache-2.0); `<style>` cut by regex before it | Second sanitiser layer | Remove it |
| PR #218: 503 on first load is "The first feed is not ready yet"; layout and colours the agent's choice. PR #250: an app header, later replaced by your tabs | Superseded by your later client PRs | None needed |
| PR #207: Reconnect and sign-out unverified on a phone; four test-author e2e tests adjusted with reasons | Your daily use since has shown no problem | None needed |

**Process and docs**

| Decision | Reason | To undo |
|---|---|---|
| PRs #232, #244, #253, #259: placement and wording of `REVIEW.md` lines (8 rather than 9 in #232, one already covered) | Docs only | Edit `REVIEW.md` |
| PR #237: ADR-0011 records sanitised article HTML on the app origin, with the wider risk you accepted | Your decision, 7 November | A new ADR |
| You closed Phase 3 after 9 of 14 days | Your decision, 15 November | None needed; you keep using the app |

**Follow-ups settled** are in the Process review below.

## What was built

The reader runs on Cloud Run behind Google's Identity-Aware Proxy (IAP), for your account only, and each merge deploys it. On the phone it installs as an app with News, Feed and Longer tabs, a reader with typography and dark mode, likes, dismiss and mute, a Liked list, an offline cache of 50 articles, and a text-size setting. Feedback is queued on the phone, so it survives going offline or leaving at once, and reaches Firestore through the API. `newsfeed report` and `newsfeed inspect` show it. The pipeline now publishes the three views as well as `feed.json`.

Agent-merged PRs, 36 in all (27 product code, 9 docs and design): [#186](https://github.com/4OH4/news-bot/pull/186), [#197](https://github.com/4OH4/news-bot/pull/197), [#198](https://github.com/4OH4/news-bot/pull/198), [#199](https://github.com/4OH4/news-bot/pull/199), [#207](https://github.com/4OH4/news-bot/pull/207), [#208](https://github.com/4OH4/news-bot/pull/208), [#212](https://github.com/4OH4/news-bot/pull/212), [#218](https://github.com/4OH4/news-bot/pull/218), [#219](https://github.com/4OH4/news-bot/pull/219), [#220](https://github.com/4OH4/news-bot/pull/220), [#221](https://github.com/4OH4/news-bot/pull/221), [#223](https://github.com/4OH4/news-bot/pull/223), [#224](https://github.com/4OH4/news-bot/pull/224), [#226](https://github.com/4OH4/news-bot/pull/226), [#227](https://github.com/4OH4/news-bot/pull/227), [#232](https://github.com/4OH4/news-bot/pull/232), [#233](https://github.com/4OH4/news-bot/pull/233), [#235](https://github.com/4OH4/news-bot/pull/235), [#237](https://github.com/4OH4/news-bot/pull/237), [#238](https://github.com/4OH4/news-bot/pull/238), [#244](https://github.com/4OH4/news-bot/pull/244), [#246](https://github.com/4OH4/news-bot/pull/246), [#248](https://github.com/4OH4/news-bot/pull/248), [#250](https://github.com/4OH4/news-bot/pull/250), [#251](https://github.com/4OH4/news-bot/pull/251), [#253](https://github.com/4OH4/news-bot/pull/253), [#259](https://github.com/4OH4/news-bot/pull/259), [#260](https://github.com/4OH4/news-bot/pull/260), [#262](https://github.com/4OH4/news-bot/pull/262), [#263](https://github.com/4OH4/news-bot/pull/263), [#274](https://github.com/4OH4/news-bot/pull/274), [#291](https://github.com/4OH4/news-bot/pull/291), [#292](https://github.com/4OH4/news-bot/pull/292), [#299](https://github.com/4OH4/news-bot/pull/299), [#301](https://github.com/4OH4/news-bot/pull/301), [#303](https://github.com/4OH4/news-bot/pull/303). You can revert any of them. You merged the other PRs in the phase.

## Deferred work

`DEFERRED_WORK.md`, reviewed. This PR updates it.

| Entry | Status |
|---|---|
| Whether the test author is worth its cost | **Accept: keep it.** Every feat and fix ran test-first; reviews in batches 10 to 15 found no defects, against six rework rounds in batch 3. Its cost showed as size, not usage: its tests pushed #216 into a split and PR #292 to 1,482 of 1,500 lines. That is process item 1 below. Removed |
| Agents running from the main checkout | **Accept.** One more case on 1 November (reviewers shared the main checkout, one detached its HEAD, nothing lost); PR #194 made reviewers run with `isolation: worktree`, with no case since. Removed |
| `REVIEW.md` line: a URL transform built for identity mustn't be reused to fetch (from #274) | **Address:** added to `REVIEW.md` in this PR. Removed |
| Check that Al Jazeera English gives full articles | **Accept: keep the source.** 8 days: 441 of 651 articles `ok` (median 535 words), 199 `partial` (median 63 words, likely video and live pages, shown as links), 11 failed. Removed |
| Remember the last-used tab | **Accept.** You didn't ask for it in daily use, and a tab tap already reloads at the top. Removed |
| Getting back to the top of a long list | **Resolved into #307**, which you asked for. Removed |
| Automatic compaction | **Stays deferred.** A manual `/compact` mid-batch on 7 November kept the delivery manager, its workers and the 15-minute check running; no automatic one yet. Same trigger |
| Machine user for merges | **Stays deferred.** You choose |
| Pins Dependabot doesn't update | **Stays deferred,** Phase 4 milestone |
| Choose News sources in Settings; word-count rules for Longer | **Stays deferred.** The tabs reached your phone on 15 November, so there isn't a week of use yet. Look again on 2026-11-22 |
| `guardian-longread` extracts few words (partial median 81) | **Stays deferred.** Still 6 of 10 `partial` in 14 days. Look again with #89 |
| Region filter, importance, ranking recipe per view, sync across devices, wire sources | **Stays deferred.** Phase 4 design reads them; triggers unchanged |

## Risks and parked work

- **Article HTML on the app origin (#239).** You accepted the residual risk twice (PRs #226 and #237, ADR-0011): a bypass of both sanitisers and the CSP could persist through the service-worker cache and send forged feedback. **Recommendation: accept, and leave #239 open under ADR-0011's "Revisit when"** (its list of triggers, including a user who isn't a developer, production, a sanitiser advisory, a wider allowlist and a looser CSP). It moves to the Phase 6 milestone, off `agent-ready`.
- **#307 (header slides away on scroll, back-to-top button, Liked moves to Settings)** wasn't started, because you closed the phase first. It carries to Phase 4 and can run beside the Phase 4 design.
- **Carried forward:** #89 (live extraction check; its Firestore part is now unblocked), #92 (page headline as title). **#50, Google News:** robots.txt still disallows the feed paths. Recommendation: close as not planned on sign-off.
- **Cost:** your billing export on 7 November showed about £0.97 gross for 31 October to 7 November, rising to 16 to 19p a day, mostly job CPU, with the free tier making the net bill about £0. Nothing newer on record; step 6 of Try it shows it. Job runs have since fallen from 4.7 to about 4.0 minutes on average. The views add about 2,000 Firestore reads a run, about $0.15 a month.
- **IAP bypass check.** When you accepted PR #200's risk, #179 was to show that a direct call bypassing IAP gets 403. That result isn't on record. The reviewer read the live config on 15 November: Cloud Run's invoker check is on, IAP's service agent is the only invoker, and IAP is enabled, so the risk looks contained. Accept.
- **Terraform drift:** `terraform plan` shows the API service's `scaling` block going from zeros to null. Not applied; deferred (below).
- **Leftover worktrees:** seven under `.claude/worktrees/` hold only scratch files or a reviewer's leftover edits on merged branches. Force removal is denied to agents. To clear them: `git worktree list`, then `git worktree remove --force <path>` for each `agent-*` and `before-272`.
- **Parked PRs:** none.
- **Running in the cloud:** the hourly Cloud Run job and Scheduler, the `newsfeed-api` service (0 to 1 instance) behind IAP, Firestore with two new indexes, Cloud Storage, Artifact Registry, Secret Manager, and the budget alert.

## Process measures

| Measure | Target | Phase 2 | Phase 3 |
|---|---|---|---|
| PRs merged by agents (product code) | At least 80% | 3 of 3 pipeline PRs | 27 of 35 (77%), **just below**. Of the 8 you merged, 4 were look-and-feel changes you chose to see first (#278, #281, #295, #305), and 4 were Tier C by path (#202 scaffold, #222 deploy, #277 Makefile, #306 deploy workflow). Leaving out the four you chose to see, 27 of 31 (87%). Agents also merged 9 docs and design PRs |
| Stop-the-line events | Few, with reasons | 1 | 3, all needing you: the dev account's two read-only roles (7 Nov), the first `terraform apply` with IAP's OAuth settings (#179), and the IAP spike's setup (#196). Plus 5 residual risks referred to you and accepted (#186's bill, #200, #226, #237, #280) |
| Agent-merged PRs reverted or needing a behaviour fix | At most 1 in 10 | 0 of 3 | **4 of 27 (15%), over target.** #233 closed a way for a website to read a local `.env` while `newsfeed serve` (#220) ran in development; #246 made #223's feedback input checks strict; #263 fixed #226's sanitiser letting `data:` URLs through; and PR #292's views were redesigned on your correction (PRs #301, #303). The first three were found by review and fixed in planned follow-up batches within a day; none reached your phone as a bug |
| Decisions you overturn | At most 1 in 5 | Set by review | During the phase: the views' time windows and copy check (PR #292). At this review: set by your comments |
| Rework rounds per PR | Falling | Falling | Falling: 6 PRs reworked in batch 3, one round each in batches 4 to 11, none in batches 12 to 15 |
| Defects review caught that tests should have | Falling | At least 5 PRs | Batch 3 (test helpers writing the field the code read, #234, #235), then no defects in batches 10 to 15 |
| Issues reworked because the plan was wrong | Reported | 2 | 4: #175 and #184 were too big and split mid-flight; #243's premise was wrong; #288 was redesigned (#296, #297) |
| Usage pauses, usage per issue | Reported | None | None. 5-hour use peaked at 61%, weekly at 24%. No source for usage per issue |

**Thresholds.** The agent-merge share is close, and the gap is your choice to see look-and-feel changes, so from Phase 4 count those PRs separately. The behaviour-fix rate is over its target. All three fixes came from review notes known at merge time and shipped in the next batch, so the rule that tightens is not the tiers but the merge: **a review note that matters (#233: a secret readable in development) is fixed before merge, not in a follow-up.** That goes into process item 2. Keep the thresholds and look again at the Phase 4 milestone.

**Compaction (§16).** One manual mid-batch compaction, with nothing lost. No automatic one yet. The approach stays.

## Process review

**How the Phase 2 changes turned out.**

| Change | Outcome |
|---|---|
| Tier C PRs as drafts; the lead reads comments (PR #194) | No review was overtaken by a merge in Phase 3. The comment check was later narrowed to your account only (PRs #285, #287), after review found a stranger's comment could be acted on |
| Review notes resolved or accepted in one line (PR #195) | Batch reports now end with "Dropped as cheap" lines with reasons, and you raised no friction about loose notes |
| `/lead-milestone` skill (PR #189) | This report is its first use |
| "PRs merged by agents" counts pipeline code | Used above; it now needs the look-and-feel adjustment |

**Status table.** Every friction item, proposal and follow-up with no issue from the Phase Log. Merging accepts the recommendations.

**Address.** Each becomes a `process` issue after sign-off; three is the limit.

| # | Friction | Evidence | Recommendation | Shows it worked |
|---|---|---|---|---|
| 1 | Sizing: tests crowd the 1,500-line limit, and big issues split mid-flight | #175 (518 source lines) and #184 (610) split into sub-issues; #216 split after review because its tests were 1,020 lines; PR #292 at 1,482 lines, 887 of them tests; client test helpers counted as source | The architect sizes issues with tests included, under 400 source lines; each split part gets its own sub-issue and only the last closes the parent; the test author keeps tests lean (one test per acceptance example, shared setup); `tier.py` counts client test helpers as tests | No issue split mid-flight; no PR above 1,300 lines from tests |
| 2 | The delivery manager's lifecycle is implicit, and its brief lags the habits that worked | Each batch has a tail (review, rework, merge) in which nothing new starts: approved PRs waited up to 17 minutes (batch 4), and up to an hour before `tier.py queue`; the one handover (7 November) was agreed in chat. Late messages to a busy delivery manager (3); a resumed worker didn't wake it (3 stalls on PR #277); worker reports reached the lead (5); `Confirm later` marker with no text (3); a review with no SHA (#287); `make client-install` missing (2); a Needs human item reached you first (PR #237); a test-only rework merged without a second review (PRs #261, #262) | Four named stages, each manager limited to its own batch, and the next batch starting when the current one reaches Closing; plus the brief lessons. Details below the table | The gap from one batch's last worker finishing to the next batch's first worker starting falls; no PR is touched by two delivery managers; none of the brief lessons recur in Phase 4 batch reports |
| 3 | Issues leave out what the next agent needs | Look-and-feel changes could merge without you seeing them (#271, your note); an issue named the REST field for a `gh ... view` call (#286); "How to verify" said `make run`, which fetches live feeds (#297); the test author picked an unnamed helper's signature and tested through the whole app (#289, #296) | Architect brief and issue template: a change to how the client looks gets `needs-human` and phone-size before and after screenshots, light and dark (the #271 pattern); issues name the helpers and interfaces tests call; verification commands don't touch the network | No look-and-feel PR reaches you unseen; no worker edits a test author's tests for an unnamed interface |

**Item 2 in detail: the delivery manager's lifecycle.** The lifecycle part is being built now, at your request, in issue #309 (PR #310), which has the full design and ten worked examples. After sign-off, item 2's process issue covers only the brief lessons below.

*Stages.* A delivery manager moves through four stages. They're the lead's working model, not state kept anywhere else:

1. **Building:** at least one of its issues is still with a test author or worker.
2. **Closing:** every issue in its batch has a PR, and only review, rework, the Delivery review and merge remain. It sends the lead one line when it gets here, naming its open PRs.
3. **Paused:** a usage limit was hit. Running agents finish, then it reports and ends (as today).
4. **Ended:** its batch report is sent.

*Scope.* A delivery manager works only on the issues in its task message, the PRs that close them, and anything the lead names to it later by message. Its sweep on resume and its conflict check after a merge cover only its own PRs. "Pick up from the board after a stopped batch" goes: the lead lists carried work in the next task message. Its `REVIEW.md` docs PR stays part of its own batch.

*Overlap.* At most two delivery managers run, and the older one must be Closing. When a manager reaches Closing, the lead may start the next batch from Ready issues that share no files with the Closing batch's open PRs and don't depend on them. If none qualify, the lead waits for the report, as today. The new manager's task message names the other batch's issues as not its own. Nothing new is counted for the parallel limit: a Closing manager has no test authors or workers, and a rare rework worker may briefly go over it.

*The handover message.* When the older manager's report arrives, the lead sends the running manager one message naming the leftovers it now owns, for example "#330 is waiting for the owner; if comments come in, it's yours." A manager's scope grows only when the lead names something.

*Orphans.* Any open PR that no running batch owns (your later comment, a batch stopped by a pause) is the lead's: its 15-minute status check, which already runs `tier.py queue`, assigns it to a running manager or starts one.

*Unchanged:* the lead chooses every batch and batches stay fixed; each issue's steps, review limits, tiers and `tier.py merge`; usage pacing; escalation to the lead. With no independent Ready work, it runs exactly as today.

*Brief lessons, in the same issue:* never block in a foreground command while agents run; start a fresh worker for rework rather than resuming one by message; reviews state `Checked at <sha>`; text on the `Confirm later` line; `make client-install` first in a fresh checkout; the worker's rework read includes inline review comments; the lead's status check greps new comments for "Needs human"; a review note that matters is fixed before merge, not in a follow-up (#233); a test-only rework that changes no reviewed source needs no second review, even on a high-risk PR (this relaxes the rule, so it's your call).

*Files:* `.claude/agents/delivery-manager.md`, `.claude/skills/lead-start/SKILL.md`, the reviewer and worker briefs for their lines, and `AUTOMATION.md` §5.3 and §8. Tier C.

**Accept.** No action. Say so if you disagree.

| Friction or follow-up | Evidence | Why accept |
|---|---|---|
| Harness refusals of compound commands and force pushes | Every batch | The briefs state the limits and each refusal costs one retry. It hasn't blocked work |
| Board slips | New cards added to Done (#216, #217), a closed card left in Agent review (#175), two issues missing (#231, #236), promote moving a split parent to Done (#216) | One-line fixes by the lead, all caught at the next look |
| One-off slips, settled | ADR numbers collided (1); dev-account roles found one 403 at a time (2); your terminal ran as the dev account (1); a credentials gate left unconfirmed (settled by PR #280); a stranger's comment could be acted on (fixed by PRs #285, #287) | Fixed or one-off |
| Gate oddities | Tripped on the word "skipped" (1); stale header tiers (2); a quoted bold marker counts as real | Rare, and each fails safe |
| Smaller one-offs | The architect widened #178's scope unasked (1 Nov); a third design review found a new gap in the same threat model (1 Nov); test-author tests passed for the wrong reason at red (#297, caught by the worker); `make client-e2e` wipes before-screenshots (#295) | Each was caught in the same batch at the cost of a retry |
| Test author can't run e2e with red tests, or append to test files | 3 | The worker runs them; no defect slipped through |
| Flaky `update.spec.ts` | 6 failures before PR #238, none recorded since | Fixed in effect; the cause stays unconfirmed |
| Stray worktrees | 2 from before-screenshots, plus the leftovers above | You have the command; a nuisance only |
| Delivery manager overloaded, ready work idle, slow serial Delivery reviews | Batch 2 grew to about 12 issues; #181 idle; four approved PRs waited 17 minutes | Fixed batches (PR #214) and `tier.py queue` (#228) solved it: batches 8 to 15 ran one or two issues each with no queue waits |
| Two delivery managers in parallel lanes; your ticket-owner layer; a closer agent per PR; a "sole manager" stage with wider scope | Proposals from 7 November, and the milestone discussion on 15 November | Item 2's overlap at Closing gets the throughput without a standing second lane or an extra agent type. A stage that widens scope once the other manager ends would need a reliable "you're sole" signal and a rule that changes mid-run; the lead's handover message covers the leftovers instead |
| All PRs as drafts (your idea); align the two ways of accepting a note | Carried from Phase 2 | Tier C drafts already stop overtaking, with none in Phase 3; the wording difference caused no confusion |
| Reviewers' notes from batches 5 to 7 and 11 to 15 | Listed "drop unless daily use shows them" | A week of daily use showed none of them |
| Your checks: Android status bar over the title (PR #281), the card image (#290), Reconnect after sign-out (#207) | Asked after merge | You reported nothing in a week of use. Comment if you saw any |
| Deny `gcloud config unset` in settings | Offered to you on 7 November | You accepted the related ADC risk on PR #280; the deny rule adds little |
| Longer seeding retries, seeding log check | From PR #292 | Gone: seeding was removed in PR #303 |
| Untested guards in `publish/views.py`; a two-assert test for the version wiring (PR #306); the Firestore 30-source chunking tested only in the emulator | Reviewers' notes | Cheap: a wrong guard shows as a wrong view, fixed next run, and no view has 30 sources |
| `REVIEW.md` line: a test of a bot-bumped pin mustn't hard-code the version | Batch 5 candidate | Added to `REVIEW.md` in this PR |
| `REVIEW.md` line: a cost estimate counts per-request side charges such as logging | 1 November, PR #186's review | Added to `REVIEW.md` in this PR |
| ADR-0006 still said a missing copy shows as a link | PR #303 follow-up | Corrected in this PR |

**Defer.** Logged in `DEFERRED_WORK.md`.

| Item | Why | Look again when |
|---|---|---|
| `tier.py`: name the line-placement rule in its refusal, parse trailers more loosely, a higher tier for `api/auth.py` | Past the three-issue limit, and each gate refusal costs one fix | Phase 4 milestone |
| Terraform drift on the API service's `scaling` block | Applying it is harmless but untested; nothing depends on it | The next Terraform change to the API service |

**Your notes this phase.** Look-and-feel changes should reach you first: item 3. The views were too complex, and the lead ranked the cheaper design second because it needed a contract change: the redesign shipped (PRs #299, #301, #303), and the lesson is in `AUTOMATION.md` §15. A late start spotted on the agent map: item 2. The review-limit correction on 1 November and the referral rule were delivered in PR #194.

`AUTOMATION.md` §15 is updated in this PR.

## Next phase

Proposed goal: Phase 4, personalisation. Embeddings and interest profiles from what you read, a hybrid score with a visible breakdown, near-duplicate story clustering, and an A/B check against rules-only ranking. Exit: a measurable lift in open and read rate.

Found this phase that affects the plan:

- **Your signal is reads and opens, not likes.** Nine days gave 78 opens, 69 reads and 5 likes. Interest profiles should be built mainly from reads, with likes as a stronger but rare signal.
- **The A/B check needs time.** At 10 to 30 opens a day, a lift will take weeks to show. The design should size the test honestly, and say how long it runs, before building it.
- **Views exist now.** Personalisation should apply to the Feed view, leaving News in time order and Longer as it is, unless you say otherwise. Stored scores aren't recomputed (ADR-0013), so a new score reaches older items only as they age out, or the design re-scores.
- **Dismiss and mute** act only in the client today. Phase 4 reads them in the pipeline, as planned.
- **Clustering** unlocks the deferred "importance" item (coverage across sources).
- **Carried in:** #307 (your feed-screen request), #89, #92.

## Cost of failure

For Phase 4, updated in `CLAUDE.md` in this PR, and merging confirms it. Still development, with you as the only user. What changes:

- **Newly matters:** your personal history. A Google Takeout export, the embeddings and interest profile built from it or from your reads, must never reach the public repo, an issue, a PR, a log line or a third party beyond the embedding provider the design names. And a bill from embeddings: an embedding API call per article each hour is the first cost that grows with use.
- **Still matters:** losing your feedback, which now also includes the A/B record the exit depends on.
- **Still cheap:** poor ranking, including a personalised ranking that does worse than rules. That is what the A/B check is for.
