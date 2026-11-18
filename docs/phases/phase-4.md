# Phase 4 design: personalisation

**Goal:** a feed that also learns from what I read: embeddings, an interest profile, a hybrid score with a visible breakdown, near-duplicate story clustering, and reader preferences I set myself. **Exit:** a measurable lift in open and read rate versus rules-only (`PROJECT_PLAN.md` §6).

Design issue: #318. Phase log: #314. The reader preferences epic (#331) was added by the owner on 2026-11-16.

## Approach

```
hourly job (rank stage)                                   API (each time a view is served)
  embed new articles locally (bge-small, 384 numbers)       load view file (publish, unchanged except clusters)
  rebuild profile from feedback (at most every 6 h)    ──►  drop what the owner hides            (epic)
  store per article: score = rules x coverage               x Fewer / More factors               (epic)
                     breakdown: similarity, cluster_id      rules order    = score
publish: one item per cluster, coverage count               personal order = score x similarity
                                                            same diversity rule on both
                                                            Feed: interleave both, mark each item's arm
phone: sends view + arm with open/read/like  ──►  feedback store  ──►  next profile build, A/B report
```

**1. Embeddings run locally, not on Vertex AI.** `fastembed` runs `BAAI/bge-small-en-v1.5` (MIT, 384 dimensions) on the job's CPU (#321). The model is baked into the image (#322). Text: title, summary and the first 2,000 characters of the body. Only articles without an embedding are embedded, about 20 to 40 a run, which adds a few CPU seconds; there is no per-call bill, and the owner's reading history goes to no third party, which settles the "embedding provider the design names" in "Cost of failure": there is none. Vertex AI would be a new paid service (stop-the-line), a bill that grows each run, and a third party seeing which articles the owner reads. The `Embedder` port keeps a hosted model possible later. This closes the open question in `PROJECT_PLAN.md` §11 (ADR-0014).

**2. Embeddings are stored on `Article`.** The rank stage already reads and rewrites every article in its 72-hour window, so the vector rides on that write and costs no extra read. Firestore and SQLite store it as packed float32 bytes, not an array (Firestore would index 384 array entries per article). Old rows load with no embedding.

**3. The profile is built from reads, keyed by user id.** Phase 3 gave 78 opens, 69 reads and 5 likes in nine days, so reads carry the learning: a read weighs up to 1 (full at 120 seconds), an open 0.3, a like 2, and a dismiss goes to a negative vector. Events decay with a 45-day half-life over 180 days. Positive stories become up to four centroids (weighted k-means), so separate interests don't blur into one average. The rank stage rebuilds the profile when there is new feedback, at most every six hours, and stores it as `Profile(user_id="owner")` (#323). It never reaches a log line. A profile is recreated from feedback, so it needs no backup.

