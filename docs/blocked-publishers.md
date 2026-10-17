# Blocked publishers

Publishers we haven't added to `config/sources.yaml` because they refuse our fetcher: robots.txt disallows our User-Agent, or their pages return HTTP 403 or 402. Our fetching rules (CLAUDE.md, architecture rule 6) mean we respect these refusals and never work around them, so these sites can't be in the feed. Listing them would only add fetch errors to every run.

This is a coverage limit of the project, not an extraction failure. It feeds into the Phase 1 go/no-go review (#21).

## How the checks were made

- **Fetcher:** the project's `HttpFetcher`, which checks robots.txt, rate-limits each host and sends `newsfeed-personal/0.1 (+https://github.com/4OH4/news-bot)`.
- **Network:** a residential connection in the UK. From GCP in Phase 2, results may be worse, because some publishers block datacenter IP ranges (PROJECT_PLAN.md §11).
- **Method:** fetch the publisher's RSS/Atom feed, then fetch two or three article pages linked from it.
- **First found:** 2026-10-17, while curating sources for #16 (PR #61).
- **Last confirmed:** 2026-10-17, re-checked with the same fetcher.

## Blocked

| Publisher | Feed tested | Feed | Article pages | Mechanism |
|---|---|---|---|---|
| The Register | `theregister.com/headlines.atom` | robots.txt disallows | robots.txt disallows | robots.txt allows only named search engines and AI agents |
| New Scientist | `newscientist.com/feed/home/` | robots.txt disallows | allowed by robots.txt (untested) | Feed path disallowed for unnamed agents, so we can't discover articles |
| Rest of World | `restofworld.org/feed/latest` | HTTP 403 | not reached | 403 on the feed |
| Sky News | `feeds.skynews.com/feeds/rss/home.xml` | OK | HTTP 403 | Feed served, articles refused |
| The Atlantic | `theatlantic.com/feed/all/` | OK | HTTP 403 | Feed served, articles refused |
| The Economist | `economist.com/latest/rss.xml` | OK | HTTP 403 | Feed served, articles refused |
| Financial Times | `ft.com/rss/home` | OK | HTTP 403 | Feed served, articles refused (also hard paywall) |
| New York Times | `rss.nytimes.com/.../World.xml` | OK | HTTP 403 | Feed served, articles refused |
| Bloomberg | `feeds.bloomberg.com/markets/news.rss` | OK | HTTP 403 | Feed served, articles refused |
| The Information | `theinformation.com/feed` | OK | HTTP 403 | Feed served, articles refused (also subscription-only) |
| Medium-hosted blogs | `netflixtechblog.com/feed` | OK | HTTP 403 | Medium refuses article pages. Applies to any blog on Medium, custom domain or not |
| The Telegraph | `telegraph.co.uk/rss.xml` | OK | HTTP 402 | 402 Payment Required on every article tried, live blogs included |
| Wall Street Journal | `feeds.a.dj.com/rss/RSSWorldNews.xml` | OK | robots.txt disallows | `wsj.com` robots.txt has `Disallow: /` for unnamed agents |

A feed that is served without its articles still gives us headlines and summaries. If you want them in the feed as "headline + link" items, that's a ranking/publish decision for later, not something to add now.

## Recoverable: needs a decision

**Al Jazeera** (`aljazeera.com/xml/rss/all.xml`). #61 recorded it as blocked by robots.txt, but the re-check shows the block is narrower:
- robots.txt disallows URLs carrying the `traffic_source` parameter, and every link in the feed ends `?traffic_source=rss`.
- The same article URLs without that parameter are allowed and return HTTP 200.
- A rule in `config/url_params.yaml` (`aljazeera.com: strip: [traffic_source]`) would canonicalise the links before fetching and make the source work.
- However, the robots.txt header says the content is for "personal, non-commercial use" and that automated scraping needs prior written permission. This project is personal and non-commercial, but it fetches automatically.

Recommendation: leave it out unless you decide the stated terms allow this use.

## Not added for other reasons

These aren't refusals, but they were dropped during #61 for technical reasons:

| Publisher | Reason |
|---|---|
| Dan Luu (`danluu.com`) | Feed is larger than the fetcher's 5 MB body cap (`DEFAULT_MAX_BODY_BYTES`) |
| CNN | World feed is stale (items from 2022–23), and article pages exceed the 5 MB cap |
| Washington Post | HTTP 502 on the feed when probed (2026-10-17); may be transient |
| The Spectator | Feed URL returned 404; the right feed URL wasn't found |

## Updating this list

When a source starts or stops refusing us, update its row and the "Last confirmed" date. To re-check a URL with the project's fetcher:

```python
from newsfeed.fetching.http_fetcher import DEFAULT_USER_AGENT, HttpFetcher

f = HttpFetcher(user_agent=DEFAULT_USER_AGENT)
url = "https://..."
print("robots.txt allows" if f.is_allowed(url) else "robots.txt disallows")
if f.is_allowed(url):
    print(f.fetch(url).status_code)
```

Run it from `backend/` with `uv run python`. Fetch only a handful of pages per publisher.
