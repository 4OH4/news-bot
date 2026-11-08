# News source catalogue: research protocol

**Status:** agreed protocol, research not started. **Written:** 2026-11-08.

**Where things live.** This protocol is committed to the repo. The catalogue and the raw data it is built from are kept **outside** the repo, in `~/.newsfeed/research/news-sources/` (see "Where the results go"). They are never committed, the same arrangement as the evaluation snapshots in `~/.newsfeed/eval-fixtures`.

## Purpose

The goal is to build a master list of English-language news publishers, so we can see how much of the news landscape the feed covers. For each publisher, the list shows how important it is, where it is based, what it reports on, and whether we use it, can't use it or haven't tried it yet. It is a research document, not pipeline configuration: `config/sources.yaml` remains the list of sources the pipeline actually fetches, and nothing is added there because it appears here.

"Important" doesn't have one definition. We approximate it with three separate signals: **reach** (how many people read it), **trust** (whether readers and editors rely on it) and **authority** (whether other outlets and Wikipedia build on its reporting). Each signal stays visible in the catalogue, and the combined score is just a default ordering that can be reweighted.

Publishers come first. Blogs, newsletters and aggregators may get their own catalogue later.

## Scope

- **Publishers that do original news reporting:** newspapers, broadcasters, wire services, digital-native newsrooms and news magazines. Aggregators and content farms can enter the candidate pool, but they are recorded as `kind: aggregator` and left out of the ranking (see "Ranked entries").
- **One entry per editorial brand, not per domain.** BBC News is one entry covering `bbc.co.uk` and `bbc.com`. Signals measured per domain are added up across an entry's domains, and the method is noted. A brand that shares a domain with others (BBC Sport, a Guardian section) isn't split out. Each masthead in a chain (for example the local papers owned by Reach plc) is its own entry, with the chain in `ownership.owner`. Brands that the Digital News Report counts as TV or radio only are linked to their online entry where one exists, and their offline reach is kept as a separate signal.
- **Specialist news publishers count** if they are significant news outlets in their own right, such as the FT, Bloomberg, Politico or Nature News. `topics` records the subject. Trade press (industry newsletters and trade magazines) is out of scope.
- **English language.** A publisher that is mainly in another language is included if it has a significant English edition: its own English section or feed with new articles every day. Examples are DW, France 24, NHK World, Le Monde in English, El País English edition or Al Jazeera English. The `languages` and `english_edition` fields record how much is in English. A publisher with no English edition is out of scope for now. If it appears in a candidate source anyway, it is still recorded with `english_edition: none`, so no data is lost.
- **No data is dropped.** Every candidate stays in the catalogue with all the data we collected for it. Being unreliable, deprecated, an aggregator or state-controlled is recorded as a flag or a field, never by removing the entry. The "top 100" and any other shortlist are views of the catalogue, not separate lists.

## Building the candidate pool

Candidates come from the union of these sources. Each one is recorded in the Sources table (`sources.md` in the output folder; see "Where the results go") with its edition and the date we retrieved it.

| Key | Source | What it gives |
|---|---|---|
| `dnr` | Reuters Institute *Digital News Report* (latest edition) | Weekly online and offline reach per brand, and brand trust scores, by country, from surveys |
| `rsp` | Wikipedia: *Reliable sources/Perennial sources* | Editors' reliability rating (generally reliable, no consensus, generally unreliable, deprecated, blacklisted) and the discussions behind it |
| `wiki-cites` | English Wikipedia `externallinks` table from the Wikimedia database dumps, counted by domain once for the whole pool | How often English Wikipedia links to the domain |
| `tranco` | Tranco list (dated snapshot) | Research-grade popularity rank per domain |
| `crux` | Chrome UX Report popularity rank, from the published monthly top lists (zakird/crux-top-lists on GitHub), not BigQuery, so no GCP project or bill is involved | Rank band per origin (top 1k, 10k, 100k …) |
| `similarweb` | Similarweb public rankings and figures, including those Press Gazette republishes | Monthly visits and country rank |
| `press-gazette` | Press Gazette top-50 news websites (UK and US, monthly) | Ranked visits, with month-to-month changes |
| `mediacloud` | Media Cloud source collections by country | Lists of national and local outlets per country, and inbound link counts |
| `gdelt` | GDELT source volumes | How many articles each domain publishes, and how often other outlets reprint or cite it |
| `state-media-monitor` | State Media Monitor (Media and Journalism Research Center) | Classification of state-funded and state-controlled media by editorial independence |
| `mom` | Media Ownership Monitor (RSF and partners) | Who owns which outlets, country by country |
| `rsf` | RSF World Press Freedom Index (latest year) | Press freedom score per country |
| `online-pop` | World Bank / ITU: internet users by country (latest year) | Online population per country, to turn Digital News Report percentages into estimated audiences |
| `pew` / `ofcom` | Pew Research (US) and Ofcom News Consumption survey (UK) | Reach by brand from national surveys |

