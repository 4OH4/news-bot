# ADR-0011: Render sanitised third-party article HTML on the app origin

- **Status:** Accepted
- **Date:** 2026-11-07
- **Related:** #182 and PR #226 (the reader screen, "For human review"); #229; Phase Log #187 (comment of 2026-11-07); `docs/phases/phase-3.md`; `CLAUDE.md` "Cost of failure"; [ADR-0009](0009-reader-client-stack.md) (client stack, `script-src 'self'`); [ADR-0010](0010-client-access-and-feedback-identity.md) (one origin, IAP).

## Context

The reader screen (`/article/<id>`, #182) shows article HTML taken from other people's websites. ADR-0010 puts the client and the API on one origin behind Identity-Aware Proxy (IAP, Google's sign-in gate), so that HTML is shown on the same origin as the owner's signed-in session. If hostile markup ever ran script there, the script would act as the owner inside the app. The worst lasting harm is false feedback: feedback events can't be recreated (`CLAUDE.md` "Cost of failure"), and a forged event looks the same as one the owner made. The owner accepted this residual risk in the lead chat on 2026-11-07, guarded by the layers below, and asked for it to be recorded where later work will find it.

## Decision

**We will show article HTML inline on the app origin, after sanitising it twice, under a strict Content Security Policy (CSP).** Each guard is pinned by tests, as they are on `main` after PR #226:

1. **Backend sanitising at extraction, with `nh3`** (a binding to the Rust `ammonia` sanitiser). `backend/src/newsfeed/extract/sanitize.py` (`sanitize_html`, `ALLOWED_TAGS`, `ALLOWED_ATTRIBUTES`) runs on every extractor's output before it is stored. Pinned by `backend/tests/extract/test_sanitize.py`, in particular `test_removes_dangerous_elements_with_their_content`, `test_removes_event_handler_attributes`, `test_unwraps_links_with_unsafe_schemes`, `test_drops_images_without_a_safe_source`, `test_strips_attributes_outside_the_allowlist` and `test_output_tags_are_all_allowed`.
2. **Client sanitising with DOMPurify, using the same allowlist.** `client/src/reader/sanitize.ts` (`cleanArticleHtml`) reads its lists from `client/src/reader/allowed-tags.json`, keeps only `http` and `https` URLs and cuts `<style>` blocks before parsing. `backend/tests/extract/test_client_tag_list.py` fails if the JSON lists differ from the backend's. `client/src/reader/dangerous-html.test.ts` checks that `dangerouslySetInnerHTML` is used exactly once, in `client/src/reader/`, fed by `cleanArticleHtml`, and that the lists hold no script-capable tags.
3. **CSP `script-src 'self'`.** The production headers are `SECURITY_HEADERS` in `backend/src/newsfeed/api/headers.py`, set on every response by `SecurityHeadersMiddleware`; the client declares the same values in `client/security-headers.json`, which `vite preview` and Playwright use. Pinned by `backend/tests/api/test_headers.py` (`test_security_headers_match_the_client_declaration`, `test_every_response_carries_the_security_headers`) and `client/src/security-headers.test.ts` ("allows scripts only from our own origin"). The same headers carry `X-Content-Type-Options: nosniff`, which `script-src 'self'` relies on: without it, a stored `text/html` article blob on this origin could be loaded as a "same-origin" script. Pinned by the same backend tests and "sets the other headers the API sets".
4. **CSP `connect-src 'self'`**, which stops a script from sending data to another host with `fetch`, XHR or WebSockets. Pinned by the same tests ("allows connections only to our own origin").
5. **Hostile payloads in real Chromium.** `client/e2e/reader.spec.ts` serves a script tag, an `onerror` image, a `javascript:` link, an iframe, a style block and an `onload` SVG through the reader under the production headers. "hostile markup is removed, no payload runs, and nothing trips the CSP" fails if any element survives, any payload runs or the CSP reports anything. "form, input and base elements go, and the text inside the form stays" covers forms.

### Residual risk

A bypass of every layer at once: markup that `nh3` and DOMPurify both let through as script, and that `script-src 'self'` doesn't block. That needs a flaw in both sanitisers that the same input triggers, or a same-origin script the page could be made to load.

What such a script could do, as the owner, within the app:
- read the feed, article text and the IndexedDB copy, through same-origin requests (`GET /api/feed`, `GET /api/articles/{id}`), which carry the IAP cookie;
- send false feedback with `POST /api/feedback`, which stores an event that can't be told from the owner's and, for a like, copies an article to `saved/`. That endpoint is the API's only write;
- change what the reader shows in that tab;
- **persist beyond that page.** The service worker (`VitePWA` in `client/vite.config.ts`) precaches `**/*.{js,css,html,png,webmanifest}`, serves those entries cache-first and uses `/index.html` as `navigateFallback`. A same-origin script can overwrite an entry with `caches.open(...)` and `put('/index.html', new Response(...))`, or do the same for a hashed `/assets/*.js` file. On every later app open the service worker returns the forged response, which carries no `Content-Security-Policy` header, so inline script runs with no `script-src` or `connect-src` limit. This lasts until a deploy replaces that entry, or indefinitely if the script poisons the cache again each time it runs. Forged feedback, the harm that can't be undone, then becomes continuous rather than limited to one session. Writing events into the IndexedDB feedback queue is a second, smaller route for forged events to persist and be sent later. (Reasoned from the config and how Workbox and CSP behave; not run in a browser.)

What it couldn't do:
- **Call other origins** with `fetch`, XHR or WebSockets (`connect-src 'self'`), or load scripts from them (`script-src 'self'`), but only from a page served with the CSP: a poisoned cache entry, as above, drops the CSP for every later page. Even on the first page this is not a full stop on leaks: the CSP still allows images from any `https:` host (`img-src https: data:`) and does not restrict top-level navigation, so a running script could still send small amounts of data out in a URL.
- **Reach any other site as the owner.** The IAP session is scoped to this service, and other Google services sit on other origins.
- **Read IAP's cookie.** *Unverified:* the cookie is set by Google, not by this code, so the repo can't show that it is `HttpOnly`. To check, open the app in desktop Chrome, then DevTools > Application > Cookies, and look at the `HttpOnly` column for the IAP cookies. Even if the cookie were readable, `connect-src` would stop `fetch` sending it elsewhere, but the image and navigation paths and a poisoned cache entry above would not.
- **Change or delete stored data beyond feedback events.** The API has no other write route (ADR-0010 decision 7).

One more path is narrower: if the owner opens `/api/articles/<id>` directly, the browser renders the stored HTML as a page with only guard 1 and the CSP (guards 3 and 4), not DOMPurify. The CSP header is set on that response too.

### Alternatives considered

- **A sandboxed iframe on a separate origin.** Safest: script in the article couldn't touch the app or its session at all. Rejected for now: it needs a second origin (another service or domain behind its own access control, since article text can't be public), messaging between frames, and iframe height and theme handling that makes the reading layout harder.
- **Text only.** No markup, so nothing to run. Rejected: it loses headings, lists, quotes, links and images, which is most of what makes the reader worth using.

