# ADR-0010: Client access through IAP, and feedback identity

- **Status:** Accepted
- **Date:** 2026-11-07
- **Related:** #171, #196 (decision and spike results); #172 and #173 (merged: the additions recorded here); `docs/phases/phase-3.md`; `PROJECT_PLAN.md` §5.2, §5.4; amends [ADR-0005](0005-content-retention.md) decision 6; [ADR-0006](0006-feed-json-schema.md); [ADR-0007](0007-agent-infrastructure-scope.md); [ADR-0009](0009-reader-client-stack.md); [ADR-0011](0011-render-sanitised-article-html-on-the-app-origin.md) (third-party article HTML on this origin).

## Context

The phone app must read `feed.json` and article text from a private bucket, post feedback, and let nobody but the owner do either. Article text is third-party content, so it can't sit in a public bucket or on a CDN. The first design used Firebase Auth on a public Cloud Run service with Firebase Hosting for the client. Two live spikes in #196 changed that: Identity-Aware Proxy (IAP, Google's sign-in gate placed in front of a Cloud Run service) turned out to refuse strangers before the container runs, at no measurable cost. Separately, ADR-0005 decision 6 said a like repoints the article's `content_ref`, and the hourly run would silently undo that. Last, the client needs a stable id for a story whose row id can change, and Phase 4 needs to join feedback to stories.

## Decision

**1. Access is IAP, turned on directly on the Cloud Run service.** The owner's Google account alone holds `roles/iap.httpsResourceAccessor`, and the service has no `allUsers` grant. Evidence from #196 spike A: 4,500 unauthenticated requests started no instance, were not counted or billed as Cloud Run requests, and wrote no request log. A flood from strangers therefore costs nothing measurable, and the only billed traffic is the owner's.

**2. One service serves the client and the API on one origin.** The API reads `feed.json` and article HTML through the `BlobStore` port and returns them to the owner only. The bucket stays private, there is no CDN, and no copy of article text exists outside the bucket. The IAP session cookie covers the client and every API call, which are same-origin.

**3. The API repeats the check.** Every route requires a valid `x-goog-iap-jwt-assertion` whose `email` is the owner's, and fails closed. `IapJwtVerifier` (an adapter) implements the `TokenVerifier` port, so core code imports no identity library. The check still holds if IAP is turned off by mistake or the grant is wrong. If the check can't be made (for example the key fetch fails), the API answers 503, not 401.

**4. IAP's OAuth client and settings are made in the console, never in Terraform.** `google_iap_settings` would store the client secret in Terraform state.

**5. Reconnect is a plain navigation, and only on a 401 while online.** The client sends `X-Requested-With: XMLHttpRequest` so IAP answers 401 rather than a redirect `fetch` can't follow. A 401 shows a "Reconnect" banner, and tapping it navigates to `/reconnect`, which the service worker always sends to the network. It does not use IAP's `DO_SESSION_REFRESH`: in spike B (#196) that mode only reached IAP's keep-alive page and needed Back.

**6. Feedback identity: `story_id` in the feed, `Feedback.article_id` unchanged.**
- Events store the id of the row the owner saw and are never rewritten.
- `story_id` is the id of the row at the end of the `declared_canonical` chain. The client keys seen, liked and dismissed state on it.
- Phase 4's join rule: follow `declared_canonical` for a stored row; match `article_id(canonical(row.url))` for a re-keyed row (`with_canonical_url` keeps `url`). A row merged into another on retry can't be joined. That loses one signal in a rare case, and is accepted.

**7. The API never writes `Article` rows.** A like copies the blob to `saved/<id>.html`, which the API reads first, and `content_ref` is left alone. This amends ADR-0005 decision 6, which pointed `content_ref` at the copy. The reason: `upsert_article` replaces a whole row and the rank stage rewrites every row in its window from a snapshot, so a repointed `content_ref` would be overwritten by the next run (`REVIEW.md`: a batch article is a snapshot). The API's only `Repository` write is `save_feedback`, keyed by a client-made id, so a retry is harmless. `saved/` stays outside both lifecycle rules.

### Alternatives considered

- **Firebase Auth on a public service, with Firebase Hosting.** Rejected: a flood of requests is billed with no free stop, it needs a hand-made sign-in flow in the client, and deploying Hosting needs a privileged `firebase-tools` deploy.
- **A load balancer with IAP.** Works, but adds a monthly charge for one user.
- **The client on another origin (Hosting or a bucket).** The IAP cookie becomes third-party to the page, and CORS is needed (#196 §2b).
- **A public bucket, or signed URLs.** A public bucket publishes third-party text. Signed URLs hand out working links to it and need a service to mint them anyway.
- **Repointing `content_ref` on a like (ADR-0005 decision 6).** Overwritten by the next run, as above. A repository method that updates one field would also work but is a contract change.
- **Adding `url` to `Feedback`.** Would make the join exact, but helps only events recorded afterwards; reconsider if merged rows turn out to matter.

## Consequences

- Strangers are refused by Google before the container runs; the app has no sign-in code and loads no third-party script.
- A cold start can delay the first load in Chrome and the first after a new version. The installed app shows its IndexedDB copy first, so opening it isn't delayed.
- A one-off console step is needed after `terraform apply` (the OAuth client and the owner's grant). In a project with no organisation, `iap_enabled` gives a 502 until it is done.
- A Trusted Web Activity in Phase 5 may fail Digital Asset Links behind IAP, because the check is made without the owner's cookie and IAP covers every path. Fallbacks: live with a URL bar, or build a Compose app that uses IAP's programmatic access.
- A `failed` row re-keyed on retry gets a new `story_id`, so a dismissed story can come back. That is cheap.
- Reversible: Firebase Auth on a public service (`TokenVerifier` and the client's `api/` and `auth/` modules change); host the client separately (CORS and a third-party cookie); add explicit undo events or a `url` field later.
- Revisit if IAP's behaviour changes (session lifetime, error 11, the quota of 360,000 browser requests a minute), or if a user other than the owner is ever added.
