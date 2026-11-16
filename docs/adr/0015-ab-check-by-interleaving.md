# ADR-0015: A/B check by team-draft interleaving

- **Status:** Accepted
- **Date:** 2026-11-16
- **Related:** #320 (this contract: `Feedback.view`, `Feedback.arm`, `RankingArm`, the `arm` item field), #326 (API interleaving), #327 (client sends `view` and `arm`), #329 (`newsfeed inspect ab`); `docs/phases/phase-4.md` "A/B check"; `PROJECT_PLAN.md` §6 (Phase 4 exit: a measurable lift in open and read rate versus rules-only); [ADR-0014](0014-personal-ranking.md).

## Context

Phase 4's exit asks for a measurable lift in open and read rate from personal ranking versus rules-only. There is one reader. Opens ranged from about 4 to 33 a day in Phase 3, so an A/B split by day would be swamped by day-to-day swings in the news, the owner's mood and time spent, and would need months to show any realistic lift.

## Decision

**Method: team-draft interleaving in the Feed.** Each Feed list is built by letting the rules order and the personal order (ADR-0014, decision 5) take turns to pick their best remaining item, with a coin per round seeded by the run, so every page of one run is consistent. Each item carries the `arm` (`rules` or `personal`) that picked it, as an optional field in the served item (`docs/feed.schema.json`). Both arms see the same candidates, preferences, diversity rule and coverage, so the comparison measures the learned part alone.

**Attribution.** The phone sends `view` and `arm` back with `open`, `read` and `like` events (#327); they are stored on `Feedback` (`view`, `arm`, with `user_id`). Each open or read is credited to its arm. A credited read is 30 seconds or more. Events from other views, or without an arm, are not credited.

**Sizing.** About 10 credited Feed opens a day is assumed (to be measured once #327 is live). A two-sided test at 5% significance with 80% power needs:

| Personal side's share of credited opens | Credited opens needed | At 10 a day |
|---|---|---|
| 65% | about 85 | 9 days |
| 60% | about 195 | 20 days |
| 55% | about 780 | 11 weeks |

**Stopping rule.** Run at least 21 days and at least 200 credited opens. Then the result is "personalised ahead" if the 95% interval of the personal share is wholly above 50%, "rules ahead" if wholly below, otherwise "no clear difference". A per-day tally checks that one heavy day isn't driving it. Reads are reported the same way. `newsfeed inspect ab` prints all of it (#329).

**Rejected: A/B by day** (alternating rankings on alternate days): the day-to-day variance hides the effect for months.

## Consequences

- The A/B record is part of the owner's feedback, which can't be recreated, so it is backed up daily with the rest of the feedback (#330).
- The record starts filling only once the contract (#320), embedder (#321, #322), profile (#323), API (#326) and client (#327) are deployed; the milestone is at least three weeks after that.
- A lift smaller than about 60/40 reads as "no clear difference yet" within the phase. That is an acceptable outcome under "Cost of failure" (poor ranking is cheap), not a failure of the check.
- Feed items now differ in order from `feed.json`'s score order; News and Longer are unaffected.
- Revisit if credited opens are far below 10 a day (the check can't finish), or if a second user arrives (the arms would then be compared per user).
