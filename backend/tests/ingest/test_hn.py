"""Tests for ``newsfeed.ingest.hn``. The Algolia JSON fixture is synthetic."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError

from newsfeed.ingest.hn import (
    ALGOLIA_SEARCH_URL,
    HackerNewsIngester,
    front_page_url,
    item_url,
    parse_hits,
)
from newsfeed.ingest.rss import FeedFetchError
from newsfeed.models import Article, ExtractionStatus, Source, SourceState, SourceType, article_id
from newsfeed.ports import SourceIngester
from tests.fakes import FakeFetcher

FEEDS = Path(__file__).parent.parent / "fixtures" / "feeds"
NOW = datetime(2026, 10, 7, 6, 0, tzinfo=UTC)


def clock() -> datetime:
    return NOW


def fixture() -> bytes:
    return (FEEDS / "hn_front_page.json").read_bytes()


def parse(content: bytes | None = None, **kwargs: Any) -> list[Article]:
    return parse_hits(
        fixture() if content is None else content, source_id="hn", fetched_at=NOW, **kwargs
    )


def hits_json(*hits: Any) -> bytes:
    return json.dumps({"hits": list(hits)}).encode()


def make_source(**kwargs: Any) -> Source:
    fields: dict[str, Any] = {"id": "hn", "type": SourceType.HN}
    fields.update(kwargs)
    return Source(**fields)


@pytest.fixture
def records() -> Iterator[list[logging.LogRecord]]:
    """Capture records from the ``newsfeed`` logger, which doesn't propagate to root."""
    captured: list[logging.LogRecord] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = _Handler(level=logging.DEBUG)
    logger = logging.getLogger("newsfeed")
    logger.addHandler(handler)
    try:
        yield captured
    finally:
        logger.removeHandler(handler)


# --- URLs --------------------------------------------------------------------


def test_front_page_url_queries_algolia_for_front_page_stories() -> None:
    url = front_page_url()
    assert url.startswith(f"{ALGOLIA_SEARCH_URL}?")
    params = parse_qs(urlsplit(url).query)
    assert params["tags"] == ["front_page"]
    # More than the 30 front-page slots, so filtering never runs short of hits.
    assert int(params["hitsPerPage"][0]) >= 30


def test_item_url() -> None:
    assert item_url("9000002") == "https://news.ycombinator.com/item?id=9000002"


# --- Parsing -----------------------------------------------------------------


def test_hits_become_articles_sorted_by_points() -> None:
    articles = parse()

    assert [a.title for a in articles] == [
        "A synthetic compiler written in a weekend",
        "Researchers model the migration of fictional geese",
        "Ask HN: How do you organise imaginary notes?",
        "Show HN: A placeholder project nobody upvoted",
    ]
    first = articles[0]
    assert first.url == "https://blog.example.com/weekend-compiler"
    assert first.canonical_url == first.url
    assert first.id == article_id(first.url)
    assert first.source_id == "hn"
    assert first.fetched_at == NOW
    assert first.published_at == datetime(2026, 10, 7, 3, 20, tzinfo=UTC)
    assert first.extraction.status is ExtractionStatus.PENDING
    # The submitter of a link isn't the article's author.
    assert first.author is None


def test_summary_carries_the_discussion_link() -> None:
    first = parse()[0]

    assert first.summary == (
        "Hacker News discussion (312 points, 87 comments): "
        "https://news.ycombinator.com/item?id=9000001"
    )


def test_ask_hn_links_to_the_item_page() -> None:
    ask = next(a for a in parse() if a.title.startswith("Ask HN"))

    assert ask.url == "https://news.ycombinator.com/item?id=9000002"
    assert ask.canonical_url == ask.url
    assert ask.author == "curious_example"
    assert ask.summary == (
        "Hacker News discussion (95 points, 140 comments): "
        "https://news.ycombinator.com/item?id=9000002 — "
        "I keep invented notes in three places & can never find them. What works for you?"
    )


@pytest.mark.parametrize("url", [None, "", "   ", "javascript:alert(1)", "item?id=1"])
def test_hits_without_a_usable_url_link_to_the_item_page(url: str | None) -> None:
    hit: dict[str, Any] = {"objectID": "77", "title": "Tell HN: placeholder", "points": 10}
    if url is not None:
        hit["url"] = url

    (article,) = parse(hits_json(hit))
    assert article.url == "https://news.ycombinator.com/item?id=77"


def test_long_text_post_is_truncated_but_keeps_the_link() -> None:
    hit = {"objectID": "5", "title": "Ask HN: long", "points": 1, "story_text": "word " * 500}

    (article,) = parse(hits_json(hit), summary_max_chars=200)
    assert article.summary is not None
    assert len(article.summary) <= 200
    assert article.summary.startswith(
        "Hacker News discussion (1 points, 0 comments): "
        "https://news.ycombinator.com/item?id=5 — word"
    )
    assert article.summary.endswith("…")


@pytest.mark.parametrize(
    ("min_points", "expected"),
    [(0, 4), (5, 3), (95, 3), (96, 2), (312, 1), (313, 0)],
)
def test_min_points_filters_stories(min_points: int, expected: int) -> None:
    articles = parse(min_points=min_points)
    assert len(articles) == expected


def test_max_items_keeps_the_highest_scoring() -> None:
    articles = parse(max_items=2)
    assert [a.title for a in articles] == [
        "A synthetic compiler written in a weekend",
        "Researchers model the migration of fictional geese",
    ]


