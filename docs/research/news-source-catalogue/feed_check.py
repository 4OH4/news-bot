"""Feed check for news-source catalogue enrichment (protocol: "Checking our coverage").

Run from backend/ (it uses the project's HttpFetcher):
    uv run python ../docs/research/news-source-catalogue/feed_check.py <feed_url> [--headlines N]

Does at most: robots.txt check of the feed URL, ONE feed fetch, and a robots.txt test
(no download) of one article URL from the feed. Prints a JSON summary and the headlines
(title + link) to stdout so the coverage sample can be counted. Nothing is written to disk:
keep only the counts, never the headlines. Reads RSS 2.0, RSS 1.0 (RDF) and Atom.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from newsfeed.fetching.http_fetcher import DEFAULT_USER_AGENT, HttpFetcher

ATOM = "{http://www.w3.org/2005/Atom}"
RSS1 = "{http://purl.org/rss/1.0/}"
DC_DATE = "{http://purl.org/dc/elements/1.1/}date"


def _text(el: ET.Element, *tags: str) -> str:
    """The first non-empty child text among ``tags``, stripped."""
    for t in tags:
        v = (el.findtext(t) or "").strip()
        if v:
            return v
    return ""


def parse(body: bytes) -> list[dict]:
    """Items from an RSS 2.0, RSS 1.0 (RDF) or Atom feed: title, link and date strings."""
    root = ET.fromstring(body)
    items = []
    for it in root.iter("item"):
        items.append(
            {
                "title": _text(it, "title"),
                "link": _text(it, "link"),
                "date": _text(it, "pubDate", DC_DATE),
            }
        )
    for it in root.iter(f"{RSS1}item"):  # RSS 1.0 (RDF) feeds namespace their items
        items.append(
            {
                "title": _text(it, f"{RSS1}title"),
                "link": _text(it, f"{RSS1}link"),
                "date": _text(it, DC_DATE),
            }
        )
    for it in root.iter(f"{ATOM}entry"):
        link = ""
        for ln in it.findall(f"{ATOM}link"):
            if ln.get("rel", "alternate") == "alternate":
                link = ln.get("href", "")
                break
        items.append(
            {
                "title": _text(it, f"{ATOM}title"),
                "link": link,
                "date": _text(it, f"{ATOM}updated", f"{ATOM}published"),
            }
        )
    return items


def _date(d: str) -> datetime | None:
    """Parse an RFC 822 (RSS) or ISO 8601 (Atom, Dublin Core) date; None if unreadable."""
    try:
        if d[:1].isalpha():
            dt = parsedate_to_datetime(d)
        else:
            dt = datetime.fromisoformat(d.replace("Z", "+00:00"))
    except (ValueError, TypeError, IndexError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def newest(items: list[dict]) -> str | None:
    """The date of the newest item, ISO formatted, or None if no date could be read."""
    dates = [dt for i in items if (dt := _date(i["date"]))]
    return max(dates).date().isoformat() if dates else None


def main() -> int:
    """Run the check for the feed URL given on the command line."""
    url = sys.argv[1]
    n = int(sys.argv[sys.argv.index("--headlines") + 1]) if "--headlines" in sys.argv else 30
    f = HttpFetcher(user_agent=DEFAULT_USER_AGENT)
    out: dict = {
        "feed_url": url,
        "checked": datetime.now(UTC).date().isoformat(),
        "robots_allows_feed": f.is_allowed(url),
    }
    if not out["robots_allows_feed"]:
        out["result"] = "robots-disallows-feed"
        print(json.dumps(out, indent=1))
        return 0
    r = f.fetch(url)
    out["http_status"], out["error"], out["final_url"] = r.status_code, r.error, r.final_url
    items: list[dict] = []
    if r.status_code == 200 and r.content:
        try:
            items = parse(r.content)
        except ET.ParseError as exc:
            out["parse_error"] = str(exc)[:200]
    out["items"] = len(items)
    out["newest_item"] = newest(items)
    article = next((i["link"] for i in items if i["link"].startswith("http")), None)
    out["article_url_tested"] = article
    out["robots_allows_articles"] = f.is_allowed(article) if article else None
    print(json.dumps(out, indent=1))
    print("--- headlines (for counting only; do not save) ---")
    for i in items[:n]:
        print(f"- {i['title'][:140]}  <{i['link'][:120]}>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
