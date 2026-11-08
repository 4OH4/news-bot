# News source catalogue: research protocol

**Status:** pool harvested and ranked; full enrichment not started. **Written:** 2026-11-08. **Updated:** 2026-11-08 with the lessons from the harvest (see the change log).

**Where things live.** This protocol is committed to the repo. The catalogue and the raw data it is built from are kept **outside** the repo, in `~/.newsfeed/research/news-sources/` (see "Where the results go"). They are never committed, the same arrangement as the evaluation snapshots in `~/.newsfeed/eval-fixtures`.

## Purpose

The goal is to build a master list of English-language news publishers, so we can see how much of the news landscape the feed covers. For each publisher, the list shows how important it is, where it is based, what it reports on, and whether we use it, can't use it or haven't tried it yet. It is a research document, not pipeline configuration: `config/sources.yaml` remains the list of sources the pipeline actually fetches, and nothing is added there because it appears here.

"Important" doesn't have one definition. We approximate it with three separate signals: **reach** (how many people read it), **trust** (whether readers and editors rely on it) and **authority** (whether other outlets and Wikipedia build on its reporting). Each signal stays visible in the catalogue, and the combined score is just a default ordering that can be reweighted.

Publishers come first. Blogs, newsletters and aggregators may get their own catalogue later.

## Scope

- **Publishers that do original news reporting:** newspapers, broadcasters, wire services, digital-native newsrooms and news magazines. Aggregators and content farms can enter the candidate pool, but they are recorded as `kind: aggregator` and left out of the ranking (see "Ranked entries").
- **One entry per editorial brand, not per domain.** BBC News is one entry covering `bbc.co.uk` and `bbc.com`. A brand that shares a domain with others (BBC Sport, BBC World Service, a Guardian section) isn't split out. Each masthead in a chain (for example the local papers owned by Reach plc) is its own entry, with the chain in `ownership.owner`. Brands that the Digital News Report counts as TV or radio only are linked to their online entry where one exists, and their offline reach is kept as a separate signal. The Digital News Report often lists several channels of one broadcaster; channels that share the broadcaster's domain are part of its entry, and each channel's offline reach is kept as its own row.
  - **Signals measured per domain** are combined across an entry's domains as the measure allows: counts (Wikipedia links) are summed over every host under the entry's domains; ranks (Tranco, Chrome UX Report) can't be added, so the best rank is used. The method is noted on each value.
  - **Infrastructure domains** (CDN, image and DNS hosts such as `bbci.co.uk` or `nyt.com`) go in the brand's `domains`, so bulk lookups find them.
  - **Shared domains** host several unrelated brands or are shared infrastructure (`google.com`, `yahoo.com`, `go.com`, `indiatimes.com`, `sky.com`, `substack.com` and similar). They never join two records into one brand. A value measured on one is credited to every entry that lists it, with a note, and Wikipedia links under a shared domain count only through a more specific host (`news.sky.com`).
  - **Separately branded English sister titles** with their own newsroom (Hürriyet Daily News beside Hürriyet) are their own entries. An English section or translated edition of the same title (Folha in English) belongs to the parent entry.
