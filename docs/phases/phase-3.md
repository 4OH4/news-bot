# Phase 3 design: reader client

**Goal:** an installable PWA on my Android phone with an offline cache, and feedback capture through a thin API that only I can use. **Exit:** I use it daily in place of the Google feed for two weeks (`PROJECT_PLAN.md` §6).

Phase 2 is signed off (PR #163), so building starts once this design merges.

## Approach

```
Phone (PWA: Preact, service worker, IndexedDB)
   │ same origin, https://<project>.web.app
   ▼
Firebase Hosting ── static client files
   │ /api/** rewrite, with Authorization: Bearer <Firebase ID token>
   ▼
Cloud Run Service newsfeed-api (FastAPI, scale to zero, same image as the job)
   │ checks the token and the owner's email on every route
   ├── BlobStore: reads feed.json, articles/<id>.html, saved/<id>.html; writes saved/
   └── Repository: writes feedback only, never articles
```

The three items the Phase 2 report left for this design are settled here. ADR work is in #171.

**1. Route to the private bucket: through the API.** The bucket stays private. The API reads `feed.json` and article HTML through the `BlobStore` port and returns them only to the signed-in owner, with an `ETag` and `Cache-Control: private`, so Firebase Hosting's CDN never holds article text. `GET /api/feed` needs no Firestore read. The client shows its IndexedDB copy first and refreshes behind it, so a Cloud Run cold start never delays opening the app (NFR-4). Rejected: a public bucket, which publishes third-party text; signed URLs, which need a key or signing permission and a Firestore-free way to hand out 200 links; serving the client from the API, which makes the first load wait for a cold start.

**2. Identity: Firebase Auth, with the owner check in our API.** Cloud Run allows `allUsers` to reach the service, as Hosting rewrites require, and every route needs a valid token whose verified email equals `NEWSFEED_OWNER_EMAIL`. Anyone can get a Firebase token for their own Google account, so the email check is the real gate, and it fails closed. An adapter delegates token checks to Google's `firebase_admin` library, which uses Google's public keys, so this design has no secret to store and writes no HTTP of its own. Rejected: IAP, because its login redirects and short sessions break background refresh in a service worker, and without a Workspace organisation it needs a hand-made OAuth client.

**The client loads no third-party script.** Firebase's popup and redirect helpers load a script from `apis.google.com`, which `CLAUDE.md` forbids. So the client sends the browser to Google's sign-in page itself, receives an ID token on `/signin`, and passes it to `signInWithCredential`. That is plain HTTP calls from bundled code, and it avoids the third-party storage problems of Firebase's redirect flow. The CSP stays at `script-src 'self'`.

**3. Feedback identity: keep `Feedback.article_id`, and add `story_id` to the feed.**

- Feedback events store the id of the row the owner saw. They are never rewritten.
- `story_id` is a new additive feed field: the id of the row at the end of the `declared_canonical` chain, which publish already works out in `_story_end`. It stays the same when a target recovers, so the client keys seen, liked and dismissed state on it (#184). A seen story doesn't return as new when its target recovers. That doesn't cover a re-key on retry: a `failed` row shown as a link, whose page later declares a canonical with no stored row, gets a new `id` and so a new `story_id`, and a dismissed story comes back. That's cheap, so the design accepts it.
- Nothing is lost when an id changes. Phase 4 joins an event to its story like this: a row that still exists follows `declared_canonical`, and that covers the recovery case in PR #148, where both rows stay stored. A row re-keyed on retry is found through its first-seen URL, because `with_canonical_url` keeps `url`, so `article_id(canonical(row.url))` is the old id. Only a row merged into another on retry can't be joined, which loses one signal in a rare case. That is cheap, so `Feedback` doesn't change.

**The API writes no article rows.** ADR-0005 decision 6 says a like points `content_ref` at the saved copy. But `upsert_article` replaces the whole row and the rank stage rewrites every row in its window from a snapshot, so a like during a run would be overwritten silently (`REVIEW.md`: a batch article is a snapshot). Instead the API copies the blob to `saved/<id>.html` and reads that key first. `content_ref` stays as it is, and #171 records the amendment. The API's only `Repository` write is `save_feedback`, which is keyed by a client-made id, so a retry is harmless.

**Framework: Preact, TypeScript, Vite and `vite-plugin-pwa`.** It's a small bundle for a phone, it builds to static files for Hosting, and Testing Library and Playwright both work with it. Next.js and SvelteKit add server features this client doesn't use. React costs about ten times the bundle for nothing used here. Plain TypeScript would leave state and rendering to hand-written code.

**Offline.** The service worker precaches only the app shell. Data lives in IndexedDB and the app reads it first, because API calls carry a token and need app logic around them: the last good feed, the text of the top 50 article items, anything opened or liked, the feedback queue and per-story state. Images stay online-only, so an offline article shows its text. Only a sign-out the user chooses clears all this. A forced one on a 401 or 403 ends the session and keeps the data, because a misconfigured owner email or a key-fetch failure mustn't wipe queued feedback or state that no route can restore (#180, #183, #184). The data stays readable to someone who signs in on the same phone until the API refuses them, which is accepted because it's the owner's phone.

**Rendering third-party HTML.** The blob is sanitised at extraction, the client runs DOMPurify over it with the same tag list, and the CSP blocks inline script. A token sits in the same origin, so a sanitiser gap is a failure that matters. `no-referrer` applies to all images and links.

**Dismiss and mute act in the client in this phase.** Events are stored for Phase 4, and the client hides the story or source. A lasting pipeline mute stays a rule in `config/interests.yaml` (architecture rule 5), and `newsfeed report` lists the sources muted from the phone so they can be moved there. Pipeline use of feedback is Phase 4.

**A like, dismiss or mute can be taken back.** `FeedbackEvent` has no `unlike`, so `like`, `dismiss` and `mute_source` carry `value` 1 or 0 and the latest event wins, with no contract change. Without this, Phase 4 would learn from dismissals taken back, and feedback is the one store that can't be recreated (#184, #185). The saved copy of an unliked article stays.

**Client tooling runs in Docker through `make`.** This machine has no Node, and `.claude/settings.json` allows neither `npm` nor `npx`. `make` and Docker are allowed and available, so `make client-*` runs pinned Node and Playwright images. Nothing has to be installed or allow-listed, and CI runs the same targets. These targets mount only `client/` and `docs/`, so a check that needs backend code is a pytest test instead (#182's tag list, #184's payload).

**Merge gate.** #174 leaves `TEST_PATHS` out of source lines in `tier.py`, because `SRC_PATHS` includes `client/*`, so test-first client PRs would otherwise hit the 400-line limit. It also adds `client/firebase.json` to `TIER_C_PATHS`, because that file holds the CSP and the `/api/**` rewrite.

**Deploy.** The region isn't in the repo, so `client/firebase.json` commits the placeholder `__GCP_REGION__` (#174), and #178 fills it in from `vars.GCP_REGION`. #178 installs `firebase-tools` from a lockfile in `tools/firebase/` with `--ignore-scripts`, not `npx`, and builds before the auth step. The build mounts `client/` writable, so #178 then restores `client/firebase.json` from the checked-out commit and fails if it names `predeploy` or `postdeploy`, because Firebase runs those hooks as shell commands with the deploy credentials. Between them, these keep third-party code from running with the deploy identity.

## The demo

The milestone report will carry exact URLs. On the Android phone:

1. Open `https://<project>.web.app` in Chrome, sign in with Google, and choose "Install app".
2. Open the app from the home screen and read today's ranked feed.
3. Open an article and check the reader in light and dark mode. Like it, and dismiss another story.
4. Turn on aeroplane mode, close and reopen the app. The feed shows, and the liked article opens with its text.
5. Turn aeroplane mode off and pull to refresh. On the laptop, with the `NEWSFEED_STORAGE=gcp` exports from step 3 of the Phase 2 report, because with no settings it reads local SQLite, `uv run --project backend newsfeed report` shows the like and the dismiss.
6. Try `https://<project>.web.app` signed in with any other Google account: no feed, and the API answers 403.

## Issue graph

There are 15 issues in one line of work, with one demo, so epics would add nothing.

```
#171 ADRs (B)
#172 contract (B) ─────► #175 read API ──► #176 feedback API ──► #184 ◄─┐
#173 story_id (B) ──► #181                                             │
#174 scaffold (C) ─┬───► #180 sign-in ──► #181 feed list ──► #182 reader ──► #183 offline
                   └───► #178 deploy (C)
#177 Terraform (C) ────► #178
#175, #178, #180, #181 ──► #179 human setup
#176 ──► #185 report
```

| Issue | Depends on | Tier | Risk |
|---|---|---|---|
| #171 ADRs | - | B | |
| #172 `BlobStore.ref_for`, `TokenVerifier` | - | B | |
| #173 `story_id` | - | B | |
| #174 scaffold, `make client-*`, CI | - | C | |
| #175 read API, `newsfeed serve` | #172 | B | High |
| #176 feedback API, saved copies | #175 | A | High |
| #177 Terraform | - | C | High |
| #178 deploy | #174, #177 | C | High |
| #179 human setup | #175, #177, #178, #180, #181 | `needs-human` | |
| #180 sign-in, API client | #174 | B | High |
| #181 feed list | #173, #180 | A | |
| #182 reader | #181 | B | High |
| #183 offline | #182 | B | |
| #184 client feedback | #176, #183 | A | |
| #185 report | #176 | A | |

Suggested first batch: #171, #172, #173 and #174, which touch no common files. #177 can run beside them. The two API issues and the client issues each form a line that touches the same directory, so they run one at a time.

## Contract changes and ADRs

- `ports.py`: `BlobStore.ref_for(key)`, because the API has to read keys it didn't write and the fake's refs differ from its keys on purpose. New `TokenVerifier` port and `TokenRejectedError`, so core API code stays free of Google imports (rule 1).
- `models.py`: new `VerifiedIdentity`. `Feedback` doesn't change.
- `docs/feed.schema.json`: additive `story_id`, `schema_version` stays 1 (ADR-0006).
- ADR-0008: reader client stack. ADR-0009: client access and feedback identity, which also amends ADR-0005 decision 6.

## Check against later phases

- **Phase 4:** `list_feedback` gives raw events to build centroids from, and the join above resolves ids. Its open-rate comparison needs a denominator: the per-day counts in #185, plus additive feed fields if a slot test is chosen. The client doesn't compute scores, so hybrid scores need no client change, and `score_breakdown` is already in the feed.
- **Phase 5:** a Trusted Web Activity wraps this PWA as it stands. A Compose app or widget uses the same API and Firebase token. Neither needs a change here.
- **Phase 6:** a "why am I seeing this" view reads `score_breakdown`. Summaries and tags are additive feed fields.
- **Retention:** `saved/` sits outside both lifecycle rules, as ADR-0005 intended. No retention rule changes and nothing is deleted.
- **A production decision** is not needed: the only user is the developer.

## What the human must do, and what agents need first

- **Merge the Tier C PRs:** #174, #177 and #178.
- **#179**, after #175, #177, #178, #180 and #181 merge: one `terraform apply`, a few clicks in the Firebase and Google Cloud consoles, and three repository variables. The issue gives exact steps. Agents can't do these, because the development service account doesn't exist yet (ADR-0007), and IAM and consent screens are the human's in any case.
- **Carried from Phase 2, alongside the build:** #89, the live extraction check, which also covers run length, and steps 1 and 5 of the Phase 2 report, the feed and cost check. Both need the owner's account. A thin feed is the first thing daily use would show, so they shouldn't wait for the milestone.
- **Agents need:** nothing new once #174 merges. The lead should confirm the agents can reach Docker from a worktree in the first batch.
- **Brief gap, for the lead:** `test-author.md` and `worker.md` assume `backend/tests/`, `make lint` and `make test`. Client issues name their paths and `make client-*` commands, which should be enough. If it isn't, that's a small Tier C fix.

## First point the human can use something

After #175, #177, #178, #179, #180 and #181: the feed list on the phone, signed in, installable, with live data. #182 follows directly, with articles readable. Feedback on the first use goes in the Phase Log, and the lead makes issues from it. #179 waits for #180 and #181, because its last step and its definition of done are signing in on the phone and seeing the feed. It needs nothing after #181.

## Where this phase differs from "Cost of failure"

The statement in `CLAUDE.md`, confirmed for Phase 3 by merging PR #163, covers this phase. These are the failures above the bar in it, and each carries `**Risk:** high`:

- anyone but the owner reading content or writing feedback (#175, #176, #177, #180);
- a bill from a flood of requests at the public service (#177), which the instance cap limits but doesn't stop, as the follow-up below explains;
- losing a feedback event once it's accepted, or leaving article text in a CDN cache (#175, #176);
- script running from article HTML in the same origin as the token (#182);
- a privileged deploy that widens what it can change (#178).

Poor ranking, a duplicate item, a stale feed and a failed refresh are still cheap. So is a queued feedback event lost by clearing the phone's storage.

## Decisions to confirm later

Each is reversible. The owning issue repeats it in its PR header.

| Decision | To undo |
|---|---|
| The API sits in front of the private bucket, same origin through a Hosting rewrite | Serve through signed URLs |
| Firebase Auth, with the owner check in the API, and `allUsers` on the Cloud Run service | Move to IAP or a proxy; only `TokenVerifier` and the client's session module change |
| Hand-made Google redirect with `signInWithCredential`, so no third-party script | Use Firebase's redirect helper and relax the rule and the CSP |
| Preact, TypeScript, Vite and `vite-plugin-pwa` | Replace `client/`; the API and feed format are unaffected |
| `story_id` in the feed, `Feedback` unchanged, merged rows accepted as unjoinable | Add `url` to `Feedback`, which helps only events recorded after that |
| Saved copies at `saved/<id>.html` with `content_ref` left alone, amending ADR-0005 decision 6 | Add a repository method that updates one field |
| `like`, `dismiss` and `mute_source` with `value` 0 retract | Add explicit undo events |
| Dismiss and mute act in the client only in this phase | Phase 4 reads them in the pipeline |
| Top 50 article items prefetched, images online-only | Change the constant, or cache images |
| DOMPurify as a second sanitiser pass in the client | Remove it and rely on extraction and the CSP |
| Client tooling runs in Docker through `make` | Install Node and allow-list `npm` |
| `FirebaseTokenVerifier` lives in `adapters/` and delegates to `firebase_admin.auth.verify_id_token`, so the project writes no HTTP of its own (rules 1 and 6). The library's key fetch is a cloud SDK's internal traffic, not project HTTP | Delegate to `google-auth` inside the same adapter |
| API limits: 0 to 1 instance, 512 MiB, 1 vCPU | Change the Terraform variables |
| Only a sign-out the user chooses clears local data, and a forced one on 401 or 403 keeps it | Call the `onSignOut` hooks from `endSession` too |
| `firebase-tools` locked in `tools/firebase/`, installed with `--ignore-scripts` | Use `npx` with an exact version |

**Unverified, because this design made no outbound requests.** These come from memory of Google's documentation, and the first run of #179 tests them. If one fails, the lead calls the architect back.

- Hosting rewrites reach Cloud Run in the project's region. Fallback: call the `run.app` URL directly with one allowed CORS origin.
- The `google-beta` Terraform resources for Firebase are as named in #177.
- Firebase resources under user credentials need `billing_project` and `user_project_override` on the `google-beta` provider, or the first `terraform apply` fails with a quota project error. #177 copies both from the existing `google` provider, so this should hold.
- An installed Android PWA returns from Google's sign-in page. Fallback: sign in in Chrome first, since the installed app shares its storage.
- Firestore rules are not open on a database that predates Firebase. #177 pins them to deny-all, so this holds either way.
- `signInWithCredential` accepts the Google ID token with its `nonce`. It may instead answer `auth/missing-or-invalid-nonce`. Fallback: send Google the SHA-256 hex of the nonce and pass the raw nonce through `OAuthProvider('google.com').credential({ idToken, rawNonce })`. #180 checks this in the Firebase SDK source.
- `firebase deploy --only hosting` works with install scripts off. Fallback: `npm rebuild <package>` for the one package that needs it.

## Follow-ups and a decision for the lead

- **For the lead to decide:** `client` in `CONDITIONAL_CHECKS` passes the gate when absent, so a renamed job drops out silently. `REQUIRED_CHECKS` is stricter, but must match the `Protect main` ruleset, which the human changes. #174 uses `CONDITIONAL_CHECKS` until the lead chooses.
- #175: `newsfeed serve` goes through `cli.py`, which still loads `trafilatura` and `newspaper` (0.4 s, 67 MiB). Lazy-import them if cold start matters.
- #177: the instance cap is 1, not 2, because the service is public and a bill matters. A flood lasting a whole month then costs about $65 in CPU and memory instead of $130, plus $0.40 per million requests that reach the app (estimates from memory). That is still above the $10 budget, in about five days. Nothing free stops it, because a budget only sends email, so the limit is a person acting on the alerts at 50, 90 and 100%: `gcloud run services remove-iam-policy-binding newsfeed-api --region=<region> --member=allUsers --role=roles/run.invoker` stops all traffic to the app, and `terraform apply` restores it. #177 puts this in `infra/README.md`. Rejected: under 1 vCPU, because Cloud Run then allows one request at a time and prefetch sends four (from memory); Cloud Armor, a paid service; a budget-triggered shutdown, which needs a function and Pub/Sub to guard against an unlikely flood. During a flood the phone may get 429s and keeps its cached feed, which is cheap.
