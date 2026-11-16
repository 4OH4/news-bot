"""One item per story cluster (#325): ``one_per_cluster`` and its use in ``build_feed``."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from newsfeed.models import Article, ExtractionStatus, Source, SourceType
from newsfeed.publish.feed import build_feed, one_per_cluster

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
SCHEMA = json.loads((Path(__file__).parents[3] / "docs" / "feed.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=Draft202012Validator.FORMAT_CHECKER)
SOURCES = [
    Source(id=name, type=SourceType.RSS, url=f"https://{name}.example.com/rss")
    for name in ("bbc", "guardian", "independent")
] + [Source(id="off", type=SourceType.RSS, url="https://off.example.com/rss", enabled=False)]


def article(
    name: str,
    source: str = "bbc",
    *,
    score: float = 1.0,
    age_hours: float = 1,
    cluster_id: str | None = "c1",
    status: ExtractionStatus = ExtractionStatus.OK,
    muted: bool = False,
) -> Article:
    url = f"https://{source}.example.com/{name}"
    breakdown = {"source": 1.0, "muted": 1.0} if muted else {"source": 1.0}
    return Article.model_validate(
        {
            "canonical_url": url,
            "url": url,
            "source_id": source,
            "title": f"Story {name}",
            "fetched_at": NOW - timedelta(hours=age_hours),
            "score": score,
            "score_breakdown": breakdown,
            "extraction": {"status": status, "word_count": 300},
            "content_ref": f"ref/{name}",
            "cluster_id": cluster_id,
        }
    )


def build(articles: list[Article]) -> dict[str, Any]:
    feed = build_feed(articles, SOURCES, now=NOW, run_id="20261018T120000-000000").feed
    VALIDATOR.validate(feed)
    return feed


def shown(feed: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {i["url"].rsplit("/", 1)[1]: i for i in feed["items"]}


def test_the_highest_scored_row_of_a_cluster_is_shown_with_its_source_count() -> None:
    rows = [
        article("b", "bbc", score=2.0),
        article("g", "guardian", score=3.5),
        article("i", "independent", score=1.0),
    ]

    ((row, count),) = one_per_cluster(rows)

    assert row.source_id == "guardian"
    assert count == 3


def test_rows_from_one_source_count_once_and_rows_without_a_cluster_are_kept() -> None:
    rows = [
        article("a", score=2.0),
        article("b", score=1.0),
        article("x", cluster_id=None),
        article("y", cluster_id=None),
    ]

    result = one_per_cluster(rows)

    assert sorted(r.canonical_url.rsplit("/", 1)[1] for r, _ in result) == ["a", "x", "y"]
    counts = {r.canonical_url.rsplit("/", 1)[1]: n for r, n in result}
    assert counts["a"] == 1
    assert counts["x"] <= 1


def test_a_tie_goes_to_the_article_then_the_earliest_time_then_the_id() -> None:
    link = article("link", "bbc", status=ExtractionStatus.FAILED, age_hours=9)
    full = article("full", "guardian", age_hours=1)
    early = article("early", "independent", age_hours=5)
    late = article("late", "guardian", age_hours=2)

    ((row, _),) = one_per_cluster([link, full])
    assert row.id == full.id
    ((row, _),) = one_per_cluster([late, early])
    assert row.id == early.id
    twin_a = article("same", "bbc", age_hours=3)
    twin_b = article("same", "guardian", age_hours=3)
    ((row, _),) = one_per_cluster([twin_b, twin_a])
    assert row.id == min(twin_a.id, twin_b.id)


def test_a_cluster_is_one_item_with_coverage_and_the_rows_own_story_id() -> None:
    feed = build(
        [
            article("b", "bbc", score=2.0),
            article("g", "guardian", score=3.5),
            article("i", "independent", score=1.0),
        ]
    )

    (item,) = feed["items"]
    assert item["url"].endswith("/g")
    assert item["coverage"] == 3
    assert item["story_id"] == item["id"]


def test_a_cluster_from_one_source_has_no_coverage() -> None:
    (item,) = build([article("a", score=2.0), article("b", score=1.0)])["items"]

    assert "coverage" not in item


@pytest.mark.parametrize("excluded", ["muted", "disabled"])
def test_only_eligible_rows_are_counted_or_shown(excluded: str) -> None:
    out = (
        article("m", "independent", score=9.0, muted=True)
        if excluded == "muted"
        else article("m", "off", score=9.0)
    )

    feed = build([article("b", "bbc", score=2.0), article("g", "guardian", score=3.0), out])

    (item,) = feed["items"]
    assert item["url"].endswith("/g")
    assert item["coverage"] == 2


def test_articles_without_a_cluster_are_published_as_before() -> None:
    feed = build([article("a", cluster_id=None), article("b", "guardian", cluster_id=None)])

    assert set(shown(feed)) == {"a", "b"}
    assert all("coverage" not in i for i in feed["items"])
