# ADR-0003: Phase 1 go/no-go: go, with scope changes for Phase 2

- **Status:** Accepted
- **Date:** 2026-10-17
- **Related:** #21 (this review); evidence in `docs/phase1-review.md`; results `eval/results/2026-10-17-27de250.md` and `eval/results/2026-10-17-54b9ac8.md`; #16, #18, #19, #20, #62 (Phase 1 inputs); #50, #65 (open follow-ups); `docs/blocked-publishers.md`; `PROJECT_PLAN.md` §6 and §11.

## Context

Phase 1 exists to test the project's riskiest assumption: that a polite, self-hosted pipeline can turn real news pages from the sources I read into clean, readable articles. The exit criterion (`PROJECT_PLAN.md` §6) is at least 85–90% clean extraction on the non-paywalled articles of the evaluation set, with failures understood and categorised. A "go" opens Phase 2: running the pipeline on GCP, ranking by rules and publishing a feed.

The evidence (details and per-domain tables in `docs/phase1-review.md`):

- **Evaluation:** the extractor chain is clean on **98.9%** (92/93) of the non-paywalled entries that could be snapshotted, after one per-domain override (`arstechnica.com` tries readability first). It was 97.8% before the override. 2 LWN entries are paywalled and couldn't be snapshotted (HTTP 403). The one remaining failure is a Simon Willison post where trafilatura adds the blog's "Recent articles" sidebar.
- **Live run** (25 sources, 720 articles, 2026-10-17): 91.7% `ok`, 7.1% `partial`, 1.1% `failed`. Six of the 8 failures are LWN subscriber-only pages (HTTP 403), which we respect. No Cloudflare challenges. Half of the `partial` results are audio, video and cartoon pages that really are short.
- **Coverage limits:** 13 publishers refuse our fetcher (robots.txt, 403 or 402) and were left out of the sources; they are listed in `docs/blocked-publishers.md`. That limits which publishers the feed can include, but not extraction quality.
- **My reading** of about 20 articles in the local preview (`newsfeed preview`), as the subjective check the issue asks for. Merging this ADR records that I did it and found the quality acceptable.

The review also found problems the score doesn't show:

1. **Paywalls are never detected.** No extractor returns `paywalled`, so members-only previews and subscriber notices are stored as short articles.
2. **Some `ok` results are wrong.** Trafilatura truncated Ars Technica articles (fixed by the override) and appends a sidebar on Simon Willison's blog (not fixed).
3. **`partial` mixes three things:** pages that are short by design (audio, video, cartoons, news-in-brief, link posts), paywall previews, and real extraction shortfalls.
4. **The title check measures the extractor's title, not the reader's.** Since #62 the evaluation accepts the page's `<h1>` when the extractor returned a metadata title such as "Headline | Quanta Magazine". The pipeline shows the feed's title and falls back to the extracted title only when the feed has none (0 of 719 articles in the live run), so this doesn't reach the reader today. It will for sources whose feeds lack usable titles.
5. **Everything was measured from a residential connection.** From GCP's datacenter IP ranges some publishers may block us (§11).

## Decision

**Go, with scope changes.** Phase 1 meets its exit criterion with a wide margin, and its failures are understood, so we start Phase 2. We change Phase 2's scope as follows:

1. **Publish must not present a non-clean article as a full one.** A `feed.json` item whose extraction is `paywalled`, `failed`, `skipped`, or `partial` below a usable length is published as headline, summary and link to the original, not as a reader article. The exact rule for `partial` is part of the publish issue.
2. **Add paywall and teaser detection** to the extract stage before publishing goes live, so members-only previews and subscriber notices get status `paywalled` rather than `ok` or `partial`. This uses only what the page or the HTTP response shows, never a way around the paywall (rule 6).
3. **Phase 2's exit includes a live extraction check from GCP.** During the unattended week, compare the per-domain `ok` rate from Cloud Run with this review's residential run, and record publishers that block datacenter IPs in `docs/blocked-publishers.md`. A source that drops sharply is either dropped or flagged as summary-only; that doesn't reopen this decision.
4. **Extraction observability (#65) is in Phase 2 scope**, split into smaller issues as it says, because a regression like the Ars truncation reports as `ok` and is invisible without per-domain word counts over time.
5. **Google News stays disabled** until #50 is decided. Phase 2 doesn't depend on it.
6. **The per-domain override for `arstechnica.com` is adopted** (`config/extraction.yaml`). Trafilatura stays the default first extractor: it is the best single extractor on the evaluation set (97.8%, against 86.0% for readability and 83.9% for newspaper4k).

Fixing the Simon Willison sidebar and choosing the visible `<h1>` as the extracted title are worth doing but don't block Phase 2. They go to the backlog.

### Alternatives considered

- **Go without scope changes.** Rejected: publishing `partial` and paywalled items as full articles would put teasers and empty audio pages in the reader, and an unattended run on GCP without per-domain metrics would hide regressions like the Ars truncation.
- **Stay in Phase 1 until the paywall and sidebar problems are fixed.** Rejected: they affect few articles, the headline score is well above target, and publish rule 1 contains the paywall problem until detection lands. They don't need GCP or ranking to be fixed, so they can run in parallel with the Phase 2 infrastructure work.
- **Stop.** Not supported by the evidence.

## Consequences

- Phase 2 can start. Its infrastructure decisions (Terraform or scripted `gcloud`, §11) and the GCP setup remain `needs-human`.
- The publish stage gets a rule for non-clean items, and the extract stage gets paywall detection, ahead of the client work in Phase 3. This adds two issues to Phase 2 but removes the need for the client to second-guess extraction status.
- The 98.9% figure is specific to the current sources and to snapshots taken in October 2026. Pages change, so the evaluation is re-run whenever extraction code or overrides change, and new sources get evaluation entries when they are added.
- **Revisit this decision** if the GCP live check shows the `ok` rate across all sources falling below 85%, or if the evaluation on refreshed snapshots falls below 85% and per-domain overrides can't bring it back. Either would need a new ADR on fetching from elsewhere or narrowing the sources.