Other sources (national press councils, audit bureaus such as ABC in the UK, prize records) can be added if they fill a gap. Each one gets a row in the Sources table.

## Two levels of detail

The catalogue holds two kinds of data, and they cost very different amounts to collect, so they are collected to different depths.

- **The pool: every candidate, with no cap.** Each entry gets the data that can be looked up in bulk by domain: the Wikipedia rating, Tranco and Chrome UX Report ranks, Wikipedia citation counts, any reach figure already in a harvested table (Digital News Report, Press Gazette, published Similarweb lists), and a combined score and rank. This costs almost nothing per entry, so nothing is left out to save effort. Expected size: roughly 400 to 700 entries.
- **Full enrichment: the ranked top, plus outlets added to fill regional gaps.** These entries also get the fields that need research or judgement for each outlet: ownership, independence, coverage (from a headline sample), Digital News Report reach and trust looked up for the outlet's country, and our feed status. Expected size: roughly 250 to 350 entries.

### Harvesting the pool

- **Seeds** (sources that name publishers and so add entries): Digital News Report brand lists, Wikipedia's reliable-sources list, Press Gazette's top 50s, and Tranco's top 10k domains filtered to news outlets. Tranco has no categories, so each of those domains has to be classified as a news outlet or not.
- **Enrichment only** (looked up for entries already in the pool, but doesn't add them): Chrome UX Report, Wikipedia citation counts, GDELT and Media Cloud inbound links. Wikipedia citations come from one download of the `externallinks` dump rather than the API: big outlets have hundreds of thousands of links, and paging through those 500 at a time would take thousands of requests per domain.
- **Gap filling:** Media Cloud's country collections are too large to use as a seed (thousands of US local outlets). They are used only for the regional searches below.
- **Similarweb:** taken only from what is published (Press Gazette, Similarweb's public top-site lists), never scraped. Many entries won't have a Similarweb figure, and the scoring already allows for missing signals.

### Working down the ranking

1. Sort the ranked entries by combined score (see "Scoring"). There are no quotas, and regions don't get separate rankings. Percentiles are relative to the pool, so re-score and re-sort whenever entries are added, including outlets added by gap filling, and record the date of the ranking.
2. Fully enrich entries in rank order, counting them by **location region**, until every group has reached its minimum:

   | Group | Minimum |
   |---|---|
   | UK | 20 |
   | US and Canada | 20 |
   | Europe, excluding the UK | 15 |
   | Asia-Pacific (asia, oceania) | 15 |
   | Middle East and Africa | 10 |
   | Latin America and Caribbean | 5 |

   The major wire services (AP, Reuters, AFP) and international broadcasters (for example BBC World Service and Al Jazeera English) have no separate target. They count toward their location region, and the authority signal should rank them highly. If any of them don't rank near the top, check whether the scoring is wrong.
3. **Regional gap filling.** If working down the ranking would mean enriching far down a list dominated by other regions just to reach one group's minimum, go straight to a targeted search for that group: its Digital News Report country pages, Media Cloud collections and national press lists. Add the best candidates to the pool, fully enrich them, and mark them `added_by: regional-quota` so the ranking can be read honestly. This is expected for Latin America and Caribbean, and possibly for Europe excluding the UK, because there are few significant English-language outlets there.
4. **Check-in, not a hard stop.** If full enrichment passes about **400 entries** with any group still below its minimum, stop and report the gaps to the owner before going further.

Each entry records which level it reached in `detail: pool | full`.

### Ranked entries

Every entry gets scores, but the ranking, the regional counts and shortlists such as the "top 100" use only **ranked entries**. These are entries that are not `kind: aggregator` and whose `english_edition` is not `none`. The other entries stay in the catalogue with all their data.

## Fields for each entry

```yaml
- id: japan-times                 # lowercase slug, matches config/sources.yaml ids where one exists
  name: The Japan Times
  domains: [japantimes.co.jp]
  kind: newspaper                 # newspaper | broadcaster | wire | digital-native | magazine | aggregator (incl. content farms) | other
  ownership:                      # who legally owns and funds it (fact); see "Ownership and independence"
    type: private                 # private | public-service | state-owned | nonprofit | cooperative | unknown
    owner: "News2u Holdings"      # named owner, parent or chain, as far as known
    source: https://...           # source key (e.g. mom-2026) or a URL
  independence:                   # who controls what it publishes (judgement)
    rating: independent           # independent | contested | captured | state-directed | unknown
    controlled_by: null           # who exerts the control, if rating is captured or state-directed
    basis: editorial-judgement    # state-media-monitor | mom | rsp | editorial-judgement
    note: "Not covered by State Media Monitor or Media Ownership Monitor"
  country_press_freedom: {value: null, source: rsf-2026}   # RSF index score for location.country (context only)
  languages: [en]
  english_edition: full           # full (English is the main language) | partial (significant English edition) | none
  location:                       # where the newsroom is based (objective)
    country: JP                   # ISO 3166-1 alpha-2; the only geographic value researched by hand
    region: asia                  # derived from country via the region table; never set by hand
    subregion: Eastern Asia       # derived: UN M49 subregion
    locality: Tokyo               # optional: city or county, for local outlets
  coverage:                       # what it reports on (a judgement; see below)
    scope: international          # local | regional | national | international | global
    focus: [JP, asia]             # ISO country codes (upper case) or region tags (lower case), most important first
    basis: feed-sample            # feed-sample | sections | editorial-judgement
    note: "Japan-centred, with a world desk"
  topics: [general]               # general | business | tech | science | politics | sport | culture …
  signals:                        # raw values copied from the sources; one key per measure, each with its source key
    dnr_reach_online:             # one row per surveyed country the brand appears in
      - {country: JP, value: null, source: dnr-2026}
    dnr_reach_offline: []         # TV, radio and print reach, same shape
    dnr_trust:
      - {country: JP, value: null, source: dnr-2026}
    rsp: {value: null, source: rsp-2026-11}       # the rating for its general news reporting; see "Scoring"
    rsp_topics: []                # topic-specific ratings, e.g. {topic: "politics and science", value: generally-unreliable}
    wiki_cites: {value: null, source: wiki-cites-2026-11}
    tranco_rank: {value: null, source: tranco-2026-11-01}
    crux_band: {value: null, source: crux-2026-10}
    similarweb_visits: {value: null, source: similarweb-2026-10}
    press_gazette_rank: {value: null, source: press-gazette-2026-10}
    gdelt_inbound: {value: null, source: gdelt-2026-11}
    mediacloud_inbound: {value: null, source: mediacloud-2026-11}
  scores:                         # derived from the signals; see "Scoring"
    reach: null
    trust: null
    authority: null
    combined: null
    signals_available: 0
  flags: []                       # see "Flags"
  detail: full                    # pool (bulk signals only) | full (also researched fields); see "Two levels of detail"
  added_by: pool                  # pool | regional-quota | manual
  our_status:                     # see "Checking our coverage"
    status: untested              # in-use | available | no-feed | blocked | untested
    feed_url: null
    robots_allows_feed: null
    robots_allows_articles: null
    checked: null                 # date
    note: ""
```

## Flags

Flags are short labels for facts that don't have their own field. Add new ones here before using them.

| Flag | Meaning |
|---|---|
| `exile` | The newsroom works outside the country it mainly covers (see "Outlets in exile") |
| `rsp-deprecated` | Wikipedia rates it deprecated or blacklisted (the rating itself is in `signals.rsp`) |
| `paywall-hard` | Almost all articles are behind a subscription |
| `feed-stale` | Its feed exists but hasn't been updated for weeks |
| `defunct` | It has stopped publishing |
| `merged` | It has merged into another entry, which `note` names |

Whether an outlet is an aggregator is recorded in `kind`, and state control is recorded in `independence`, so neither has a flag.

## Geographic tags

**Location** is where the publisher is based. It is a fact: the country (ISO code) and, for local outlets, a locality. Region and subregion are derived from the country (see "Regions come from countries").

**Coverage** is what the publisher reports on. It is a judgement and can have several values:

- `scope` is how wide the coverage reaches:
  - `local`: a city or county, e.g. Cambridge News (cambridge-news.co.uk) covers Cambridgeshire.
  - `regional`: part of a country, e.g. a Scottish or Californian paper.
  - `national`: one country, with little foreign news.
  - `international`: centred on its home country but with real foreign coverage, e.g. The Japan Times, or The New York Times.
  - `global`: no strong home bias, e.g. Reuters, AP or BBC World Service.
- `focus` lists the countries or regions the publisher concentrates on, most important first. The New York Times would be `scope: international, focus: [US]`, and The Japan Times would be `scope: international, focus: [JP, asia]`.
- `basis` records how the judgement was made. The preferred method is `feed-sample`: take about 30 recent headlines from the outlet's own feed and count where each story happens. Only the counts are kept, never the headlines. This uses the same feed fetch as "Checking our coverage", so each feed is fetched once. If there's no usable feed, use `sections` (the structure of the site's navigation), or else `editorial-judgement`, with a note.

**Outlets in exile:** `location` is where the newsroom actually works, not the country it reports on. Meduza (working from Riga) is `location.country: LV` with `coverage.focus: [RU]` and the flag `exile`. Exiled Iranian, Belarusian and Afghan outlets are handled the same way, so all outlets focused on a country can still be found through `focus`.

### Regions come from countries

Entries record only the country. `region` and `subregion` are derived from a single table that maps each country to a region, so redrawing a boundary later means editing one row and re-deriving the regions, with no re-research. The table lives in the catalogue file (`regions:`), and each country appears in it exactly once.

**Region vocabulary** (used for `location.region` and in `focus`): `uk`, `europe`, `north-america`, `latin-america-caribbean`, `middle-east`, `africa`, `asia`, `oceania`. The UK is its own region, as in the reader ideas, so `europe` means Europe excluding the UK. These are the same tags a regional filter in the client would use.

The table starts from the UN M49 standard country groupings, which also give the `subregion`, with these exceptions and clarifications:

| Country or area | Region | Why |
|---|---|---|
| UK | `uk` | Its own region (M49: Northern Europe) |
| US, Canada (and Bermuda, Greenland, Saint Pierre and Miquelon) | `north-america` | M49: Northern America |
| Mexico, Central and South America, the Caribbean | `latin-america-caribbean` | M49's "Latin America and the Caribbean" grouping |
| Gulf states, Iraq, Iran, Israel, Palestine, Jordan, Lebanon, Syria, Yemen | `middle-east` | The usual meaning in news. Iran is an exception (M49: Southern Asia) |
| Egypt | `middle-east` | Its news is part of the Arab world (M49: Northern Africa). The Middle East and Africa share one minimum, so this matters only for filtering |
| Turkey | `middle-east` | A judgement call: its English-language outlets focus heavily on the region (M49: Western Asia) |
| Russia | `europe` | M49: Eastern Europe. Russian coverage shows in `coverage.focus` |
| Armenia, Azerbaijan, Georgia, Cyprus | `europe` | M49: Western Asia |
| Central Asia (Kazakhstan, Kyrgyzstan, Tajikistan, Turkmenistan, Uzbekistan) | `asia` | M49: Central Asia |
| Hong Kong, Taiwan, Palestine | their own ISO codes (`HK`, `TW`, `PS`) | Keeps the catalogue neutral without taking a position |
| Other African countries | `africa` | M49: Africa |
| Other Asian countries | `asia` | M49: Asia |
| Australia, New Zealand, Pacific islands | `oceania` | M49: Oceania |

## Ownership and independence

These are two fields because they answer different questions, and they often disagree:

- `ownership` is **who legally owns and funds the outlet**. It is a fact, with the owner named where known.
- `independence` is **who controls what it publishes**. It is a judgement:
  - `independent`: no owner or government directs coverage.
  - `contested`: credible sources disagree.
  - `captured`: in practice, coverage follows an owner aligned with the government or with a political or business interest.
  - `state-directed`: the state sets the editorial line.

Examples: the BBC is public-service and independent. A privately owned paper bought by government allies is private and captured. RT is state-owned and state-directed. Al Jazeera is state-owned and contested, with a note explaining why.

Evidence comes from these sources, in this order: State Media Monitor's classification, which is mapped onto our ratings and the mapping recorded in a note; Media Ownership Monitor's ownership records; the editors' discussions behind Wikipedia's reliability ratings; and, where none of these covers the outlet, editorial judgement with a note. `country_press_freedom` (the RSF World Press Freedom Index score for the outlet's base country) is background for reading the independence rating.

**None of these fields affects the scores.** Public-service, state-owned and captured outlets are ranked on reach, trust and authority like any other outlet. The fields are there so the catalogue can be read and filtered with this in mind.

## Scoring

- **Reach** measures absolute audience, so large and small countries are compared on the same footing. The regional minimums are what protect small countries.
  - Digital News Report percentages are turned into an estimated audience: the share of the country's online population, multiplied by that population (`online-pop`), and added up across the countries where the brand is surveyed. The raw percentages are kept.
  - Each reach measure (estimated audience, Similarweb visits, Tranco rank, Chrome UX Report band) is converted to a percentile across the pool, on a log scale so the biggest outlets don't flatten everyone else. The reach score is the average of the percentiles the entry has.
- **Trust:** the Digital News Report brand trust score where there is one, combined with the Wikipedia rating (generally reliable 1.0, no consensus 0.5, generally unreliable 0.1, deprecated or blacklisted 0).
  - The Wikipedia rating used is the one for the outlet's **general news reporting**. Ratings that Wikipedia gives for one topic only (for example, a paper that is generally reliable but rated "no consensus" on one subject) are kept in `rsp_topics` and shown in the catalogue, but don't change the score.
  - Where Wikipedia splits the outlet's general news itself (for example, Fox News, which has separate ratings for politics and science and for its other news), use the rating that covers most of its news output, and explain the choice in a note.
- **Authority:** the percentile of Wikipedia citation counts, combined with GDELT or Media Cloud inbound citations where available.
- **Combined:** 40% reach, 30% trust and 30% authority.
  - **Missing signals:** for ranking only, a missing reach, trust or authority score is filled with the pool's median for that score, so being unmeasured neither helps nor hurts. The catalogue still stores the score as unknown, and `signals_available` records how many were real. An entry scored on one real signal is less certain than one scored on three, and shortlists should show that.
- **Reach for multi-language outlets** usually measures the whole brand rather than its English edition. Record that in a note, and don't adjust the number.

The weights are a starting point. Changing them re-sorts the catalogue but never removes an entry.

## Checking our coverage

For each fully enriched entry, record whether the feed could use it:

- `in-use`: the publisher is in `config/sources.yaml`.
- `available`: checked; it has a working feed, and robots.txt allows both the feed and its article URLs.
- `no-feed`: checked; robots.txt allows us, but no working RSS or Atom feed was found.
- `blocked`: listed in `docs/blocked-publishers.md`, or refused when checked (robots.txt, HTTP 401, 402 or 403).
- `untested`: not yet checked. All pool-only entries stay here.

Checks use the project's `HttpFetcher` only, so they respect robots.txt, per-host rate limits and our User-Agent. Each publisher gets at most a robots.txt check, one feed fetch and a robots.txt test of one article URL from the feed. No article pages are downloaded or stored. New refusals are recorded here, in the catalogue. Updating the committed `docs/blocked-publishers.md` is a separate job, done only if the owner asks for it.

## Data and attribution

- This is personal research and the catalogue stays off git, so figures may be copied from the sources above (including Similarweb and Press Gazette) without licence restrictions, as long as each value carries its source key and the Sources table attributes it. If the catalogue is ever committed, check this again first.
- Every value records which dataset it came from, and the Sources table records each dataset's edition and retrieval date, so we know how old a value is. Copied signal values aren't edited by hand after retrieval. A refresh adds a new source key with a new date. Judgement fields (coverage, independence) are written by hand, and each records its basis.
- Only facts about publishers are stored. No article text and no full feed dumps.

## Where the results go

- `docs/research/news-source-catalogue.md` (in the repo): this protocol and its change log.
- `~/.newsfeed/research/news-sources/` (outside the repo, never committed), shared by the main checkout and every worktree:
  - `catalogue.yaml`: the merged catalogue, including the `regions:` table.
  - `sources.md`: the Sources table (each dataset's key, publisher, edition, retrieval date and URL).
  - `raw/`: downloaded datasets (Tranco list, CrUX list, counts from the `externallinks` dump, harvested Digital News Report tables), kept so that scores can be recomputed without downloading them again.
  - `shards/`: work in progress from individual agents (see "Splitting the work").

## Splitting the work

The catalogue lives outside the repo, so research agents don't need git, branches or worktrees. They read this protocol and write to the shared folder. To keep parallel agents from overwriting each other:

- **Each agent writes only its own shard**: `shards/<task>.yaml`, for example `shards/harvest-dnr.yaml` or `shards/enrich-batch-07.yaml`. Only one step, the merge, writes `catalogue.yaml`.
- **Harvest first, in parallel by source:** one agent per seed source (Digital News Report, Wikipedia's list, Press Gazette, Tranco classification). The bulk lookups (Tranco, CrUX, Wikipedia citations) are one job, because they are scripted joins.
- **Merge, remove duplicates and score:** one step, which assigns ids, applies the brand rule, derives regions and ranks entries.
- **Full enrichment, in parallel by batch:** batches of about 20 to 30 entries from the ranking, assigned so that no two agents have the same outlet. Each batch does ownership, independence, coverage and the feed check for its entries.
- **Fetch checks stay polite under parallel work:** every outlet belongs to exactly one batch, so no host is checked by two agents at once, and each agent's `HttpFetcher` applies the per-host rate limit.
- **Re-merge after each round,** re-rank, and count the regions to decide whether another round or a regional search is needed.

## Known biases

- **Wikipedia appears twice.** Its reliability rating feeds trust, and its citation counts feed authority. English Wikipedia leans toward English-language and Western outlets, so that bias counts twice. This is accepted for now, and the regional minimums partly offset it.
- **Survey coverage is uneven.** The Digital News Report surveys about 47 markets, so outlets elsewhere rely on web-traffic signals alone.
- **Traffic isn't readership.** Tranco and Chrome UX Report measure visits from browsers, including bots and casual clicks, not readers. They are the fallback, not the main measure.

## Change log

| Date | Change |
|---|---|
| 2026-11-08 | Protocol agreed; research not started |
| 2026-11-08 | Separate `ownership` and `independence` fields, plus country press freedom; recorded only, never scored (owner's decision) |
| 2026-11-08 | 400-entry limit replaced by two levels of detail: an uncapped pool with bulk signals, and full enrichment down the ranking until regional minimums are met, with a check-in at about 400. Region renamed "Latin America and Caribbean" (owner's decision) |
| 2026-11-08 | Regions derived from country through one table based on UN M49, with named exceptions (Middle East, Egypt, Turkey, Russia, Caucasus); M49 subregion recorded; `exile` flag for outlets working outside the country they cover (owner's decision) |
| 2026-11-08 | Kept off git (local exclude). Consistency review: brand-vs-domain rule, "ranked entries" defined, Flags table, fuller signals block, coverage statuses `available` and `no-feed`, CrUX from published lists (no BigQuery), Wikipedia citations from the dump, blocked-publishers.md not updated by this work, known biases |
| 2026-11-08 | Reach as estimated absolute audience, averaged across per-measure percentiles; missing scores filled with the pool median for ranking only; specialist news publishers in scope, trade press out; "significant English edition" defined; Wikipedia rating taken from general news reporting, topic ratings recorded but not scored (owner's decisions) |
| 2026-11-08 | Protocol committed to the repo; catalogue, Sources table and raw data moved to `~/.newsfeed/research/news-sources/`, outside git; work split into per-agent shards with a single merge step |