def test_missing_points_count_as_zero() -> None:
    hit = {"objectID": "8", "title": "No points yet", "url": "https://example.com/a"}
    assert len(parse(hits_json(hit))) == 1
    assert parse(hits_json(hit), min_points=1) == []


def test_bad_hits_are_skipped_and_logged(records: list[logging.LogRecord]) -> None:
    articles = parse()

    # The fixture's untitled hit is dropped; the others survive.
    assert all(a.title for a in articles)
    warnings = [r for r in records if r.getMessage() == "skipped HN hit"]
    assert len(warnings) == 1
    record = warnings[0]
    assert record.levelno == logging.WARNING
    assert record.stage == "ingest"
    assert record.source_id == "hn"
    assert record.url == "https://news.ycombinator.com/item?id=9000005"
    assert record.error_type == "ValueError"


@pytest.mark.parametrize(
    "hit",
    [
        "not an object",
        {"title": "no id", "points": 1},
        {"objectID": "1", "title": "", "points": 1},
        {"objectID": "1", "title": "bad points", "points": "many"},
    ],
)
def test_individual_bad_hits_do_not_lose_the_rest(hit: Any) -> None:
    good = {"objectID": "2", "title": "Fine", "points": 3, "url": "https://example.com/"}
    assert [a.title for a in parse(hits_json(hit, good))] == ["Fine"]


@pytest.mark.parametrize(
    "content",
    [b"<html>not json</html>", b"\xff\xfe", b"[]", b'{"hits": {}}', b"{}"],
)
def test_response_that_is_not_search_json_raises(content: bytes) -> None:
    with pytest.raises(ValueError, match="HN response"):
        parse(content)


def test_created_at_string_is_used_when_the_timestamp_is_missing() -> None:
    hit = {"objectID": "3", "title": "t", "created_at": "2026-10-01T12:00:00Z"}
    (article,) = parse(hits_json(hit))
    assert article.published_at == datetime(2026, 10, 1, 12, tzinfo=UTC)

    (undated,) = parse(hits_json({"objectID": "4", "title": "t", "created_at": "soon"}))
    assert undated.published_at is None


def test_out_of_range_timestamp_falls_back_to_created_at() -> None:
    hit = {"objectID": "6", "title": "t", "created_at_i": 10**20, "created_at": "2026-10-01"}
    (article,) = parse(hits_json(hit))
    assert article.published_at == datetime(2026, 10, 1, tzinfo=UTC)


# --- Ingester ----------------------------------------------------------------


def test_ingester_implements_the_port() -> None:
    ingester = HackerNewsIngester()
    assert isinstance(ingester, SourceIngester)
    assert ingester.source_type is SourceType.HN


def test_ingest_applies_options_and_saves_state() -> None:
    fetcher = FakeFetcher()
    fetcher.add(
        front_page_url(),
        fixture(),
        headers={"content-type": "application/json; charset=utf-8"},
        last_modified="Wed, 07 Oct 2026 05:00:00 GMT",
    )
    source = make_source(options={"min_points": 100, "max_items": 5})

    articles, state = HackerNewsIngester(clock=clock).ingest(
        source, fetcher, SourceState(source_id="hn")
    )

    assert fetcher.fetched_urls() == [front_page_url()]
    assert [a.url for a in articles] == [
        "https://blog.example.com/weekend-compiler",
        "https://science.example.org/geese",
    ]
    assert state.last_modified == "Wed, 07 Oct 2026 05:00:00 GMT"
    assert state.last_status == 200
    assert state.last_fetched_at == NOW

    again, same_state = HackerNewsIngester(clock=clock).ingest(source, fetcher, state)
    assert fetcher.calls[-1].last_modified == "Wed, 07 Oct 2026 05:00:00 GMT"
    assert again == []
    assert same_state == state


def test_source_url_overrides_the_default_query() -> None:
    url = "https://hn.algolia.com/api/v1/search?tags=show_hn"
    fetcher = FakeFetcher({url: fixture()})

    articles, _ = HackerNewsIngester().ingest(
        make_source(url=url), fetcher, SourceState(source_id="hn")
    )
    assert fetcher.fetched_urls() == [url]
    assert len(articles) == 4


@pytest.mark.parametrize(
    "options",
    [
        {"min_points": -1},
        {"max_items": 0},
        {"min_points": "10"},
        {"max_items": True},
        {"min_score": 10},
    ],
)
def test_invalid_options_are_rejected(options: dict[str, Any]) -> None:
    fetcher = FakeFetcher({front_page_url(): fixture()})
    with pytest.raises(ValidationError):
        HackerNewsIngester().ingest(
            make_source(options=options), fetcher, SourceState(source_id="hn")
        )
    # Bad config fails before anything is fetched.
    assert fetcher.calls == []


def test_robots_refusal_raises_and_is_not_bypassed() -> None:
    fetcher = FakeFetcher({front_page_url(): fixture()}, disallowed=["https://hn.algolia.com/api"])

    with pytest.raises(FeedFetchError, match=r"robots\.txt"):
        HackerNewsIngester().ingest(make_source(), fetcher, SourceState(source_id="hn"))


def test_http_error_raises_with_status() -> None:
    fetcher = FakeFetcher()
    fetcher.add(front_page_url(), b"", status=503)

    with pytest.raises(FeedFetchError) as excinfo:
        HackerNewsIngester().ingest(make_source(), fetcher, SourceState(source_id="hn"))
    assert excinfo.value.status_code == 503
