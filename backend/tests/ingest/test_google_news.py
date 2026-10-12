"""Tests for ``newsfeed.ingest.google_news``. The feed fixture is synthetic."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError

from newsfeed.ingest.google_news import (
    GOOGLE_NEWS_SEARCH_URL,
    GoogleNewsIngester,
    adapt_entry,
    build_search_url,
)
from newsfeed.ingest.rss import FeedFetchError, parse_feed
from newsfeed.models import Article, ExtractionStatus, Source, SourceState, SourceType
from newsfeed.ports import SourceIngester
from tests.fakes import FakeFetcher

FEEDS = Path(__file__).parent.parent / "fixtures" / "feeds"
NOW = datetime(2026, 10, 7, 6, 0, tzinfo=UTC)
QUERY = '"floating bike lanes"'
SEARCH_URL = build_search_url(QUERY)


def clock() -> datetime:
    return NOW


def fixture() -> bytes:
    return (FEEDS / "google_news_search.xml").read_bytes()


def make_source(**kwargs: Any) -> Source:
    fields: dict[str, Any] = {"id": "gnews-bikes", "type": SourceType.GOOGLE_NEWS, "query": QUERY}
    fields.update(kwargs)
    return Source(**fields)


def query_params(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query, keep_blank_values=True, strict_parsing=True)


# --- URL builder -------------------------------------------------------------


def test_search_url_uses_the_default_edition() -> None:
    assert build_search_url("solar") == (
        "https://news.google.com/rss/search?q=solar&hl=en-GB&gl=GB&ceid=GB:en"
    )


@pytest.mark.parametrize(
    "query",
    [
        "heat pumps",
        '"exact phrase" -excluded',
        "fish & chips",
        "C# vs F#",
        "1+1=2",
        "50% off?",
        "café crème",
        "東京 天気",
        "site:example.com when:7d",
        "a/b\\c",
    ],
)
def test_search_url_round_trips_the_query(query: str) -> None:
    url = build_search_url(query)

    assert url.startswith(f"{GOOGLE_NEWS_SEARCH_URL}?q=")
    assert query_params(url)["q"] == [query]
    # Nothing in the query can leak into another parameter or the fragment.
    assert set(query_params(url)) == {"q", "hl", "gl", "ceid"}
    assert urlsplit(url).fragment == ""


def test_search_url_percent_encodes_spaces_and_reserved_characters() -> None:
    url = build_search_url("fish & chips +peas #1")

    assert "q=fish%20%26%20chips%20%2Bpeas%20%231&" in url


def test_search_url_strips_surrounding_whitespace() -> None:
    assert query_params(build_search_url("  solar  "))["q"] == ["solar"]


@pytest.mark.parametrize("query", ["", "   "])
def test_blank_query_is_rejected(query: str) -> None:
    with pytest.raises(ValueError, match="blank"):
        build_search_url(query)


def test_ceid_defaults_to_country_and_language() -> None:
    params = query_params(build_search_url("x", hl="fr", gl="FR"))
    assert params["hl"] == ["fr"]
    assert params["gl"] == ["FR"]
    assert params["ceid"] == ["FR:fr"]

    params = query_params(build_search_url("x", hl="en-US", gl="US"))
    assert params["ceid"] == ["US:en"]


def test_explicit_ceid_is_used() -> None:
    params = query_params(build_search_url("x", hl="pt-BR", gl="BR", ceid="BR:pt-419"))
    assert params["ceid"] == ["BR:pt-419"]


# --- Source configuration ----------------------------------------------------


def test_feed_url_comes_from_query_and_options() -> None:
    ingester = GoogleNewsIngester()
    source = make_source(options={"hl": "de", "gl": "DE"})

    assert ingester.feed_url(source) == build_search_url(QUERY, hl="de", gl="DE")


def test_feed_url_can_be_given_directly() -> None:
    url = "https://news.google.com/rss/headlines/section/topic/SCIENCE?hl=en-GB&gl=GB&ceid=GB:en"
    source = make_source(query=None, url=url)

    assert GoogleNewsIngester().feed_url(source) == url


@pytest.mark.parametrize(
    "fields",
    [
        {"query": None},
        {"query": "  "},
        {"url": "https://news.google.com/rss/search?q=x"},
    ],
    ids=["neither", "blank-query", "both"],
)
def test_source_needs_exactly_one_of_query_or_url(fields: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="exactly one of 'query' or 'url'"):
        GoogleNewsIngester().feed_url(make_source(**fields))


@pytest.mark.parametrize("options", [{"language": "en"}, {"hl": 1}, {"ceid": ["GB:en"]}])
def test_invalid_options_are_rejected(options: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        GoogleNewsIngester().feed_url(make_source(options=options))


# --- Parsing -----------------------------------------------------------------


def test_items_are_parsed_with_publisher_removed_from_titles() -> None:
    articles = parse_feed(
        fixture(),
        source_id="gnews-bikes",
        feed_url=SEARCH_URL,
        fetched_at=NOW,
        adapt_entry=adapt_entry,
    )

    assert [a.title for a in articles] == [
        "Placeville trials floating bike lanes on the canal",
        # Only the trailing " - Publisher" goes; dashes inside the headline stay.
        "Cyclists - and ducks - review the new lanes",
        "Lane markings without a publisher suffix",
    ]
    # Links stay the Google News redirect URLs (see the module docstring).
    assert all(a.url.startswith("https://news.google.com/rss/articles/") for a in articles)
    assert all(a.canonical_url == a.url for a in articles)
    assert articles[0].published_at == datetime(2026, 10, 7, 4, 15, tzinfo=UTC)
    assert all(a.author is None for a in articles)


def test_summary_repeating_the_headline_is_dropped() -> None:
    articles = parse_feed(
        fixture(), source_id="g", feed_url=SEARCH_URL, fetched_at=NOW, adapt_entry=adapt_entry
    )

    # Just "headline + publisher": nothing worth keeping.
    assert articles[0].summary is None
    # A story cluster lists related coverage, which is kept.
    assert articles[1].summary is not None
    assert "Canal lanes open to mixed reviews" in articles[1].summary
    # No <source>: the item is left exactly as the RSS parser built it.
    assert articles[2].summary == "Lane markings without a publisher suffix"


def make_article(title: str, summary: str | None) -> Article:
    return Article(
        canonical_url="https://news.google.com/rss/articles/X",
        url="https://news.google.com/rss/articles/X",
        source_id="g",
        title=title,
        fetched_at=NOW,
        summary=summary,
    )


def test_adapt_entry_keeps_a_title_that_is_only_the_publisher() -> None:
    article = make_article(" - Example Herald", None)
    entry = {"source": {"title": "Example Herald"}}

    assert adapt_entry(entry, article).title == " - Example Herald"


def test_adapt_entry_ignores_a_different_publisher_suffix() -> None:
    article = make_article("Headline - Other Paper", "Headline - Other Paper Other Paper")
    entry = {"source": {"title": "Example Herald", "href": "https://herald.example.org"}}

    adapted = adapt_entry(entry, article)
    assert adapted.title == "Headline - Other Paper"
    assert adapted.summary == "Headline - Other Paper Other Paper"


# --- Ingester ----------------------------------------------------------------


def test_ingester_implements_the_port() -> None:
    ingester = GoogleNewsIngester()
    assert isinstance(ingester, SourceIngester)
    assert ingester.source_type is SourceType.GOOGLE_NEWS


def test_ingest_fetches_the_search_feed_and_saves_state() -> None:
    fetcher = FakeFetcher()
    fetcher.add(
        SEARCH_URL,
        fixture(),
        headers={"content-type": "application/xml; charset=utf-8"},
        etag='"gn-1"',
    )
    source = make_source()

    articles, state = GoogleNewsIngester(clock=clock).ingest(
        source, fetcher, SourceState(source_id=source.id)
    )

    assert fetcher.fetched_urls() == [SEARCH_URL]
    assert len(articles) == 3
    assert {a.source_id for a in articles} == {"gnews-bikes"}
    assert {a.extraction.status for a in articles} == {ExtractionStatus.PENDING}
    assert {a.fetched_at for a in articles} == {NOW}
    assert articles[0].title == "Placeville trials floating bike lanes on the canal"
    assert state.etag == '"gn-1"'
    assert state.last_status == 200
    assert state.last_fetched_at == NOW

    # The next run is conditional, and a 304 yields nothing and keeps the state.
    again, same_state = GoogleNewsIngester(clock=clock).ingest(source, fetcher, state)
    assert fetcher.calls[-1].etag == '"gn-1"'
    assert again == []
    assert same_state == state


def test_robots_refusal_raises_and_is_not_bypassed() -> None:
    fetcher = FakeFetcher({SEARCH_URL: fixture()}, disallowed=["https://news.google.com/rss"])
    source = make_source()
    state = SourceState(source_id=source.id)

    with pytest.raises(FeedFetchError, match=r"robots\.txt") as excinfo:
        GoogleNewsIngester().ingest(source, fetcher, state)

    assert excinfo.value.status_code is None
    assert fetcher.fetched_urls() == [SEARCH_URL]
