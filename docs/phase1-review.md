# Phase 1 review: extraction failures

The evidence behind the Phase 1 go/no-go decision (ADR-0003, issue #21). It covers the evaluation set and one live run against every configured source, groups what went wrong by cause and domain, and says which `config/extraction.yaml` overrides the evidence supports.

This file describes structure and counts only. It contains no article text.

## Sources of evidence

| Evidence | What it is | Where |
|---|---|---|
| Evaluation, before | `make eval` at 27de250 over 98 snapshotted pages (2 LWN pages not snapshotted) | `eval/results/2026-10-17-27de250.md` |
| Evaluation, after | The same snapshots with the Ars Technica override below, at 54b9ac8 | `eval/results/2026-10-17-54b9ac8.md` |
| Live run | `make run` on main 6a77393, run id `20261017T173514-077243`, 2026-10-17 17:35–17:56 UTC, from a residential UK connection | Local SQLite database and JSON log (not committed) |

## Headline numbers

| Measure | Result | Target |
|---|---|---|
| Evaluation, clean on non-paywalled entries (`chain`) | **98.9%** (92/93), up from 97.8% (91/93) | 85–90% |
| Evaluation, paywalled entries clean (`chain`) | 5/5 (none *detected* as paywalled, see below) | — |
| Live run, status `ok` | 660 of 719 stored (91.8%; 720 attempted, 1 merged as a duplicate) | — |
| Live run, `ok` or `partial` (some content stored) | 711 of 719 (98.9%) | — |
| Live run, `failed` | 8, of which 6 are LWN subscriber-only pages (HTTP 403) | — |
| Live run, Cloudflare challenges | 0 | — |

The live run fetched 25 sources, 721 feed entries, 720 new articles, 1 duplicate merged, 173 canonical URLs refined (2 to another domain). A first run over an empty database took 21 minutes, mostly per-host rate limiting.

`ok` means the extractor got at least 150 words (the default `min_words`). `partial` means it got some content but fewer words. In the live run most `partial` results are pages that really are short, not extraction failures (see "Short pages" below).

## Evaluation failures, by cause

### Before (27de250): 2 failures

| Entry | Cause | Diagnosis |
|---|---|---|
| `ars-2026-10-16-openai-models-hacked-a-crypto-wallet` | Word count low (251 < 650) | **Trafilatura truncates the article.** It keeps the first 4 blocks (up to and including the first `<h2>` section) and drops the remaining 10 paragraphs and a blockquote. Readability and newspaper4k both get 820 words, and every block trafilatura kept is in their output. |
| `willison-2026-10-15-latex` | Word count high (470 > 410) | **Trafilatura adds the blog's "Recent articles" sidebar** (an `<h2>` and 4 list items) to the post. It does this on all 4 Simon Willison evaluation entries and on 27 of the 30 Simon Willison articles in the live run; it only crosses the expected range on this one. |

### After (54b9ac8): 1 failure

Only `willison-2026-10-15-latex` remains. No entry that passed before fails now.

## Overrides

### Applied: `arstechnica.com` tries readability first

```yaml
arstechnica.com:
  order: [readability, trafilatura, newspaper4k]
```

Evidence:
- On the 4 Ars evaluation entries, readability is clean on 4/4 and trafilatura on 3/4. Measured as overlapping 8-word runs, readability's output contains everything trafilatura's does on the truncated page (trafilatura has 30% of readability's text).
- On the other 3 entries the two agree to within 1–6%. Where trafilatura has more text, it is the affiliate-link disclaimer and the author bio at the end of the page, which readability leaves out.
- In the live run, 15 of the 18 Ars articles stored came out of trafilatura at 233–457 words, all `ok`. Ars news stories are usually longer, so many of these are probably the same truncation, reported as success. The live database doesn't keep the fetched page, so this can't be confirmed offline; the reading step in the PR includes two Ars articles for that reason.

Caveat: the expected word range for this entry was estimated from readability's output when the evaluation set was built (its `notes` say so). The block-level comparison above doesn't depend on that estimate.

### Considered and not applied

| Domain | Override considered | Why not |
|---|---|---|
| `simonwillison.net` | `order: [readability, …]` to drop the sidebar | Readability drops the sidebar but also drops content trafilatura keeps on some posts (on `latex`, a paragraph and the code snippets). The evidence is mixed, and the word ranges for this site were estimated from readability's output, so the evaluation would favour readability whether or not it is better. Needs a targeted fix instead (follow-up below). |
| `simonwillison.net`, `nature.com` | Lower `min_words` so short posts are `ok`, not `partial` | Only changes the status label, not the content. Nothing in the evaluation set fails because of it, and on Simon Willison's site about 50 of the counted words are the sidebar, so a lower threshold would hide that problem. Better handled by how publish treats `partial` items. |
| `lwn.net` | `mode: summary_only` | 6 of 15 LWN articles are subscriber-only for their first week and return HTTP 403, but 6 others are `ok`. A domain-wide summary-only mode would throw those away. |
| Any other domain | — | No other domain has an evaluation failure for the `chain` extractor. Trafilatura is the best single extractor (97.8%, against 86.0% for readability and 83.9% for newspaper4k), so it stays the default. |

## Live run, by cause

### Failed (8)

| Cause | Count | Domain | Notes |
|---|---|---|---|
| HTTP 403 on the article page | 6 | lwn.net | Subscriber-only articles. The two LWN evaluation entries marked `paywalled` also return 403, which is why they aren't snapshotted. This is a paywall, not bot blocking; we respect it (rule 6). |
| No usable content (under 50 words) | 1 | lwn.net | Best result 43 words: probably another subscriber-only page served as a short notice rather than a 403. |
| No usable content (under 50 words) | 1 | newyorker.com | A humour piece; best result 38 words. Cause not known (could be a metered page). |

### Partial (51)

| Cause | Count | Domains |
|---|---|---|
| Audio, video or cartoon page: the page has only a short standfirst under a player or image | 26 | theguardian.com `/news/audio/` (12), newyorker.com cartoons, podcast and cover pages (9), bbc.co.uk `/news/videos/` (3), aeon.co `/videos/` (2) |
| Short news items that are short by design | 12 | nature.com (86–145 words) |
| Short link-blog and quotation posts | 8 | simonwillison.net (78–138 words, including the sidebar) |
| Members-only preview | 2 | 404media.co (62 and 112 words). The evaluation's paywalled 404 Media entry has the same shape: a short preview, then a members gate. |
| Short subscriber notice | 2 | lwn.net (78 and 102 words) |
| Short video page | 1 | businessinsider.com |

### `ok` but not right

The status hides two problems:
- **Ars Technica truncation** (above), fixed by the override for future runs.
- **Simon Willison sidebar**: 27 of 30 articles end with the "Recent articles" list.

### Blocked publishers and bot detection

No Cloudflare challenges and no 403s outside LWN's subscriber pages. Publishers that refuse our fetcher entirely were kept out of `config/sources.yaml` during curation (#16) and are listed in `docs/blocked-publishers.md`. All of this was measured from a residential connection; from GCP's datacenter IP ranges, results may be worse (PROJECT_PLAN.md §11), so Phase 2 measures it again.

## Gaps the numbers don't show

- **Paywalls are never detected.** No extractor returns the `paywalled` status, so on all 5 paywalled evaluation entries the chain reports `ok` and stores the visible part. Members-only previews (404 Media) and subscriber notices (LWN) appear as short articles, not as paywalled ones.
- **The title check measures the extractor's title, not the reader's.** Since #62 the evaluation accepts the page's main `<h1>` when the extractor returned the metadata title (for example a title with a "| Quanta Magazine" suffix). The pipeline and preview show the **feed's** title, and use the extracted title only when the feed gives none. That happened for 0 of 719 articles in the live run, so suffixed metadata titles don't reach the reader today. They will matter for sources whose feeds don't carry usable titles (Hacker News, Google News).
- **The evaluation set is close to its own sources.** The 100 URLs came from the same sources as the live run, mostly from the week before it, and some expected word ranges were estimated from one extractor's output. A 98.9% score is a strong signal for these sources, not a promise for new ones; new sources should get evaluation entries when they're added.