**4. Similarity is stored beside the rule score, not in it.** Each embedded article gets `similarity` in its breakdown: 1 below the median closeness to the profile, rising to 1 + 2 (that is, x3) for the closest. `Article.score` stays the rule score, so `feed.json`, Longer and the rules side of the A/B check are unchanged, and the API applies `similarity` only for the personal order. Clustering also runs in rank (#324): articles whose embeddings are very close (cosine 0.88 or more) within 48 hours form one story, `cluster_id` is stored, and a story covered by several sources gets a small `coverage` factor in its rule score (x1.17 for two sources, x1.35 for four), which addresses the deferred "importance" item. Publish shows one item per cluster with a `coverage` count (#325).

**5. Ordering, preferences and the A/B mix happen in the API, when a view is served.** ADR-0013 never recomputes stored scores, so anything baked in at rank time would take up to 72 hours to reach older items. The API already reads each view file per request; it now re-orders `score` views from the fields each item carries (#326): rules order is `score`, personal order is `score x similarity`, and the same source-diversity rule (each further item from one source x0.85) applies to both. Reader preferences plug into the same step (#337): Hide drops the item in every view, and Fewer and More multiply it in `score` views, on both A/B sides. A change shows on the next refresh and the pipeline stays shared, which is also what makes per-user views cheap later. Every factor appears in the served item's breakdown.

**6. The owner's data gets a daily backup.** Feedback has had no backup (point-in-time recovery is off), and it now also holds the A/B record. The job writes `backups/<date>/feedback.jsonl` to the existing private bucket once a day, outside both lifecycle rules, with a restore command (#330). Preferences join it (#334). This uses only permissions the job already has.

**Views.** Personalisation applies to Feed only (`ranking: ab` in `config/views.yaml`). News stays newest first; Longer stays in rules order. Clusters collapse in every view.

**Not built: the Google Takeout bootstrap.** Weeks of reads already exist, so a cold start isn't the problem it was when the plan was written, and an export of search and YouTube history is the most sensitive data the project could hold. Deferred (see `DEFERRED_WORK.md`).

## A/B check

**Method: team-draft interleaving in the Feed** (ADR-0015). Each Feed list is built by letting the rules order and the personal order take turns to pick their best remaining item, with a coin per round seeded by the run, so every page of one run is consistent. Each item carries the `arm` that picked it, the phone sends it back with `open`, `read` and `like` (#327), and each open or read is credited to that arm. Both arms see the same candidates, preferences, diversity and coverage, so the comparison measures the learned part alone.

**Why not A/B by day.** Opens ranged from about 4 to 33 events a day in Phase 3. Day-to-day swings that large would hide any realistic lift for months. Interleaving compares the two orders inside the same session, so the owner's mood, the news and the time spent cancel out.

**Sizing.** About 10 credited Feed opens a day is assumed (some opens come from News and Longer; to be measured once #327 is live). A two-sided test at 5% with 80% power needs:

| Personal side's share of credited opens | Credited opens needed | At 10 a day |
|---|---|---|
| 65% | about 85 | 9 days |
| 60% | about 195 | 20 days |
| 55% | about 780 | 11 weeks |

**Stopping rule.** Run at least 21 days and at least 200 credited opens. Then: "personalised ahead" if the 95% interval of the personal share is wholly above 50%, "rules ahead" if wholly below, otherwise "no clear difference". A per-day tally checks that one heavy day isn't driving it. Reads are reported the same way (a credited read is 30 seconds or more). `newsfeed inspect ab` prints all of it (#329). If the lift is smaller than about 60/40, the honest result at the milestone is "no clear difference yet", and that is an acceptable outcome under "Cost of failure" (poor ranking is cheap).

**When it starts.** The record fills once #320, #321, #322, #323, #326 and #327 are merged and deployed and the profile has at least 10 positive stories (already true from Phase 3 feedback, once #323's rebuild has embedded those articles). So the A/B clock starts about two batches into the phase, and the milestone is at least three weeks after that.

## Epic: reader preferences (#331)

The owner's decisions (2026-11-15/16, #314) are settled: four states per source and topic (Hide · Fewer · Show · More, icons ⊘ − ✓ +); a small starter set with "add more", grouped; first-run setup; shared by all three tabs; Hide everywhere, Fewer and More in ranked views; no new sources and no change to ingest; single user, keyed by user id; a prototype first.

**The layout (owner's pick, #333, 2026-11-17).** Option a, one page with inline controls, with the sources split into sections: "News sources" first, then the other sources, "Long Reads" as the last source section, then Topics. No Regions section for now; regions may come back later, perhaps for news sources only (`DEFERRED_WORK.md`).

**Source sections come from the tags in `config/sources.yaml`**, through a `source_sections` list in `config/preferences.yaml` (#335), so a new source lands in a section with no code change:

| Section, in screen order | A source is in it when | Today |
|---|---|---|
| News sources | tagged `news` | bbc-news, guardian-world, independent-uk, inews, al-jazeera-english, dw-news, business-insider |
| Tech | tagged `tech`, and not `news` or `long-form` | ars-technica, the-verge, wired, lwn, 404media, mit-tech-review, simon-willison, julia-evans, martin-fowler, cloudflare-blog |
| Science | tagged `science`, and none of the above | nature-news, phys-org, carbon-brief |
| More sources | in no other section | noahpinion |
| Long Reads | tagged `long-form`, and not `news` | quanta, bbc-future, guardian-longread, aeon, new-yorker |

Each enabled source is in exactly one section. Sections claim sources in the order `news`, `long-form`, `tech`, `science`, then the catch-all, which is not the screen order: a source tagged both `news` and `long-form` (none today) is under News sources, and one tagged `science` and `long-form` (quanta, bbc-future) is under Long Reads, matching the News and Longer tabs, which select by the same tags (`config/views.yaml`). An empty section isn't shown. Within each section the starter sources show first and "Show n more" opens the rest of that section; a section with no starter source shows only its heading and that button.

**Mechanism.**
- **Defaults in config, choices in the store.** `config/preferences.yaml` holds the multipliers (0, 0.5, 1, 2, with the product clamped to 0.25 to 4), the starter set (8 sources, 6 topics), the source sections, a display label per source, and preference topics, each covering one or more `interests.yaml` topics (Tech covers ai, software, linux, security-privacy, internet-infrastructure) (#335). The owner's choices are stored as overrides only, in `ReaderPreferences(user_id="owner")` (#334, ADR-0016), backed up daily.
- **No regions in this version.** `ReaderPreferences` has no regions map, `config/sources.yaml` gains no region tags, and there is no `pref:region:*` key. Adding regions later is additive: a new map with an empty default, so no stored data needs migrating.
- **More lifts the rules' demotion of that topic.** Sport has weight -2 in `interests.yaml`, so a sport headline is multiplied by about 0.14; x2 alone would leave it below unmatched stories, and "Set Sport to More" would look broken (#314). So when a topic is set to More and an item is about it, the API also divides out the item's `demote:<t>` factor for each covered topic, recorded as `pref:undemote:<t>` (outside the 0.25 to 4 clamp, which limits only the owner's multipliers). Fewer and Hide leave the demotion as it is. A body-only demotion stays, because the item isn't "about" the topic.
- **Topics** come from the rank stage's keyword matching: it marks `about:<topic>` when a topic matches the title or summary (body-only matches are often sidebars), for every topic including new weight-0 ones such as politics, so a topic can be hidden without changing anyone's score (#336). Optionally, once embeddings exist, an article close to a topic's description is marked too, and a topic set to More seeds a thin profile (#340).
- **How it combines.** served score = stored rule score x preference factors (x similarity on the personal side) x diversity. Any Hide wins over everything. Each factor shows in the breakdown as `pref:source:<id>`, `pref:topic:<id>`, `pref:undemote:<t>` or `pref:clamp`, so "Why this story?" (#328) explains it.
- **"As before" means against the same view file.** `coverage` and recency are part of the stored score and change from run to run, so tests and the demo compare a view served with preferences to the same view file served with none, not to an earlier run.
- **"Mute source" becomes Hide** (#339): the card's menu sets the preference, so a mute is visible and reversible in Settings, and the sources already muted on the phone move into the store once.
- **Fewer and More apply to Feed and Longer**, the two views ordered by score; News stays newest first. The screen says so ("Hide works on every tab. Fewer and More change the order in Feed and Longer; News stays newest first."), so nothing looks broken.

**Prototype first.** #332 builds the screen on mock data at a hidden route, `/lab/preferences`, in three layouts (inline controls; tabs with a bottom sheet; regions holding their sources) plus the first-run flow, storing nothing. Agents merge it so it reaches the phone on the next deploy; the owner opens it in Chrome at the app's address plus `/lab/preferences`. #333 (`needs-human`) was the owner's pick, made on 2026-11-17 (above); #334 to #340 were updated to it in #369.

**Epic demo.** Settings, then "Sources and topics": the screen shows News sources, Tech, Science, More sources and Long Reads, then Topics. Set BBC News to Hide, and it is gone from News, Feed and Longer on the next refresh, with no pipeline run. Set Sport to More: sport stories rise in the Feed above unmatched stories, and a sport story's "Why this story?" shows "Your setting for sport x2.00" and "Sport no longer demoted". News keeps its order. Set everything back to Show, and the tabs are as before.

The epic is off the critical path: no core issue depends on it. Its API work (#337) follows #326 because both change the view-serving code.

## Demo

At the milestone, on the phone:

1. **The same day's feed with and without personalisation.** Settings, "Compare ranking": open "Feed, rules only", then "Feed, personalised only" (#328). Your reading interests should visibly rise in the second.
2. **One article's score breakdown.** On any Feed card, open the menu, then "Why this story?": topics matched, freshness, coverage ("Covered by 3 sources"), "Like what you read x2.1", spread across sources, your settings, and which A/B side placed it.
3. **One story, once.** A big story appears once, with "3 sources" in its meta line.
4. **The A/B results so far**, on the laptop:
   ```bash
   make inspect-gcp ARGS="ab --since 30d"
   ```
   Credited opens and reads per side, the personal share with its interval, days won, and the verdict against the stopping rule.
5. **The preferences epic demo** above.

## Issue graph

Core personalisation:

```
#320 contract (models, ports, schema, ADR-0014/0015)
 ├── #321 local embeddings in rank ── #322 bake model into image (Tier C)
 │     └── #323 profile + similarity ── #324 clustering + coverage ── #325 publish one per cluster
 ├── #326 API: interleaving, diversity, ?ranking ── #327 client sends view + arm
 │                                                    └── #328 "Why this story?", coverage note, compare links (needs-human; also after #307)
 ├── #329 newsfeed inspect ab
 └── #330 daily backup + restore
```

Reader preferences epic (#331):

```
#332 prototype (hidden route) ── #333 owner picks (needs-human)
                                   ├── #334 contract: ReaderPreferences, backup, ADR-0016 (after #320, #330)
                                   │     └── #335 config, source sections, item_effect
                                   │           ├── #336 about:<topic> markers, weight-0 topics (after #324)
                                   │           └── #337 preferences API, applied when served (after #326)
                                   │                 ├── #338 preferences screen, first-run (needs-human; after #328)
                                   │                 │     └── #339 mute source = Hide
                                   └── #340 optional: topics by embedding, More seeds profile (after #323, #324, #336)
```

| Issue | Depends on | Risk | Tier (expected) |
|---|---|---|---|
| #320 contract | none | high | B |
| #321 embeddings | #320 | | B (dependency) |
| #322 image | #321 | high | C (Dockerfile) |
| #323 profile, similarity | #320, #321 | high | A |
| #324 clustering | #321, #323 | | A |
| #325 publish clusters | #320, #324 | | A |
| #326 API ranking, A/B | #320 | high | A or B (`config/views.yaml`) |
| #327 client view + arm | #326 | | A |
| #328 breakdown UI | #326, #327, #307 | | C (needs-human) |
| #329 A/B report | #320 | | A |
| #330 backup | #320 | high | A |
| #332 prototype | none | | A |
| #333 pick | #332 | | owner |
| #334 preferences contract | #333, #320, #330 | high | B |
| #335 preferences config | #333, #334 | | A (new `config/preferences.yaml` only) |
| #336 topic markers | #333, #335, #324 | | B (`interests.yaml`) |
| #337 preferences API | #334, #335, #326 | high | A |
| #338 preferences screen | #333, #337, #328 | | C (needs-human) |
| #339 mute = Hide | #337, #338 | | A |
| #340 optional topics by embedding | #323, #324, #335, #336 | | A |

Links that exist only to avoid editing the same files at once: #324 after #323 (`rank/stage.py`), #336 after #324 (`rank/scoring.py`), #337 after #326 (`api/app.py`), #328 after #307 and #338 after #328 (`FeedScreen.tsx`, `SettingsScreen.tsx`), #334 after #320 (contracts are serialised).

**First batch:** #320 (contract), #332 (prototype), and #307 (carried, already Ready; its PR #319 is open). They touch different files. Then #321, #326, #329 and #330 in parallel once #320 merges.

**Carried in.** #307 (feed screens; PR #319). #89 (live extraction check, `needs-human`) and #92 (page headline as title, not `agent-ready`) stay in the milestone untouched; neither blocks this phase.

## Contract changes and ADRs

- **#320** (Tier B): `Embedder` port and `EmbeddingError`; `Article.embedding`, `embedding_model`; `Profile` with `InterestCentroid`, keyed by `user_id`; `Repository.get_profile`/`save_profile`; `Feedback.user_id`, `view`, `arm`; `RankingArm`; `DEFAULT_USER_ID = "owner"`. `docs/feed.schema.json` gains optional `coverage` and `arm` (additive, `schema_version` stays 1). ADR-0014 (personal ranking) and ADR-0015 (A/B by interleaving). No stored-data migration: every new field has a default.
- **#334** (Tier B): `PreferenceState`, `ReaderPreferences`; `Repository.get_preferences`/`save_preferences`. ADR-0016 (reader preferences). `PROJECT_PLAN.md` §6 gains the epic.
- New breakdown keys, documented in `rank/scoring.py`: `similarity` (factor, not in `score`), `similarity_raw`, `coverage` (in `score`), `coverage_sources`, `about:<topic>` (markers); served only: `diversity`, `pref:*`.
- New config: `config/personalisation.yaml` (#323, #324), `config/preferences.yaml` (#335); `ranking` in `config/views.yaml` (#326).

## Check against later phases

- **Phase 5 (Android widget, push).** The widget reads the same API, so it gets preferences and the A/B mix for free; its opens can carry `arm` the same way. A Trusted Web Activity behind IAP is unchanged by this phase.
- **Phase 6 (LLM summaries and tags, "why am I seeing this", trending).** "Why this story?" is the first version of "why am I seeing this". LLM tags can add `about:<topic>` markers without touching the API. Clusters and coverage are the base for a trending layer.
- **More users.** Profiles, preferences and feedback all carry `user_id`, and the API maps the verified identity to it in one function (`user_id_for`). Adding a user means adding a key and IAP access, which is stop-the-line, not a migration.
- **Cost growth.** Embeddings have no per-call cost; the per-run reads added are about one profile read and, at most every six hours, a feedback read. The view-serving work is CPU in the existing API instance.

## What the owner and agents need first

- **Owner:** nothing before work starts. Later: merge the Tier C PRs (#322, #328, #338) and keep using the Feed so the A/B record fills.
- **Agents:** no credentials, cloud setup or paid service. #322's build downloads the model from the model hub in CI; if an agent's local sandbox blocks it, CI's `docker` job is the check. After #322 merges, if it raised `job_memory`, the lead runs `terraform apply` (ADR-0007). No manual backfill is needed in the cloud: each profile rebuild embeds up to 200 feedback articles that lack an embedding (#323), so the owner's Phase 3 history fills in over the first few runs.

## First point the owner can use something

The prototype (#332): in the first batch, on the phone at the app's address plus `/lab/preferences`. Next, after the second batch: the "Why this story?" breakdown and the compare links (#328), which show the personal order working before the A/B result is in.

## Cost of failure

No change from `CLAUDE.md` (confirmed for Phase 4 by PR #308). Where it bites in this phase:

- **Matters:** the owner's feedback, A/B record and preferences (#330, #334 back them up; #320, #334 and #337 are high risk); the profile and anything derived from reads in logs (#321, #323 test that logs hold no vectors, titles or ids); a bill (#322 measures memory and image size).
- **Cheap:** a wrong cluster, a weak profile, an A/B result of "no clear difference", topic markers missing on older items.

## Decisions to confirm later

| Decision | Reason | To undo |
|---|---|---|
| Local embeddings (`fastembed`, `BAAI/bge-small-en-v1.5`) rather than Vertex AI | No bill per call, no third party sees reading history, no new paid service | Another `Embedder` adapter; re-embed |
| Embeddings stored on `Article`, as float32 bytes in Firestore | Rides the rank stage's existing write; avoids 384 index entries per article | A separate collection keyed by article id |
| Profile signal weights (read 1 at 120 s, open 0.3, like 2, dismiss -1), 45-day half-life, up to 4 centroids, rebuilt at most every 6 h | Reads are the owner's real signal (Phase 3 numbers) | Edit `config/personalisation.yaml` |
| `similarity` stored as a factor outside `score`, applied in the API | Keeps rules-only intact for A/B, `feed.json` and Longer | Fold it into `score` in rank |
| Similarity by percentile, never below x1, top x3 | Robust to the model's scale; never buries what rules like | Edit `beta` or the formula |
| Clustering at cosine 0.88 within 48 h in rank; `coverage` factor 1 + 0.25 ln(sources) in the rule score; one item per cluster in every view, News included | Cheap, deterministic, fair to both A/B sides | Edit `config/personalisation.yaml`; drop the publish grouping |
| A/B by team-draft interleaving in Feed; stopping rule 21 days and 200 credited opens | The only design that can answer within the phase at 10 to 30 opens a day | ADR-0015; day-level A/B |
| Diversity (x0.85 per further item from one source) applied to both A/B sides | Measures the learned part alone | Apply it to the personal side only |
| Personalisation in Feed only; Longer in rules order | The Phase 3 report's default | `ranking` in `config/views.yaml` |
| Daily feedback backup to the existing bucket, not Firestore's own backups | No new permission or cloud setting; restore command included | Enable Firestore scheduled backups (one Terraform resource) |
| Google Takeout bootstrap deferred | Reads already exist; the export is the most sensitive data the project could hold | Build it as planned in §6 |
| `DEFAULT_USER_ID = "owner"`; the email never used as a key | Email is private; one function maps identity to user id | Change `user_id_for` |
| Prototype merged by agents behind a hidden route, not `needs-human` | No screen the owner uses changes; it reaches the phone sooner; the review is #333 | Remove `client/src/lab/` |
| No region preferences for now (owner, #333); regions left out of the model, config and API, not hidden | The owner asked to remove the Regions section; leaving them out keeps the contract small, and adding a map later is additive | `DEFERRED_WORK.md` "Regions in reader preferences" |
| Source sections from `sources.yaml` tags: News sources (`news`), Tech (`tech`), Science (`science`), More sources (the rest), Long Reads (`long-form`), claimed in the order news, long-form, tech, science | The owner's pick names News and Long Reads; Tech and Science split the rest the way the prototype's kinds did; claiming order keeps each section matching its tab | Edit `source_sections` in `config/preferences.yaml` |
| A source tagged both `news` and `long-form` sits under News sources only (none today) | One control per source; News is the section the owner asked to be clear | Swap the claiming order |
| More on a topic divides out the rules' `demote:<t>` for covered topics (`pref:undemote:<t>`, outside the clamp); Fewer and Hide don't | x2 can't lift a x0.14 demotion, so Sport set to More looked broken (#314) | Drop the undemote step in `item_effect` |
| Restoring a backup keeps the stored preferences when they are newer (`updated_at`) than the backed-up copy, with a log line | A restore after data loss brings them back; a restore over live data never loses newer choices | Use `newsfeed restore-backup --force` (shipped in #373) to replace them with the backup's copy |
| Topics from `about:` markers on title or summary matches; new weight-0 topics (politics, business, health, culture) | Hide works without changing scores; body-only matches are often sidebars | Edit `config/interests.yaml` |
| Fewer and More apply to Feed and Longer (both ordered by score), not Feed alone | Same rule for every ranked view; the screen says so | Apply them only in `ranking: ab` views |
| Preference multipliers 0 / 0.5 / 1 / 2, product clamped to 0.25 to 4; starter set of 8 sources and 6 topics | The owner's starting values | Edit `config/preferences.yaml` |
| "Mute source" sets the source to Hide; local mutes migrated once | One place for source control, visible and reversible | Restore the client-only mute |
| The selectable sources are the enabled ones in `config/sources.yaml` (26 today; the two disabled examples are left out) | Disabled sources produce no articles | Include disabled sources |
