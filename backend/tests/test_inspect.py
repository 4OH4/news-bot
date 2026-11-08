"""Unit tests for the pure query logic in ``newsfeed.inspect`` (issue #265)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from newsfeed import inspect as q
from newsfeed.models import Article, ArticleExtraction, ExtractionStatus, Feedback, FeedbackEvent
from tests.fakes import InMemoryRepository

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def art(slug: str, *, domain: str = "example.com", **kw: Any) -> Article:
    extraction = ArticleExtraction(
        status=kw.pop("status", ExtractionStatus.OK), word_count=kw.pop("words", 0)
    )
    return Article(
        canonical_url=f"https://{domain}/{slug}",
        url=f"https://{domain}/{slug}",
        source_id=kw.pop("source", "src"),
        title=kw.pop("title", f"Headline {slug}"),
        fetched_at=NOW,
        extraction=extraction,
        **kw,
    )


@pytest.mark.parametrize(
    ("text", "delta"),
    [("90m", timedelta(minutes=90)), ("24h", timedelta(hours=24)), ("7d", timedelta(days=7))],
)
def test_parse_since_durations(text: str, delta: timedelta) -> None:
    assert q.parse_since(text, NOW) == NOW - delta


def test_parse_since_dates_default_to_utc() -> None:
    assert q.parse_since("2026-11-01", NOW) == datetime(2026, 11, 1, tzinfo=UTC)
    assert q.parse_since("2026-11-01T08:30:00+01:00", NOW).utcoffset() == timedelta(hours=1)


@pytest.mark.parametrize("text", ["3x", "", "h", "-1d", "yesterday"])
def test_parse_since_rejects_garbage_naming_the_format(text: str) -> None:
    with pytest.raises(q.InspectError, match="90m"):
        q.parse_since(text, NOW)


def test_check_choice_names_the_choices() -> None:
    with pytest.raises(q.InspectError, match="id, source_id"):
        q.check_choice("--fields", "body", ["id", "source_id"])


def test_filters_reject_inverted_score_bounds_and_bad_regex() -> None:
    with pytest.raises(q.InspectError, match="above"):
        q.filter_articles([], min_score=3, max_score=1)
    with pytest.raises(q.InspectError, match="regex"):
        q.filter_articles([], title_match="(")


def test_has_prefix_and_exact_matching() -> None:
    a = art("a", score_breakdown={"demote:sport": -1.0})
    b = art("b", score_breakdown={"demoted": 1.0})
    assert q.filter_articles([a, b], has=["demote:"]) == [a]
    assert q.filter_articles([a, b], has=["demote"]) == []
    assert q.filter_articles([a, b], lacks=["demote:"]) == [b]


def test_rows_sort_blanks_last_and_break_ties_by_id() -> None:
    items = [art("b", score=1.0), art("n"), art("a", score=1.0)]
    rows = q.list_article_rows(items, ["score"], "score")
    assert [r["id"] for r in rows] == [*sorted([items[0].id, items[2].id]), items[1].id]


def test_group_by_domain_matches_the_issue_example() -> None:
    items = [art(c, words=w) for c, w in (("a", 400), ("b", 600), ("c", 800))]
    items.append(art("d", status=ExtractionStatus.FAILED))
    items.append(art("e", domain="other.org", words=50))

    rows = q.group_articles(items, "domain")

    assert [r["domain"] for r in rows] == ["example.com", "other.org"]
    first = rows[0]
    assert (first["count"], first["ok"], first["failed"], first["word_count_median"]) == (
        4,
        3,
        1,
        500,
    )
    assert first["score_mean"] is None


def test_group_by_breakdown_counts_each_key_once_per_article() -> None:
    rows = q.group_articles(
        [
            art("a", score_breakdown={"recency": 1.0, "x": 2.0}),
            art("b", score_breakdown={"recency": 3.0}),
        ],
        "breakdown",
    )
    assert {r["breakdown"]: r["count"] for r in rows} == {"recency": 2, "x": 1}


def test_group_score_statistics() -> None:
    rows = q.group_articles([art("a", score=1.0), art("b", score=3.0), art("c")], "source_id")
    assert rows[0]["score_mean"] == 2.0
    assert rows[0]["score_median"] == 2.0


def test_group_feedback_counts_largest_first() -> None:
    rows = [{"event": "like"}, {"event": "dismiss"}, {"event": "like"}]
    assert q.group_feedback(rows, "event") == [
        {"event": "like", "count": 2},
        {"event": "dismiss", "count": 1},
    ]


def test_query_feedback_joins_articles_and_sorts_newest_first() -> None:
    repo = InMemoryRepository()
    kept = art("k", source="s1", title="Kept")
    repo.upsert_article(kept)
    old = Feedback(
        article_id=kept.id, event=FeedbackEvent.LIKE, created_at=NOW - timedelta(hours=2)
    )
    new = Feedback(article_id="missing", event=FeedbackEvent.DISMISS, created_at=NOW)
    repo.save_feedback(old)
    repo.save_feedback(new)

    rows = q.query_feedback(repo, since=NOW - timedelta(days=1))

    assert [r["event"] for r in rows] == ["dismiss", "like"]
    assert rows[0]["title"] is None
    assert rows[1]["source_id"] == "s1"
    day = NOW - timedelta(days=1)
    assert [r["event"] for r in q.query_feedback(repo, since=day, events=["like"])] == ["like"]
    with pytest.raises(q.InspectError, match="like"):
        q.query_feedback(repo, since=NOW, events=["nonsense"])


def test_render_formats_contain_exactly_the_columns() -> None:
    rows = [{"id": "a", "score": 1.5, "extra": "x", "at": NOW, "bd": {"k": 0.25}}]
    lines = q.render(rows, ["id", "score"], "jsonl").splitlines()
    assert json.loads(lines[0]) == {"id": "a", "score": 1.5}
    assert q.render(rows, ["id", "score"], "csv").splitlines() == ["id,score", "a,1.5"]
    text = q.render(rows, ["id", "at", "bd"], "text")
    assert "2026-11-08T12:00" in text
    assert "k=0.25" in text
    with pytest.raises(q.InspectError, match="jsonl"):
        q.render(rows, ["id"], "xml")


@pytest.mark.parametrize("text", ["99999999999d", "99999999999999999999h"])
def test_parse_since_rejects_durations_too_large_for_a_date(text: str) -> None:
    with pytest.raises(q.InspectError, match="too far"):
        q.parse_since(text, NOW)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_score_bounds_must_be_finite(bad: float) -> None:
    with pytest.raises(q.InspectError, match="finite"):
        q.filter_articles([], min_score=bad)
    with pytest.raises(q.InspectError, match="finite"):
        q.filter_articles([], max_score=bad)


def test_score_bounds_are_inclusive() -> None:
    a = art("a", score=1.0)
    assert q.filter_articles([a], min_score=1.0) == [a]
    assert q.filter_articles([a], max_score=1.0) == [a]


def test_domain_argument_ignores_www_and_case() -> None:
    a = art("a", domain="example.com")
    assert q.filter_articles([a], domains=["www.Example.com"]) == [a]
