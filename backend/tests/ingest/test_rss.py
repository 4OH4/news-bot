"""Tests for ``newsfeed.ingest.rss``. Every feed in ``fixtures/feeds`` is synthetic."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

import pytest
from pydantic import model_validator

from newsfeed.ingest import rss
from newsfeed.ingest.rss import FeedFetchError, RssIngester, parse_feed
from newsfeed.models import Article, ExtractionStatus, Source, SourceState, SourceType, article_id
from newsfeed.ports import SourceIngester
from tests.fakes import FakeFetcher, FakeResponse

FEEDS = Path(__file__).parent.parent / "fixtures" / "feeds"
NOW = datetime(2026, 10, 7, 6, 0, tzinfo=UTC)
FEED_URL = "https://news.example.com/feed.xml"


def clock() -> datetime:
    return NOW


def feed(name: str) -> bytes:
    return (FEEDS / name).read_bytes()


def parse(name: str, feed_url: str = FEED_URL) -> list[Article]:
    return parse_feed(feed(name), source_id="example", feed_url=feed_url, fetched_at=NOW)


def make_source(url: str = FEED_URL) -> Source:
    return Source(id="example", type=SourceType.RSS, url=url)


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


def warnings_with(records: list[logging.LogRecord], message: str) -> list[logging.LogRecord]:
    return [r for r in records if r.levelno == logging.WARNING and r.getMessage() == message]


# --- Parsing -----------------------------------------------------------------


def test_rss2_entries_are_normalised() -> None:
    articles = parse("rss2.xml")

    assert [a.url for a in articles] == [
        "https://news.example.com/2026/10/floating-bike-lanes?utm_source=rss",
        "https://news.example.com/2026/10/square-doughnut",
        "https://news.example.com/2026/10/ordinary-tuesday",
    ]
    first = articles[0]
    # canonical_url is the link as given: canonicalisation happens in the pipeline.
    assert first.canonical_url == first.url
    assert first.id == article_id(first.url)
    assert first.source_id == "example"
    assert first.title == "Town council approves floating bike lanes"
    assert first.author == "Ada Placeholder"
    assert first.fetched_at == NOW
    assert first.extraction.status is ExtractionStatus.PENDING
    assert first.content_ref is None


def test_rss2_dates_are_utc_or_none() -> None:
    first, second, third = parse("rss2.xml")
    # +0100 converted to UTC
    assert first.published_at == datetime(2026, 10, 5, 8, 30, tzinfo=UTC)
    assert second.published_at == datetime(2026, 10, 4, 23, 15, tzinfo=UTC)
    assert third.published_at is None


def test_summary_is_plain_text() -> None:
    summary = parse("rss2.xml")[0].summary
    assert summary == (
        "The council in Imaginaria voted 7\u20132 to trial floating cycle lanes on the canal. "
        "Residents & boaters were consulted."
    )


def test_long_summary_is_truncated_to_500_chars() -> None:
    summary = parse("rss2.xml")[1].summary
    assert summary is not None
    assert len(summary) <= 500
    assert summary.endswith("…")
    assert not summary.endswith(" …")
    assert summary.startswith("The bakery on Placeholder Lane says the new shape")


def test_summary_limit_is_configurable() -> None:
    articles = parse_feed(
        feed("rss2.xml"),
        source_id="example",
        feed_url=FEED_URL,
        fetched_at=NOW,
        summary_max_chars=40,
    )
    assert all(len(a.summary or "") <= 40 for a in articles)


def test_atom_entries_are_normalised() -> None:
    # Served from a different host than the feed's xml:base, to show xml:base wins.
    first, second = parse("atom.xml", feed_url="https://feeds.example.org/blog.atom")

    assert first.url == "https://blog.example.org/posts/robots-knit"  # xml:base, not enclosure
    assert first.title == "Why our robots now knit"
    assert first.author == "Grace Example"
    assert first.published_at == datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    assert first.summary == "A short note on teaching invented robots to knit scarves."

    assert second.url == "https://blog.example.org/posts/widget-2"
    # No <published>: falls back to <updated>, converted from +02:00.
    assert second.published_at == datetime(2026, 10, 2, 13, 45, 30, tzinfo=UTC)
    # No <summary>: falls back to <content>.
    assert second.summary == "What changed The widget now spins in both directions."
    assert second.author is None


def test_relative_links_are_resolved_against_the_feed_url() -> None:
    articles = parse("relative_links.xml", feed_url="https://relative.example.net/feeds/main.xml")
    assert [a.url for a in articles] == [
        "https://relative.example.net/stories/root-relative",
        "https://relative.example.net/feeds/path-relative.html",
        "https://mirror.example.net/stories/protocol-relative",
        "https://other.example.com/stories/absolute",
    ]
    assert all(a.canonical_url == a.url for a in articles)


def test_malformed_feed_returns_what_parsed_and_logs(records: list[logging.LogRecord]) -> None:
    articles = parse("malformed.xml", feed_url="https://sloppy.example.com/rss")

    assert [a.title for a in articles] == [
        "Fish & chips shop wins invented award",
        "Second story survives the bad markup",
    ]
    [record] = warnings_with(records, "malformed feed; using the entries that parsed")
    assert record.__dict__["stage"] == "ingest"
    assert record.__dict__["source_id"] == "example"
    assert record.__dict__["url"] == "https://sloppy.example.com/rss"
    assert record.__dict__["error_type"] == "SAXParseException"
    assert "not well-formed" in record.__dict__["error"]


def test_non_feed_content_yields_no_articles(records: list[logging.LogRecord]) -> None:
    articles = parse_feed(
        b"<html><body>Not a feed</body></html>",
        source_id="example",
        feed_url=FEED_URL,
        fetched_at=NOW,
        headers={"content-type": "text/html"},
    )
    assert articles == []
    assert warnings_with(records, "malformed feed; using the entries that parsed")


@pytest.mark.parametrize(
    "content_type",
    [None, "application/rss+xml; charset=utf-8", "text/xml", "text/plain", "text/html"],
)
def test_well_formed_feed_logs_no_warnings(
    records: list[logging.LogRecord], content_type: str | None
) -> None:
    headers = {"content-type": content_type} if content_type else {}
    for name in ("rss2.xml", "atom.xml"):
        articles = parse_feed(
            feed(name), source_id="example", feed_url=FEED_URL, fetched_at=NOW, headers=headers
        )
        assert articles
    assert [r for r in records if r.levelno >= logging.WARNING] == []


def test_entries_without_a_usable_link_are_skipped_and_logged(
    records: list[logging.LogRecord],
) -> None:
    articles = parse("bad_entries.xml", feed_url="https://odd.example.com/rss")

    assert [a.url for a in articles] == [
        "https://odd.example.com/news/good-1",
        "https://odd.example.com/news/good-2",
    ]
    skipped = warnings_with(records, "skipped feed entry")
    assert [(r.__dict__["url"], r.__dict__["error"]) for r in skipped] == [
        ("https://odd.example.com/rss", "entry has no link"),
        (
            "mailto:editor@odd.example.com",
            "entry link 'mailto:editor@odd.example.com' is not an http(s) URL",
        ),
    ]
    for record in skipped:
        assert record.__dict__["stage"] == "ingest"
        assert record.__dict__["source_id"] == "example"
        assert record.__dict__["error_type"] == "ValueError"


def test_entry_failing_validation_is_skipped_and_logged(
    records: list[logging.LogRecord], monkeypatch: pytest.MonkeyPatch
) -> None:
    class PickyArticle(Article):
        """Rejects one title, standing in for any entry that fails validation."""

        @model_validator(mode="after")
        def _reject(self) -> Self:
            if "square doughnut" in self.title:
                raise ValueError("title rejected by test")
            return self

    monkeypatch.setattr(rss, "Article", PickyArticle)
    articles = parse("rss2.xml")

    assert [a.url for a in articles] == [
        "https://news.example.com/2026/10/floating-bike-lanes?utm_source=rss",
        "https://news.example.com/2026/10/ordinary-tuesday",
    ]
    [record] = warnings_with(records, "skipped feed entry")
    assert record.__dict__["stage"] == "ingest"
    assert record.__dict__["source_id"] == "example"
    assert record.__dict__["url"] == "https://news.example.com/2026/10/square-doughnut"
    assert record.__dict__["error_type"] == "ValidationError"
    assert "title rejected by test" in record.__dict__["error"]


# --- Ingesting through the Fetcher -------------------------------------------


def test_rss_ingester_implements_the_port() -> None:
    ingester = RssIngester()
    assert isinstance(ingester, SourceIngester)
    assert ingester.source_type is SourceType.RSS


def test_ingest_returns_articles_and_new_state() -> None:
    fetcher = FakeFetcher()
    fetcher.add(
        FEED_URL,
        feed("rss2.xml"),
        headers={"content-type": "application/rss+xml; charset=utf-8"},
        etag='"v1"',
        last_modified="Wed, 07 Oct 2026 05:00:00 GMT",
    )
    state = SourceState(source_id="example")

    articles, new_state = RssIngester(clock=clock).ingest(make_source(), fetcher, state)

    assert len(articles) == 3
    assert all(a.fetched_at == NOW for a in articles)
    assert new_state == SourceState(
        source_id="example",
        etag='"v1"',
        last_modified="Wed, 07 Oct 2026 05:00:00 GMT",
        last_fetched_at=NOW,
        last_status=200,
    )
    assert state == SourceState(source_id="example")  # input not mutated
    assert fetcher.calls[0].etag is None
    assert fetcher.calls[0].last_modified is None


@pytest.mark.parametrize(
    "state_kwargs",
    [
        {"etag": '"v1"'},
        {"last_modified": "Wed, 07 Oct 2026 05:00:00 GMT"},
        {"etag": '"v1"', "last_modified": "Wed, 07 Oct 2026 05:00:00 GMT"},
    ],
)
def test_not_modified_yields_no_articles_and_unchanged_state(state_kwargs: dict[str, Any]) -> None:
    fetcher = FakeFetcher()
    fetcher.add(
        FEED_URL,
        feed("rss2.xml"),
        etag='"v1"',
        last_modified="Wed, 07 Oct 2026 05:00:00 GMT",
    )
    state = SourceState(
        source_id="example",
        last_fetched_at=datetime(2026, 10, 6, tzinfo=UTC),
        last_status=200,
        **state_kwargs,
    )

    articles, new_state = RssIngester(clock=clock).ingest(make_source(), fetcher, state)

    assert articles == []
    assert new_state == state
    [call] = fetcher.calls
    assert call.etag == state.etag
    assert call.last_modified == state.last_modified


def test_second_ingest_uses_saved_state_for_a_conditional_get() -> None:
    fetcher = FakeFetcher()
    fetcher.add(FEED_URL, feed("rss2.xml"), etag='"v1"')
    ingester = RssIngester(clock=clock)

    first, state = ingester.ingest(make_source(), fetcher, SourceState(source_id="example"))
    second, state_after = ingester.ingest(make_source(), fetcher, state)

    assert len(first) == 3
    assert second == []
    assert state_after == state


def test_validators_absent_from_response_are_cleared() -> None:
    fetcher = FakeFetcher({FEED_URL: feed("rss2.xml")})
    state = SourceState(source_id="example", etag='"old"', last_modified="old")

    _, new_state = RssIngester(clock=clock).ingest(make_source(), fetcher, state)

    assert new_state.etag is None
    assert new_state.last_modified is None
    assert new_state.last_status == 200


def test_relative_links_resolve_against_the_final_url_after_redirects() -> None:
    fetcher = FakeFetcher()
    fetcher.add(
        "https://relative.example.net/rss",
        FakeResponse(
            body=feed("relative_links.xml"),
            final_url="https://relative.example.net/feeds/v2/main.xml",
        ),
    )
    source = make_source("https://relative.example.net/rss")

    articles, _ = RssIngester(clock=clock).ingest(source, fetcher, SourceState(source_id="example"))

    assert articles[1].url == "https://relative.example.net/feeds/v2/path-relative.html"


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (FakeResponse(status=500), "HTTP 500"),
        (FakeResponse(status=None, error="connection reset"), "connection reset"),
    ],
)
def test_fetch_failure_raises(response: FakeResponse, reason: str) -> None:
    fetcher = FakeFetcher({FEED_URL: response})
    with pytest.raises(FeedFetchError, match=reason):
        RssIngester(clock=clock).ingest(make_source(), fetcher, SourceState(source_id="example"))


def test_unknown_url_and_robots_disallow_raise() -> None:
    state = SourceState(source_id="example")
    with pytest.raises(FeedFetchError, match="404"):
        RssIngester().ingest(make_source(), FakeFetcher(), state)
    blocked = FakeFetcher({FEED_URL: feed("rss2.xml")}, disallowed=["https://news.example.com/"])
    with pytest.raises(FeedFetchError, match=r"robots\.txt"):
        RssIngester().ingest(make_source(), blocked, state)


def test_source_without_url_is_rejected() -> None:
    # models.Source rejects this at load time; model_construct bypasses that.
    source = Source.model_construct(id="example", type=SourceType.RSS, url=None)
    with pytest.raises(ValueError, match="has no url"):
        RssIngester().ingest(source, FakeFetcher(), SourceState(source_id="example"))