- **Specialist news publishers count** if they are significant news outlets in their own right, such as the FT, Bloomberg, Politico or Nature News. `topics` records the subject. Trade press (industry newsletters and trade magazines) is out of scope; the major entertainment trade titles (Variety, Deadline, The Hollywood Reporter) are treated as specialist news.
- **Topic groups.** Each entry gets a `topic_group` derived from `topics`: `news` (general, politics, world, local), `specialist` (tech, science, business, health, climate and other news specialisms) or `soft` (entertainment, celebrity, gaming, sport, lifestyle, culture, music, travel, food). An entry is `soft` only if every topic it has is soft. Soft outlets such as People, IGN or ESPN are kept and scored like any other, but ranked separately (see "Ranked entries"), because their large audiences would otherwise fill the top of a list meant for news (owner's decision).
- **English language.** A publisher that is mainly in another language is included if it has a significant English edition: its own English section or feed with new articles every day. Examples are DW, France 24, NHK World, Le Monde in English, El País English edition or Al Jazeera English. A section of translated articles counts if it is updated daily; weekday-only updating counts too. The `languages` and `english_edition` fields record how much is in English. A publisher with no English edition is out of scope for now. If it appears in a candidate source anyway, it is still recorded with `english_edition: none`, so no data is lost. Because a wrong `none` drops an outlet from the ranking without trace, the strongest `none` entries (by combined score) get a light check before enrichment.
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
| `wiki-agency` | English Wikipedia search API: articles crediting a wire in a citation's `agency=` field | How often Wikipedia relies on a wire's copy, which mostly reaches it through client sites rather than the wire's own domain |
| `similarweb` | Similarweb public rankings and figures, including those Press Gazette republishes | Monthly visits and country rank |
| `press-gazette` | Press Gazette top-50 news websites (monthly): UK, US and global English-language lists | Ranked audiences, with month-to-month changes. The UK list is Ipsos iris (`ipsos-iris`); the US and global lists are Similarweb visits (US-only traffic in the US list, worldwide in the global list) |
| `ipsos-iris` | Ipsos iris, the UK industry audience measurement, as republished by Press Gazette | Unduplicated UK monthly audience across a brand's sites and apps |
| `mediacloud` | Media Cloud source collections by country | Lists of national and local outlets per country, and inbound link counts. The API needs an account key, so it isn't used yet |
| `gdelt` | GDELT Global Knowledge Graph, 12 months, queried on BigQuery (public dataset `gdelt-bq.gdeltv2.gkg_partitioned`) | How many distinct outlets link to each domain from article bodies, how many name each organisation, and how many articles each domain publishes |
| `state-media-monitor` | State Media Monitor (Media and Journalism Research Center) | Classification of state-funded and state-controlled media by editorial independence |
| `mom` | Media Ownership Monitor (RSF and partners) | Who owns which outlets, country by country |
| `rsf` | RSF World Press Freedom Index (latest year) | Press freedom score per country |
| `online-pop` | World Bank / ITU: internet users by country (latest year) | Online population per country, to turn Digital News Report percentages into estimated audiences. Taiwan isn't covered |
| `m49` | UN Statistics Division: standard country or area codes (M49) | The country table the regions are derived from. Taiwan and Kosovo aren't in it and are added by hand |
| `pew` / `ofcom` | Pew Research (US) and Ofcom News Consumption survey (UK) | Reach by brand from national surveys |

Other sources (national press councils, audit bureaus such as ABC in the UK, prize records) can be added if they fill a gap. Each one gets a row in the Sources table.

## Two levels of detail

The catalogue holds two kinds of data, and they cost very different amounts to collect, so they are collected to different depths.

- **The pool: every candidate, with no cap.** Each entry gets the data that can be looked up in bulk by domain: the Wikipedia rating, Tranco and Chrome UX Report ranks, Wikipedia citation counts, GDELT counts, any reach figure already in a harvested table (Digital News Report, Press Gazette, published Similarweb lists), and a combined score and rank. This costs almost nothing per entry, so nothing is left out to save effort. The first harvest gave about 1,650 entries, of which about 580 are ranked: the seeds bring in many brands with no English edition, which stay in the pool unranked.
- **Full enrichment: the ranked top, plus outlets added to fill regional gaps and the wire batch.** These entries also get the fields that need research or judgement for each outlet: ownership, independence, coverage (from a headline sample), Digital News Report reach and trust looked up for the outlet's country, and our feed status. Expected size: roughly 230 to 350 entries.

### Harvesting the pool

- **Seeds** (sources that name publishers and so add entries): Digital News Report brand lists, Wikipedia's reliable-sources list, Press Gazette's top 50s, and Tranco's top 10k domains filtered to news outlets. Tranco has no categories, so each of those domains has to be classified as a news outlet or not.
- **Enrichment only** (looked up for entries already in the pool, but doesn't add them): Chrome UX Report, Wikipedia citation counts, Wikipedia agency credits, GDELT and Media Cloud inbound links. Wikipedia citations come from one download of the `externallinks` dump rather than the API: big outlets have hundreds of thousands of links, and paging through those 500 at a time would take thousands of requests per domain. The dump is about 5 GB; links are counted per host in one streaming pass and only the counts are kept.
- **Wikipedia agency credits** are collected for `kind: wire` entries only, one search per name variant (`insource:"agency=AFP"`), combined by inclusion and exclusion over AND queries so an article using two spellings counts once. The search's OR between `insource:` phrases returns fewer hits than either phrase alone, and its regex search times out with partial results, so neither is used.
- **GDELT** comes from three queries over the last 12 months of the Global Knowledge Graph on BigQuery, reading only the columns they need (about 3% of the table): distinct source domains linking to each domain and host from article bodies (`PAGE_LINKS`, self-links excluded), distinct source domains naming each organisation, and articles per source domain. The owner runs them in a BigQuery sandbox project with no billing account, so nothing can be charged; each query is dry-run first and has a cap on bytes billed. Agents don't hold the credentials.
- **Gap filling:** Media Cloud's country collections are too large to use as a seed (thousands of US local outlets). They are used only for the regional searches below.
- **Similarweb and Ipsos iris:** taken only from what is published (Press Gazette, Similarweb's public top-site lists), never scraped. Both are recorded but not scored in the overall ranking (see "Scoring"); they are kept for a later look at UK sources.

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

   International broadcasters (for example BBC World Service and Al Jazeera English) have no separate target. They count toward their location region and rank near the top on their own signals.
3. **Regional gap filling.** If working down the ranking would mean enriching far down a list dominated by other regions just to reach one group's minimum, go straight to a targeted search for that group: its Digital News Report country pages, Media Cloud collections and national press lists. Add the best candidates to the pool, fully enrich them, and mark them `added_by: regional-quota` so the ranking can be read honestly. The group's minimum is then met by a regional batch rather than by working down to it. This was needed for Latin America and Caribbean, where the pool held only 2 ranked outlets; the search added 15, mostly Caribbean English-language papers.
4. **Wire batch.** Wire services don't get a target either, but the ranking can't place most of them. Their copy reaches readers through client sites, so they have little consumer traffic and score low on reach whatever their authority. AP and Reuters rank near the top; AFP and Xinhua fall inside the ranked batches; the other major wires (UPI, The Canadian Press, PTI, AAP, PA Media, EFE, Kyodo, dpa) are enriched as one extra batch, whatever their rank. The list is editorial and lives in the batch plan. Treating reach as unmeasured for wires was tried and rejected: it drops AP and Reuters to around rank 65 and still leaves the smaller wires near 300.
5. **Check-in, not a hard stop.** If full enrichment passes about **400 entries** with any group still below its minimum, stop and report the gaps to the owner before going further.

The batch plan (`batches.yaml`) is generated from the ranking by a script, so it follows any re-rank: the ranked batches run down to the rank where the last ranked group reaches its minimum, then the regional batch, then the wire batch. Every outlet is in exactly one batch.

Each entry records which level it reached in `detail: pool | full`.

### Ranked entries

Every entry gets scores, but the ranking, the regional counts and shortlists such as the "top 100" use only **ranked entries**. These are entries that are not `kind: aggregator`, whose `english_edition` is not `none`, and that aren't one of the Digital News Report's generic categories ("other regional newspapers", "commercial radio news"). Those categories aren't publishers: they are kept in the raw Digital News Report table and don't become entries. The other entries stay in the catalogue with all their data.

Ranked entries in `topic_group: soft` get their own ranking, `soft_rank`. The main ranking (`rank`), the regional minimums and the batch plan use `news` and `specialist` entries only. Both rankings share the same scores and percentiles.

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
  topic_group: news               # derived from topics: news | specialist | soft; see "Scope"
  aliases: []                     # other names the sources use for the brand
  harvested_from: [dnr, rsp]      # which seeds named it
  signals:                        # raw values copied from the sources; one key per measure, each with its source key
    dnr_reach_online:             # one row per surveyed country the brand appears in
      - {country: JP, value: null, source: dnr-2026}    # split markets add segment: "French-speaking"
    dnr_reach_offline: []         # TV, radio and print reach, same shape
    dnr_trust:
      - {country: JP, value: null, source: dnr-2026, scale: "% trust among those who have heard of the brand"}
    dnr_audience_est: {value: null, source: online-pop-wb-2026-11}   # derived: see "Scoring"
    rsp: {value: null, source: rsp-2026-11}       # the rating for its general news reporting; see "Scoring"
    rsp_topics: []                # topic- or period-specific ratings, e.g. {topic: "politics and science", value: generally-unreliable}
    wiki_cites: {value: null, source: wiki-cites-2026-11-01}
    wiki_agency: {value: null, link_equivalent: null, source: wiki-agency-2026-11-08}   # wires only
    tranco_rank: {value: null, source: tranco-2026-11-07, domain: null}
    crux_band: {value: null, source: crux-2026-09, origin: null}
    similarweb_visits:            # one row per Press Gazette market; recorded, not scored
      - {value: null, source: similarweb-2026-09, via: press-gazette, market: US}
    ipsos_iris_audience:          # UK only; recorded, not scored
      - {value: null, source: ipsos-iris-2026-09, via: press-gazette, market: UK}
    press_gazette_rank:           # one row per market: UK | US | global-en
      - {value: null, source: press-gazette-2026-09, market: UK}
    gdelt_inbound: {value: null, links: null, source: gdelt-2025-11-01_2026-10-31}   # distinct linking source domains
    gdelt_mentions: {value: null, org: null, source: gdelt-2025-11-01_2026-10-31}    # wires only: distinct domains naming it
    gdelt_volume: {value: null, source: gdelt-2025-11-01_2026-10-31}                 # articles in the window; not scored
    mediacloud_inbound: {value: null, source: null}
  scores:                         # derived from the signals; see "Scoring"
    reach: null
    trust: null
    trust_editors: null           # from the Wikipedia rating
    trust_audience: null          # from Digital News Report trust, as a within-market percentile
    authority: null
    combined: null
    signals_available: 0
  rank: null                      # main ranking (news and specialist); see "Ranked entries"
  soft_rank: null                 # separate ranking for topic_group: soft
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
| Hong Kong, Taiwan, Palestine | their own ISO codes (`HK`, `TW`, `PS`) | Keeps the catalogue neutral without taking a position. M49 has no Taiwan row, so it is added by hand (`asia`, Eastern Asia) |
| Kosovo | `XK` (`europe`, Southern Europe) | Not in M49; added by hand |
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

Every measure is turned into a **percentile**: the share of entries with that measure that it beats, from 0 to 1, with ties sharing their mid-rank. Percentiles are computed over every entry in the pool that has the measure, ranked or not. A percentile depends only on order, so no log transform is needed.

- **Reach** measures absolute audience, so large and small countries are compared on the same footing. The regional minimums are what protect small countries.
  - Digital News Report percentages are turned into an estimated audience (`dnr_audience_est`): the share of the country's online population, multiplied by that population (`online-pop`), and added up across the countries where the brand is surveyed. Where the survey splits a country into language markets (Belgium, Canada, Switzerland), each market's percentage is weighted by its approximate share of the population. The raw percentages are kept.
  - The reach measures are the estimated audience, Tranco rank and Chrome UX Report band. The reach score is the average of the percentiles the entry has.
  - **Similarweb visits and Ipsos iris audiences are recorded but not scored.** Both come only from top-50 lists, so a percentile among the listed entries puts the 50th, still a very large site, near 0 and pushes it down (with Similarweb scored, The Atlantic ranked 73rd; without it, 25th). Iris is also UK-only. Both are kept for a later look at UK sources.
- **Trust** has two parts, kept separately in `scores` and averaged where both exist:
  - `trust_editors`: the Wikipedia rating (generally reliable 1.0, no consensus 0.5, generally unreliable 0.1, deprecated or blacklisted 0).
  - `trust_audience`: the Digital News Report brand trust score ("% trust among those who have heard of the brand") as a percentile among the brands the report lists for the same country and language market, averaged across the markets the brand appears in. Raw levels can't be compared across countries: in polarised markets every brand scores low (in the US, the Washington Post 37% and the New York Times 48%).
  - The Wikipedia rating used is the one for the outlet's **general news reporting**. Ratings that Wikipedia gives for one topic only (for example, a paper that is generally reliable but rated "no consensus" on one subject) are kept in `rsp_topics` and shown in the catalogue, but don't change the score.
  - Where Wikipedia splits the outlet's general news itself (for example, Fox News, which has separate ratings for politics and science and for its other news), use the rating that covers most of its news output, and explain the choice in a note.
  - Where Wikipedia rates an outlet differently over time (CNET, Forbes, Newsweek), use the current period's rating and keep the others in `rsp_topics` with the period as the topic.
- **Authority:** the average of two percentiles, whichever exist:
  - Wikipedia citations. For wires, the larger of links to the wire's own domain and its agency credits converted to links: credited articles × the median links per citing article, measured on large news domains (about 2.5).
  - GDELT `gdelt_inbound`: distinct outlet domains linking to the entry, the largest across its domains. For wires, the number of distinct domains naming the wire is used when larger, but GDELT's organisation extraction rarely recognises wire names (it never records "AFP"), so this mainly helps AP, Reuters, Bloomberg and Xinhua.
- **Combined:** 40% reach, 30% trust and 30% authority.
  - **Missing signals:** for ranking only, a missing reach, trust or authority score is filled with the pool's median for that score, so being unmeasured neither helps nor hurts. The catalogue still stores the score as unknown, and `signals_available` records how many were real. An entry scored on one real signal is less certain than one scored on three, and shortlists should show that.
- **Reach for multi-language outlets** usually measures the whole brand rather than its English edition. Record that in a note, and don't adjust the number.

The weights are a starting point. Changing them re-sorts the catalogue but never removes an entry. The merge script takes the weights as options, and can also score Similarweb and Ipsos iris, or compute percentiles over ranked entries only, for comparison.

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
- `docs/research/news-source-catalogue/` (in the repo): the scripts, which read and write only the research folder below (set `NEWSFEED_RESEARCH_DIR` to use another):
  - `merge_score.py`: merges the shards, joins the bulk signals, scores and ranks, and writes `catalogue.yaml`, `ranked.csv` and `ranked-soft.csv`. Weights and the optional measures are options.
  - `plan_batches.py`: writes `batches.yaml` from the ranking.
  - `count_wiki_cites.sh`: counts links per host in the `externallinks` dump.
  - `wiki_agency.py`: collects Wikipedia agency credits for wires.
  - `gdelt_bq.sh` and `sql/`: the GDELT queries, which the owner runs in a BigQuery sandbox; the script dry-runs each query and asks before running.
- `~/.newsfeed/research/news-sources/` (outside the repo, never committed), shared by the main checkout and every worktree:
  - `catalogue.yaml`: the merged catalogue, including the `regions:` table.
  - `ranked.csv` and `ranked-soft.csv`: the main and soft rankings, one row per ranked entry with its scores.
  - `batches.yaml`: the full-enrichment batch plan, generated from the ranking.
  - `overrides.yaml`: hand corrections applied after every merge (merging duplicates, setting a field), so they survive re-runs. `catalogue.yaml` is never edited by hand.
  - `sources.md`: the Sources table (each dataset's key, publisher, edition, retrieval date and URL).
  - `raw/`: downloaded datasets (Tranco list, CrUX list, counts from the `externallinks` dump, harvested Digital News Report tables, GDELT query results, Wikipedia agency counts), kept so that scores can be recomputed without downloading them again.
  - `shards/`: work in progress from individual agents (see "Splitting the work").

## Splitting the work

The catalogue lives outside the repo, so research agents don't need git, branches or worktrees. They read this protocol and write to the shared folder. To keep parallel agents from overwriting each other:

- **Each agent writes only its own shard**: `shards/<task>.yaml`, for example `shards/harvest-dnr.yaml` or `shards/enrich-batch-07.yaml`. Only one step, the merge, writes `catalogue.yaml`. The merge reads entries from `harvest-*.yaml` (`added_by: pool`), `manual-*.yaml` (`added_by: manual`) and `regional-*.yaml` (`added_by: regional-quota`), and corrections from `override-*.yaml` (the same format as `overrides.yaml`), so a checking agent never edits another agent's file.
- **Harvest first, in parallel by source:** one agent per seed source (Digital News Report, Wikipedia's list, Press Gazette, Tranco classification). The bulk lookups (Tranco, CrUX, Wikipedia citations) are one job, because they are scripted joins.
- **Merge, remove duplicates and score:** one step, which assigns ids, applies the brand rule, derives regions and ranks entries.
- **Full enrichment, in parallel by batch:** batches of about 20 to 30 entries from the ranking, assigned so that no two agents have the same outlet. Each batch does ownership, independence, coverage and the feed check for its entries.
- **Fetch checks stay polite under parallel work:** every outlet belongs to exactly one batch, so no host is checked by two agents at once, and each agent's `HttpFetcher` applies the per-host rate limit.
- **Re-merge after each round,** re-rank, and count the regions to decide whether another round or a regional search is needed.

## Known biases

- **Wikipedia appears twice.** Its reliability rating feeds trust, and its citation counts feed authority. English Wikipedia leans toward English-language and Western outlets, so that bias counts twice. This is accepted for now, and the regional minimums partly offset it.
- **Survey coverage is uneven.** The Digital News Report surveys 48 markets (2026 edition), so outlets elsewhere rely on web-traffic signals alone.
- **Traffic isn't readership.** Tranco and Chrome UX Report measure visits from browsers, including bots and casual clicks, not readers. They are the fallback, not the main measure.
- **Reach can't see wire services.** Their copy is read on client sites, so they score low on reach however much they are relied on. The wire batch covers the major ones; smaller national agencies stay low in the ranking.
- **English editions borrow the whole brand's numbers.** An outlet whose English output is a translated section (Kompas, Folha, Il Sole 24 Ore) ranks on the reach and authority of the whole brand, so it ranks higher than its English output justifies. This is recorded, not adjusted.
- **GDELT sees what it collects.** Its inbound links come from its own source list and from the roughly half of articles where it extracts body links, and its organisation names miss most wire services.

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
| 2026-11-08 | Lessons from the harvest stage (owner's decisions). Similarweb and Ipsos iris recorded but not scored (top-50 lists distort percentiles), kept for a UK study; Press Gazette's UK list is Ipsos iris, and its ranks are per market. Trust split into `trust_editors` and `trust_audience` (Digital News Report trust as a within-market percentile). Topic groups with a separate `soft_rank` for entertainment, sport and lifestyle outlets. GDELT added via a BigQuery sandbox (12 months: inbound links, organisation mentions, volume). Wikipedia agency credits for wires; wire batch, since reach can't place wires. Per-domain rule written out (counts summed, best rank for ranks), with shared and infrastructure domains; English sister titles as separate entries; Digital News Report language markets and generic categories; Taiwan and Kosovo added to the region table; time-period Wikipedia ratings; percentiles over the whole pool, no log transform; `overrides.yaml` and the shard naming for manual, regional and override files; generated batch plan; scripts committed beside this protocol |
