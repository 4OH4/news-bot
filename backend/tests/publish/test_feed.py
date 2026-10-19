"""Tests for the publish stage: ``build_feed`` and ``publish_feed``."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from newsfeed.config import UrlParamsConfig
from newsfeed.models import Article, ExtractionStatus, Source, SourceType
from newsfeed.publish.feed import FEED_LIMIT, build_feed, publish_feed
from newsfeed.wiring import Services
from tests.fakes import FakeExtractor, FakeFetcher, InMemoryBlobStore, InMemoryRepository

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
RUN_ID = "20261018T120000-000000"
SCHEMA_PATH = Path(__file__).parents[3] / "docs" / "feed.schema.json"
EXAMPLE_PATH = SCHEMA_PATH.with_name("feed.example.json")
SOURCES = [Source(id="gazette", type=SourceType.RSS, url="https://news.example.com/rss")]
VALIDATOR = Draft202012Validator(
    json.loads(SCHEMA_PATH.read_text()), format_checker=Draft202012Validator.FORMAT_CHECKER
)


def article(
    name: str,
    *,
    age_hours: float = 1,
    score: float | None = 1.0,
    status: ExtractionStatus = ExtractionStatus.OK,
    words: int = 300,
    **changes: Any,
) -> Article:
    url = f"https://www.news.example.com/{name}"
    return Article.model_validate(
        {
            "canonical_url": url,
            "url": url,
            "source_id": "gazette",
            "title": f"Story {name}",
            "fetched_at": NOW - timedelta(hours=age_hours),
            "score": score,
            "score_breakdown": {"source": 1.0, "recency": 0.5} if score is not None else {},
            "extraction": {"status": status, "word_count": words},
            "content_ref": f"ref/{name}",
            **changes,
        }
    )


def build(articles: list[Article], **kwargs: Any) -> dict[str, Any]:
    feed = build_feed(articles, SOURCES, now=NOW, run_id=RUN_ID, **kwargs).feed
    assert not list(VALIDATOR.iter_errors(feed)), list(VALIDATOR.iter_errors(feed))
    return feed


def names(feed: dict[str, Any]) -> list[str]:
    return [i["url"].rsplit("/", 1)[1] for i in feed["items"]]


def test_example_validates_against_the_schema() -> None:
    VALIDATOR.validate(json.loads(EXAMPLE_PATH.read_text()))


def test_orders_by_score_then_effective_time_then_id() -> None:
    feed = build(
        [
            article("low", score=0.5),
            article("old", score=2.0, age_hours=5),
            article("new", score=2.0, age_hours=2),
            article("tie-a", score=1.0, age_hours=3),
            article("tie-b", score=1.0, age_hours=3),
            article("tie-c", score=1.0, age_hours=3),
        ]
    )

    assert names(feed)[:2] == ["new", "old"]
    assert names(feed)[-1] == "low"
    tied = [i["id"] for i in feed["items"] if i["score"] == 1.0]
    assert len(tied) == 3
    assert tied == sorted(tied)


def test_items_are_unique_and_content_path_matches_id() -> None:
    feed = build([article(f"s{n}", score=float(n)) for n in range(10)])

    assert len({i["id"] for i in feed["items"]}) == 10
    for item in feed["items"]:
        assert item["content_path"] == f"articles/{item['id']}.html"


def test_window_uses_effective_time() -> None:
    feed = build(
        [
            article("fresh", age_hours=70),
            article("stale", age_hours=73),
            # Published long ago but fetched just now: out, by published_at.
            article("backdated", age_hours=1, published_at=NOW - timedelta(hours=100)),
            # Future-dated: capped at fetched_at, so it ages out with its fetch time.
            article("future", age_hours=80, published_at=NOW + timedelta(days=30)),
        ]
    )

    assert names(feed) == ["fresh"]


def test_item_limit() -> None:
    articles = [article(f"s{n}", score=float(n)) for n in range(FEED_LIMIT + 5)]

    feed = build(articles)

    assert len(feed["items"]) == FEED_LIMIT
    assert feed["items"][0]["score"] == FEED_LIMIT + 4


@pytest.mark.parametrize(
    ("status", "words", "display"),
    [
        (ExtractionStatus.OK, 400, "article"),
        (ExtractionStatus.OK, 10, "article"),
        (ExtractionStatus.PARTIAL, 150, "article"),
        (ExtractionStatus.PARTIAL, 149, "link"),
        (ExtractionStatus.PAYWALLED, 300, "link"),
        (ExtractionStatus.FAILED, 0, "link"),
        (ExtractionStatus.SKIPPED, 0, "link"),
        (ExtractionStatus.PENDING, 0, "link"),
    ],
)
def test_display_mode_per_extraction_status(
    status: ExtractionStatus, words: int, display: str
) -> None:
    feed = build([article("a", status=status, words=words)])

    (item,) = feed["items"]
    assert item["display"] == display
    assert item["extraction_status"] == status.value
    if display == "link":
        assert item["word_count"] is None
        assert item["content_path"] is None
    else:
        assert item["word_count"] == words


def test_missing_content_shows_as_a_link() -> None:
    articles = [article("blob"), article("noref", content_ref=None), article("gone")]

    built = build_feed(
        articles,
        SOURCES,
        now=NOW,
        run_id=RUN_ID,
        has_content=lambda a: a.content_ref == "ref/blob",
    )

    shown = {i["url"].rsplit("/", 1)[1]: i["display"] for i in built.feed["items"]}
    assert shown == {"blob": "article", "noref": "link", "gone": "link"}
    assert built.links_for_missing_content == 2
    assert not list(VALIDATOR.iter_errors(built.feed))


def test_muted_unscored_and_disabled_sources_are_left_out() -> None:
    rss = "https://x.example/rss"
    sources = [
        Source(id="gazette", type=SourceType.RSS, url=rss),
        Source(id="off", type=SourceType.RSS, url=rss, enabled=False),
        Source(id="zero", type=SourceType.RSS, url=rss, weight=0),
    ]
    articles = [
        article("shown"),
        article("muted", score_breakdown={"source": 1.0, "muted": 1.0}),
        article("unscored", score=None),
        article("disabled", source_id="off"),
        article("zero", source_id="zero"),
        article("adhoc", source_id="adhoc"),
    ]

    feed = build_feed(articles, sources, now=NOW, run_id=RUN_ID).feed

    assert names(feed) == ["shown"]


def test_item_fields() -> None:
    a = article(
        "full",
        author="A. Writer",
        summary="Plain summary.",
        lead_image="https://img.example/a.jpg",
        published_at=NOW - timedelta(hours=2),
    )

    (item,) = build([a])["items"]

    assert item["domain"] == "news.example.com"
    assert item["published_at"] == "2026-10-18T10:00:00Z"
    assert item["lead_image"] == "https://img.example/a.jpg"
    assert item["score_breakdown"] == {"source": 1.0, "recency": 0.5}


def test_bad_article_is_dropped_and_logged_not_fatal(caplog: pytest.LogCaptureFixture) -> None:
    articles = [
        article("good"),
        article("blank", title="  "),
        article("nan", score=float("nan")),
        article("space", canonical_url="https://news.example.com/a b", url="https://x.example/s"),
    ]

    with caplog.at_level(logging.WARNING):
        built = build_feed(articles, SOURCES, now=NOW, run_id=RUN_ID)

    assert names(built.feed) == ["good"]
    assert built.dropped == 3
    assert sum("dropped article from feed" in r.getMessage() for r in caplog.records) == 3
    assert not list(VALIDATOR.iter_errors(built.feed))


def test_bad_lead_image_becomes_null() -> None:
    (item,) = build([article("img", lead_image="not a url")])["items"]

    assert item["lead_image"] is None


def test_trailing_newline_is_invalid_in_urls_and_run_id() -> None:
    (item,) = build([article("img", lead_image="https://img.example/a.jpg\n")])["items"]
    assert item["lead_image"] is None

    articles = [
        article("good"),
        article("nl", canonical_url="https://news.example.com/nl\n"),
    ]
    built = build_feed(articles, SOURCES, now=NOW, run_id=RUN_ID)
    assert names(built.feed) == ["good"]
    assert built.dropped == 1

    with pytest.raises(ValueError):
        build_feed([], SOURCES, now=NOW, run_id=RUN_ID + "\n")


def test_schema_rejects_a_muted_key_and_unknown_fields() -> None:
    feed = build([article("a")])
    feed["items"][0]["score_breakdown"]["muted"] = 1.0
    assert list(VALIDATOR.iter_errors(feed))
    feed = build([article("a")])
    feed["items"][0]["extra"] = 1
    assert list(VALIDATOR.iter_errors(feed))


# --- publish_feed -------------------------------------------------------------


def services(blobs: InMemoryBlobStore | None = None) -> Services:
    return Services(
        repository=InMemoryRepository(),
        blobs=blobs or InMemoryBlobStore(),
        fetcher=FakeFetcher(),
        ingesters={},
        extractor=FakeExtractor(),
        url_rules=UrlParamsConfig(),
    )


def test_publish_writes_the_fixed_key_and_a_timestamped_copy() -> None:
    svc = services()
    ref = svc.blobs.put("articles/x.html", b"<p>hi</p>")
    svc.repository.upsert_article(article("keep", content_ref=ref))
    svc.repository.upsert_article(article("expired", content_ref="memory://articles/gone.html"))
    svc.repository.upsert_article(article("old", age_hours=100))

    built = publish_feed(svc, SOURCES, run_id=RUN_ID, now=NOW)

    latest = svc.blobs.get("memory://feed.json")
    assert latest == svc.blobs.get(f"memory://feeds/{RUN_ID}.json")
    feed = json.loads(latest)
    VALIDATOR.validate(feed)
    assert feed == built.feed
    assert {i["title"]: i["display"] for i in feed["items"]} == {
        "Story keep": "article",
        "Story expired": "link",
    }
    assert isinstance(svc.blobs, InMemoryBlobStore)
    assert svc.blobs.content_type("memory://feed.json") == "application/json"
