# ADR-0016: Reader preferences

- **Status:** Accepted
- **Date:** 2026-11-18
- **Related:** #331 (epic), #333 (the owner's layout pick), #334 (this contract: `PreferenceState`, `ReaderPreferences`, `Repository.get_preferences`/`save_preferences`, backup), #335 (`config/preferences.yaml`), #336 (topic marks), #337 (API), #338 (screen), #339 (mute becomes Hide); `docs/phases/phase-4.md` "Epic: reader preferences"; [ADR-0010](0010-client-access-and-feedback-identity.md), [ADR-0013](0013-feed-views-by-source-query.md), [ADR-0014](0014-personal-ranking.md), [ADR-0015](0015-ab-check-by-interleaving.md).

## Context

The owner wants to say what they see more or less of, per source and per topic, from a menu in the app, instead of relying only on the rules in `config/interests.yaml` and what the personal ranking learns. Phase 3 deferred "Choose News sources in Settings" (`docs/milestones/phase-3.md`), and the card's "mute source" lives only on the phone, where it can't be seen or undone. The owner settled the behaviour in #314 and the layout in #333 (2026-11-17): sources in sections, then topics, and no regions for now.

Stored scores are never recomputed (ADR-0013), so anything baked in at rank time would take up to 72 hours to reach older items. The owner's choices can't be rebuilt from anything else (CLAUDE.md, "Cost of failure").

## Decision

**Four states per source and per topic: Hide, Fewer, Show, More** (`PreferenceState`). No regions in this version; see `DEFERRED_WORK.md` "Regions in reader preferences". Adding them later is additive (a new map with an empty default), so no stored data would need migrating.

**Defaults in config, choices in the store.** `config/preferences.yaml` (#335) holds the multiplier for each state (Hide 0, Fewer 0.5, Show 1, More 2, with the product of the owner's multipliers clamped to 0.25 to 4), the starter set, a display label per source, the preference topics (each covering one or more `interests.yaml` topics), and the source sections. The screen groups sources into sections derived from the tags in `config/sources.yaml` (`docs/phases/phase-4.md`), so a new source lands in a section with no code change. The owner's choices are stored as `ReaderPreferences`, holding **only overrides** of the defaults: a source or topic absent from the maps takes the configured default, so a changed default reaches every key the owner hasn't set.

**Keyed by user id, with no multi-user features.** One record per `user_id` (`"owner"`, ADR-0010): a SQLite row in `preferences`, a Firestore document in `preferences` with the user id as its id. `version` goes up by one on each save through the API, so a client can tell its copy is stale. Adding a user later means adding a key and sign-in, not a migration.

**Applied in the API at read time.** When a view is served (#337), Hide drops the item in every view; Fewer and More multiply it in `score` views, on both A/B sides (ADR-0015), so the A/B comparison still measures the learned part alone. More on a topic also divides out the rules' demotion of that topic (`pref:undemote:<t>`, outside the clamp), because x2 alone can't lift a sport headline's x0.14. Hide wins over everything; otherwise the multipliers multiply. Each factor appears in the served item's breakdown. Preferences are never baked into stored scores, which would delay a change by up to 72 hours (ADR-0013).

**Ingest is unchanged.** A hidden source is still fetched and ranked, so un-hiding it is instant and needs no backfill.

**Backed up daily, newer stored record wins on restore.** Preferences join the daily backup (`backup.py`, one record per user). A restore keeps the stored record when its `updated_at` is later than or equal to the backed-up copy's, logs that it kept it (never the settings), and saves the backed-up copy otherwise, including when nothing is stored.

**It replaces** the deferred "Choose News sources in Settings", and the card's "mute source" becomes Hide (#339), visible and reversible in the menu.

**Rejected:**
- *Applying preferences at rank time*: a change would take up to 72 hours to reach older items (ADR-0013), and every user would need their own stored scores.
- *Skipping hidden sources at ingest*: un-hiding would show nothing until the next fetches, and it would change what the pipeline holds for a display choice.
- *Storing every key's state, defaults included*: a changed default would never reach a key the owner hadn't touched, and the stored record would need rewriting whenever a source is added.
- *Refusing a restore over newer preferences without a `--force` flag*: keeping the newer record loses nothing. The default keeps it; `newsfeed restore-backup --force` (#373) replaces it with the backup's copy when a rollback is wanted.

## Consequences

- A change shows on the next refresh, and the pipeline stays shared, which keeps per-user views cheap later.
- The API reads one more record per request; on Firestore that is one document read per served view.
- The owner's choices are now data that can't be recreated, so they are in the backup, and any later contract change to them is high risk.
- Restoring an older backup doesn't roll back a newer choice unless `newsfeed restore-backup --force` is used (#373).
- Revisit when a second user is added (sign-in and keys, stop-the-line), or when regions come back.
