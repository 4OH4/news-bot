# Phase 3 design: reader client

**Goal:** an installable PWA on my Android phone with an offline cache, and feedback capture through a thin API that only I can use. **Exit:** I use it daily in place of the Google feed for two weeks (`PROJECT_PLAN.md` §6).

Phase 2 is signed off (PR #163). This revision moves client access from Firebase to Identity-Aware Proxy (IAP), following the decision issue #196 and its two live spikes. The decision there is final once the last spike check, the Reconnect after a real 401, has passed, and this design merges only after that.

## Approach

```
Phone (installed PWA: Preact, service worker, IndexedDB)
   │ one origin: https://newsfeed-api-<hash>.<region>.run.app
   ▼
IAP (Google's sign-in gate, turned on directly on the Cloud Run service)
   │ lets only the owner's Google account through; refuses everyone else
   │ before the container, and adds a signed x-goog-iap-jwt-assertion header
   ▼
Cloud Run Service newsfeed-api (FastAPI, scale to zero, same image as the job)
   │ checks the IAP JWT and the owner's email on every route
   ├── / and static files: the built client, baked into the image
   ├── /api/**: BlobStore reads feed.json, articles/<id>.html, saved/<id>.html; writes saved/
   └── Repository: writes feedback only, never articles
```

The three items the Phase 2 report left for this design are settled here. ADR work is in #171.

**1. Route to the private bucket: through the API, on one origin with the client.** The bucket stays private. The API reads `feed.json` and article HTML through the `BlobStore` port and returns them only to the owner, with an `ETag` and `Cache-Control: private`. There is no CDN, so no copy of article text sits anywhere else. The same service serves the built client from `/`, so the IAP session cookie covers both and every API call is same-origin. The client shows its IndexedDB copy first and refreshes behind it, so a Cloud Run cold start never delays opening the installed app (NFR-4). Only the first load in Chrome, and the first after a new version, wait for one cold start. Rejected: a public bucket, which publishes third-party text; signed URLs; the client on Firebase Hosting with the API on `run.app`, which makes the IAP cookie third-party to the page (#196 §2b); a load balancer, which has a monthly charge.

**2. Identity: IAP, with the owner check repeated in our API.** IAP sits in front of the service, the owner's account alone holds `roles/iap.httpsResourceAccessor`, and the service has no `allUsers` grant. Google refuses anyone else before the container runs. Spike A in #196 sent 4,500 unauthenticated requests: none started an instance, none was counted or billed as a Cloud Run request, and none wrote a request log. As defence in depth, every route also requires a valid `x-goog-iap-jwt-assertion` (ES256, Google's IAP public keys, issuer `https://cloud.google.com/iap`, audience naming this service) whose `email` equals `NEWSFEED_OWNER_EMAIL`, failing closed. Google recommends this check, because it still holds if IAP is turned off by mistake or the grant is wrong. A new adapter, `IapJwtVerifier`, delegates the check to the `google-auth` library and implements the `TokenVerifier` port from #172, which doesn't change. The project writes no HTTP of its own, and has no secret: the OAuth client secret goes to IAP once, in the console, and is never in the repo, Terraform state or Secret Manager. Rejected: Firebase Auth on a public service (the earlier design), because a flood of strangers' requests would be billed with nothing free to stop it, and it needed Hosting, a hand-made sign-in and a privileged `firebase-tools` deploy.

**The client has no sign-in code.** The first visit in Chrome goes through Google's sign-in and back, handled by IAP. The client loads no third-party script, and the CSP stays at `script-src 'self'`.

**Reconnect, when the IAP session lapses.** A `fetch` can't follow IAP's redirect through Google, so the client sends `X-Requested-With: XMLHttpRequest` on every API call, which makes IAP answer 401 instead of a 302 (spike A confirmed both). A 401 while online shows a "Reconnect" banner over the stored feed. Tapping it does a plain navigation to `/reconnect`, an in-app path the service worker always sends to the network: with a valid session the app just reloads, and with a lapsed one IAP takes it through Google and back. A network error never offers Reconnect, so the app never navigates to a page that can't load. It never uses IAP's `DO_SESSION_REFRESH` mode, which in spike B only reached IAP's keep-alive page and needed Back. The reconnect after a real 401 is the last spike check still running, so the method lives in one module, `client/src/auth/reconnect.ts`, plus one service-worker denylist entry (#174, #180), and a different method changes only those. If it fails, the lead calls the architect back before #180 merges.

**3. Feedback identity: keep `Feedback.article_id`, and add `story_id` to the feed.** Done in #173 (merged).

- Feedback events store the id of the row the owner saw. They are never rewritten.
- `story_id` is the id of the row at the end of the `declared_canonical` chain. It stays the same when a target recovers, so the client keys seen, liked and dismissed state on it (#184). A `failed` row re-keyed on retry gets a new `story_id`, so a dismissed story can come back. That's cheap, so the design accepts it.
- Phase 4 joins an event to its story by following `declared_canonical` for a stored row, and by `article_id(canonical(row.url))` for a re-keyed one, because `with_canonical_url` keeps `url`. Only a row merged into another on retry can't be joined, which loses one signal in a rare case. So `Feedback` doesn't change.

**The API writes no article rows.** `upsert_article` replaces the whole row and the rank stage rewrites every row in its window from a snapshot, so repointing `content_ref` on a like (ADR-0005 decision 6) would be overwritten by the next run (`REVIEW.md`: a batch article is a snapshot). Instead the API copies the blob to `saved/<id>.html` and reads that key first. #171 records the amendment. The API's only `Repository` write is `save_feedback`, keyed by a client-made id, so a retry is harmless.

**Framework: Preact, TypeScript, Vite and `vite-plugin-pwa`.** A small bundle for a phone, static output to bake into the image, and Testing Library and Playwright both work with it. Next.js and SvelteKit add server features this client doesn't use, React costs about ten times the bundle, and plain TypeScript leaves state and rendering to hand-written code.

**Offline.** The service worker precaches only the app shell, and deletes old caches when a new version activates, or an update would keep serving the previous one (spike B). The manifest link has `crossorigin="use-credentials"`, because the manifest sits behind IAP. Data lives in IndexedDB and the app reads it first: the last good feed, the text of the top 50 article items, anything opened or liked, the feedback queue and per-story state. Images stay online-only. Spike B showed the installed app starting from its cache in aeroplane mode and flushing a queued item through IAP once back online. Only a sign-out the user chooses clears local data, and only once the queue is empty if the user chose to send it (#184). A 401 or 403 never clears anything, because a lapsed session or a misconfigured owner email mustn't wipe queued feedback.

**Rendering third-party HTML.** The blob is sanitised at extraction, the client runs DOMPurify over it with the same tag list, and the CSP blocks inline script. Script in this origin could make same-origin calls with the owner's IAP cookie, so a sanitiser gap is a failure that matters. `no-referrer` applies to all images and links.

**Security headers live in the API.** With no Hosting, the API sets the CSP, `Referrer-Policy: no-referrer` and `X-Content-Type-Options: nosniff` on every response (#175). `client/security-headers.json` (#174) holds the same values for the Vite preview server, so Playwright runs under the production CSP, and a pytest test in #175 keeps the two equal.

**Dismiss and mute act in the client in this phase.** Events are stored for Phase 4, and the client hides the story or source. A lasting pipeline mute stays a rule in `config/interests.yaml` (architecture rule 5), and `newsfeed report` lists the sources muted from the phone.

**A like, dismiss or mute can be taken back.** `like`, `dismiss` and `mute_source` carry `value` 1 or 0 and the latest event wins, with no contract change, so Phase 4 doesn't learn from a dismissal taken back (#184, #185). The saved copy of an unliked article stays.

**Client tooling runs in Docker through `make`.** This machine has no Node, and `.claude/settings.json` allows neither `npm` nor `npx`, so `make client-*` runs pinned Node and Playwright images. These targets mount only `client/` and `docs/`, so a check that needs backend code is a pytest test (#175's headers, #182's tag list, #184's payload).

**Build and deploy.** The client is built in a Node stage of `backend/Dockerfile` that copies only `client/`, runs `npm ci --ignore-scripts` and the Vite build, and hands `dist` to the runtime stage at `/app/client` (#178). That stage sees no credentials: it gets only the files copied into it. Deploy stays as the job's is: push the image, then `gcloud run services update --image`. Nothing uses `--source`, because Cloud Build's default account has no rights in this project (#196, spike B).

**Merge gate.** #174 leaves `TEST_PATHS` out of source lines in `tier.py`, because `SRC_PATHS` includes `client/*`, so test-first client PRs would otherwise hit the 400-line limit.

## The demo

The milestone report will carry the exact URL. On the Android phone:

1. Open the service URL in Chrome, sign in with Google, and choose "Install app".
2. Open the app from the home screen and read today's ranked feed.
3. Open an article and check the reader in light and dark mode. Like it, and dismiss another story.
4. Turn on aeroplane mode, close and reopen the app. The feed shows, and the liked article opens with its text.
5. Turn aeroplane mode off and pull to refresh. On the laptop, with the `NEWSFEED_STORAGE=gcp` exports from step 3 of the Phase 2 report, `uv run --project backend newsfeed report` shows the like and the dismiss.
6. Open the URL signed in with any other Google account: Google's "You don't have access" page, and the app never loads.

## Issue graph

There are 15 issues in one line of work, with one demo, so epics would add nothing.

```
#171 ADRs (B)
#172 contract (B) ─────► #175 read API ──► #176 feedback API ──► #184 ◄─┐
#173 story_id (done) ──► #181                                          │
#174 scaffold (C) ─┬───► #180 API client, Reconnect ──► #181 feed list ──► #182 reader ──► #183 offline
                   └───► #178 image and deploy (C)
#177 Terraform (C) ────► #178
#175, #177, #178 ──► #179 human setup
#176 ──► #185 report
```

| Issue | Depends on | Tier | Risk |
|---|---|---|---|
| #171 ADRs | - | B | |
| #172 `BlobStore.ref_for`, `TokenVerifier` | - | B | |
| #173 `story_id` | - | merged | |
| #174 scaffold, `make client-*`, CI | - | C | |
| #175 read API, static client, `IapJwtVerifier`, `newsfeed serve` | #172 | B | High |
| #176 feedback API, saved copies | #175 | A | High |
| #177 Terraform | - | C | High |
| #178 client in the image, deploy the service | #174, #177 | C | High |
| #179 human setup | #175, #177, #178 | `needs-human` | |
| #180 API client and Reconnect | #174 | B | High |
| #181 feed list | #173, #180 | A | |
| #182 reader | #181 | B | High |
| #183 offline | #182 | B | |
| #184 client feedback | #176, #183 | A | |
| #185 report | #176 | A | |

#172 is under way. Suggested next batch: #171 and #174, which touch no common files, with #177 beside them. The two API issues and the client issues each form a line in one directory, so they run one at a time.

## Contract changes and ADRs

- `ports.py`: `BlobStore.ref_for(key)`, `TokenVerifier` and `TokenRejectedError` (#172, unchanged by this revision). The IAP JWT is the token `TokenVerifier.verify` takes.
- `models.py`: new `VerifiedIdentity`. `Feedback` doesn't change.
- `docs/feed.schema.json`: additive `story_id`, `schema_version` stays 1 (#173, merged).
- ADR-0008: reader client stack. ADR-0009: client access through IAP and feedback identity, which also amends ADR-0005 decision 6.

## Check against later phases

- **Phase 4:** `list_feedback` gives raw events to build centroids from, and the join above resolves ids. Its open-rate comparison needs a denominator: the per-day counts in #185. The client doesn't compute scores, so hybrid scores need no client change.
- **Phase 5, a risk:** a Trusted Web Activity (a thin Android app that shows the PWA in Chrome) needs Digital Asset Links: the site must serve `/.well-known/assetlinks.json` to a check made without the owner's cookie, and IAP covers every path, so the check may fail and the app would show a URL bar (unverified, #196 §6). The fallbacks are living with the URL bar, which is cosmetic, or a Compose app, which reaches the API through IAP's programmatic access with the custom OAuth client this phase creates. Neither needs a change here.
- **Phase 6:** a "why am I seeing this" view reads `score_breakdown`. Summaries and tags are additive feed fields.
- **Retention:** `saved/` sits outside both lifecycle rules, as ADR-0005 intended. No retention rule changes and nothing is deleted.
- **A production decision** is not needed: the only user is the developer.

## What the human must do, and what agents need first

- **Merge the Tier C PRs:** #174, #177 and #178.
- **#179**, after #175, #177 and #178 merge, about 15 minutes, once: `owner_email` in `terraform.tfvars` and one `terraform apply`; then, in the console, the IAP settings for `newsfeed-api` (**Edit policy > Configure in IAP > Custom OAuth > Auto generate credentials**) and the owner's access grant in the same dialog, then a few minutes' wait while the grant applies; one repository variable; then opening the app on the phone. The OAuth consent screen from the spike (External, left in Testing) is reused: it asks only for name and email, so Testing is enough indefinitely. The OAuth settings never go through Terraform's `google_iap_settings`, which would store the client secret in state. In a project with no organisation, `iap_enabled` from Terraform or `--iap` from `gcloud` gives a 502 ("Empty Google Account OAuth client ID(s)/secret(s)") until that console step is done (#196). Agents can't do any of this: IAM, IAP settings and consent screens are the owner's (ADR-0007).
- **Carried from Phase 2, alongside the build:** #89, the live extraction check, and steps 1 and 5 of the Phase 2 report, the feed and cost check. A thin feed is the first thing daily use would show, so they shouldn't wait for the milestone.
- **Agents need:** nothing new once #174 merges. The lead should confirm the agents can reach Docker from a worktree in the first batch.
- **Brief gap, for the lead:** `test-author.md` and `worker.md` assume `backend/tests/`, `make lint` and `make test`. Client issues name their paths and `make client-*` commands, which should be enough. If it isn't, that's a small Tier C fix.

## First point the human can use something

After #179, the scaffold's placeholder screen loads on the phone behind IAP and installs, and `/api/feed` returns the live feed in the browser. That tests the cloud setup early. Each later client merge then deploys by itself, so after #180 and #181 the phone shows the ranked feed, and #182 follows directly with articles readable. Feedback on first use goes in the Phase Log, and the lead makes issues from it.

## Where this phase differs from "Cost of failure"

The statement in `CLAUDE.md`, confirmed for Phase 3 by merging PR #163, covers this phase. These are the failures above the bar in it, and each carries `**Risk:** high`:

- anyone but the owner reading content or writing feedback (#175, #176, #177, #180);
- losing a feedback event once it's accepted (#176);
- script running from article HTML in the same origin as the owner's IAP session (#182);
- a privileged deploy that widens what it can change, or builds third-party code where credentials can reach it (#178).

**A bill from a flood** no longer needs a stop. Under IAP, refused requests start no instance, aren't counted or billed as Cloud Run requests and write no request log, so a flood from strangers costs nothing measurable. On the spike's billing day, the Requests SKU showed 188 requests against 4,500 refused, matching only the owner's own use, and the day cost £0.00 inside the free tier (#196, spike A). IAP's own quota of 360,000 browser requests a minute throttles a very large flood, which could lock the owner out while it lasts, which is cheap. The only billed traffic is the owner's own. `api_max_instances` stays at 1 for cost and simplicity, since one user never needs two.

Poor ranking, a duplicate item, a stale feed, a failed refresh and an extra Reconnect tap are cheap. So is a queued feedback event lost by clearing the phone's storage.

## Decisions to confirm later

Each is reversible. The owning issue repeats it in its PR header.

| Decision | To undo |
|---|---|
| IAP on the Cloud Run service, with no `allUsers` grant, as the gate (#196) | Firebase Auth on a public service, the earlier design; `TokenVerifier` and the client's `api/` and `auth/` modules change |
| One service serves the client and the API on one origin, the client baked into the image | Host the client separately; needs CORS and a third-party cookie |
| `IapJwtVerifier` in `adapters/` delegates to `google.oauth2.id_token.verify_token` and checks the issuer itself; it sets `email_verified` true, because IAP admits only signed-in Google accounts | Verify with `PyJWT` and a key cache inside the same adapter |
| IAP's OAuth client and settings made in the console, outside Terraform; the owner's access grant made there too | Manage the grant with `google_iap_web_cloud_run_service_iam_member` |
| Reconnect by a plain navigation to `/reconnect`, only on a 401 while online | Change `client/src/auth/reconnect.ts` and the service-worker denylist |
| A user's sign-out clears local data and then opens `/?gcp-iap-mode=CLEAR_LOGIN_COOKIE` | Clear local data only |
| The API serves the client without its own login page, and applies the JWT check to static files as well as `/api/` | Exempt static files, which hold only public repo code |
| The CSP lives in `api/headers.py`, pinned by tests, not by a Tier C path | Add the file to `TIER_C_PATHS` |
| Preact, TypeScript, Vite and `vite-plugin-pwa` | Replace `client/`; the API and feed format are unaffected |
| `story_id` in the feed, `Feedback` unchanged, merged rows accepted as unjoinable | Add `url` to `Feedback`, which helps only events recorded after that |
| Saved copies at `saved/<id>.html` with `content_ref` left alone, amending ADR-0005 decision 6 | Add a repository method that updates one field |
| `like`, `dismiss` and `mute_source` with `value` 0 retract | Add explicit undo events |
| Dismiss and mute act in the client only in this phase | Phase 4 reads them in the pipeline |
| Top 50 article items prefetched, images online-only | Change the constant, or cache images |
| DOMPurify as a second sanitiser pass in the client | Remove it and rely on extraction and the CSP |
| Client tooling runs in Docker through `make` | Install Node and allow-list `npm` |
| API limits: 0 to 1 instance, 512 MiB, 1 vCPU, concurrency 8 | Change the Terraform variables |
| Only a sign-out the user chooses clears local data; a 401 or 403 keeps it | Clear on 403 as well |

**Unverified, because this design made no outbound requests.** Each comes from #196's desk study or memory, and #179 or the build tests it. If one fails, the lead calls the architect back.

- The Reconnect navigation returns into the installed app without typing after a real 401. Spike B is testing this now. Fallback: open `/reconnect` in Chrome, which shares the session, or a Custom Tab.
- IAP error code 9, "Failed OAuth redirect", happened once on desktop in spike B and Google calls it transient. If it recurs on the phone, the owner taps Reconnect again; nothing in the client can retry a page that IAP serves.
- `google_cloud_run_v2_service` takes `iap_enabled` in the GA provider without `launch_stage = "BETA"` or `google-beta`. `terraform validate` and the first plan show it, and #177 adds whichever is needed.
- The IAP service agent `service-<project number>@gcp-sa-iap.iam.gserviceaccount.com` exists, because the spike enabled the IAP API. If it doesn't, `gcloud beta services identity create --service=iap.googleapis.com` makes it, and #179 says so.
- `gcloud run services update --image` keeps IAP on, and needs no IAP permission beyond `roles/run.developer`. The first deploy after #179 tests it.
- `/?gcp-iap-mode=CLEAR_LOGIN_COOKIE` clears the IAP cookie. Fallback: sign-out clears local data only, which is what matters.
- `google.oauth2.id_token.verify_token` fetches IAP's keys on each call through `requests`. One user makes few calls, so that is a few small GETs to `gstatic.com` a day, a library's internal traffic, not project HTTP. If it is slow, #175 can pass a caching session.
- A WebAPK installed behind IAP fetches its icons with the owner's cookie. Spike B showed a real installed app, so this holds.

## Follow-ups and a decision for the lead

- **For the lead to decide:** `client` in `CONDITIONAL_CHECKS` passes the gate when absent, so a renamed job drops out silently. `REQUIRED_CHECKS` is stricter, but must match the `Protect main` ruleset, which the human changes. #174 uses `CONDITIONAL_CHECKS` until the lead chooses.
- #175: `newsfeed serve` goes through `cli.py`, which still loads `trafilatura` and `newspaper` (0.4 s, 67 MiB). Lazy-import them if cold start matters.
- #196's teardown, once the spike ends, keeps the OAuth consent screen and the IAP API, which #179 reuses.
