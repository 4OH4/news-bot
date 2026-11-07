# ADR-0009: Reader client stack

- **Status:** Accepted
- **Date:** 2026-11-07
- **Related:** #171; `docs/phases/phase-3.md` ("Framework", "Offline", "Build and deploy"); `PROJECT_PLAN.md` §5.2 and §11; [ADR-0010](0010-client-access-and-feedback-identity.md) (one origin for client and API).

## Context

Phase 3 builds the phone app: a feed list, an article reader and an offline cache, installable from Chrome on Android. `PROJECT_PLAN.md` left the framework open ("decide at the start of Phase 3"). The client is a single-user, read-mostly app. It needs a small bundle for a phone, static output that can be baked into the backend image (ADR-0010 serves it from the API's origin), and a stack that agents can test without a browser farm (Testing Library for components, Playwright for end to end).

## Decision

**We will build the client with Preact, TypeScript, Vite and `vite-plugin-pwa`.**

- **Data and offline.** IndexedDB holds the data: the last good feed, the text of the top 50 article items, anything opened or liked, the feedback queue and per-story state. The app reads it first and refreshes behind it. The service worker precaches the app shell only, never data, and deletes old caches when a new version activates.
- **Build.** The client lives in `client/`. A Node stage in `backend/Dockerfile` builds it and the runtime stage serves the output, so there is one image and one deploy (ADR-0010). Client tooling runs in pinned Docker images through `make client-*`, because this machine has no Node.
- **Third-party code.** The client loads no third-party script, and the CSP stays at `script-src 'self'`.

### Alternatives considered

- **React.** Familiar and has the biggest ecosystem, but costs about ten times the bundle, which this client gains nothing from. Preact keeps the same component model and test tooling.
- **Svelte or SvelteKit.** Small output, but SvelteKit's routing and server features go unused here, and the smaller ecosystem means fewer ready-made PWA and test integrations.
- **Next.js.** Built around server rendering and its own server. The client is static files behind another service, so most of it would go unused.
- **Plain TypeScript.** No framework to learn, but state and rendering become hand-written code that grows with every screen.

## Consequences

- Small bundles and a fast start on a phone, and static output that fits the existing image and deploy.
- Some React libraries need `preact/compat`. Prefer libraries that work with Preact directly.
- Offline behaviour has two parts to keep apart: the service worker (shell) and IndexedDB (data). Stale-shell bugs are the usual failure, so the cache clean-up on activation matters.
- Reversible: replace `client/`. The API and the `feed.json` contract (ADR-0006) don't depend on the framework.
- Revisit if the bundle or the hand-written state code becomes a problem, or if Preact's compat gaps block a library the reader needs.