## Consequences

- The reader keeps formatting, links and images, on one origin with no extra service.
- The allowlist is now a security boundary in two places: any change to it is a change to this decision, and the pytest test keeps the two copies equal.
- Forged feedback, the one lasting harm, stays possible only through a bypass of every layer, but once that happens it can persist through the service-worker cache until a deploy or longer. A mitigation (for example, the service worker checking its cached shell entries) would be separate work.
- Reversible: move the article into a sandboxed iframe on another origin, or render text only. Either replaces `client/src/reader/` and leaves the API and the stored HTML as they are.
- **Revisit when:**
  - the app gains a user who isn't also a developer;
  - production starts;
  - a DOMPurify or `nh3` (or `ammonia`) security advisory affects the tags, attributes or URL rules on the allowlist;
  - the allowlist gains tags or attributes, for example `iframe`, `svg`, `math`, `style`, `form`, `object`, `embed`, event-handler attributes or `style` attributes;
  - the app gains endpoints that change or delete stored data beyond feedback events;
  - the CSP is loosened, for example `script-src` gains another source or `'unsafe-inline'`, or `connect-src` gains another host;
  - `X-Content-Type-Options: nosniff` is removed;
  - the service worker's caching strategy changes (what it precaches, cache-first serving or the navigation fallback), since that decides how long a bypass can persist.

  In each case, weigh the alternatives above again.
