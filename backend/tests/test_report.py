"""Tests for ``newsfeed.report``, against the in-memory repository."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from newsfeed.models import (
    Article,
    ArticleExtraction,
    ExtractionStatus,
    RunStats,
    SourceState,
)
from newsfeed.report import UnknownRunError, build_report, render_text
from tests.fakes import InMemoryRepository

T0 = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def make_run(run_id: str, hours: int, by_domain: dict[str, Any], **kwargs: Any) -> RunStats:
    start = T0 + timedelta(hours=hours)
    return RunStats(
        run_id=run_id,
        started_at=start,
        finished_at=start + timedelta(minutes=10),
        extraction_by_domain=by_domain,
        **kwargs,
    )


def article(
    slug: str,
    status: ExtractionStatus,
    *,
    hours: int,
    domain: str = "a.example.com",
    error: str | None = None,
    error_type: str | None = None,
) -> Article:
    return Article(
        canonical_url=f"https://{domain}/{slug}",
        url=f"https://{domain}/{slug}",
        source_id="s",
        title=slug,
        fetched_at=T0,
        extraction=ArticleExtraction(
            status=status,
            error=error,
            error_type=error_type,
            last_attempt_at=T0 + timedelta(hours=hours, minutes=5),
        ),
    )


@pytest.fixture
def repo() -> InMemoryRepository:
    repo = InMemoryRepository()
    repo.save_run_stats(
        make_run(
            "r1",
            0,
            {"a.example.com": {"ok": 1, "failed": 1}},
            median_word_count_by_domain={"a.example.com": 300},
        )
    )
    repo.save_run_stats(
        make_run(
            "r2", 1, {"a.example.com": {"ok": 3, "failed": 1}, "b.example.org": {"partial": 1}}
        )
    )
    repo.upsert_article(
        article("old", ExtractionStatus.FAILED, hours=0, error="HTTP 403 from a.example.com")
    )
    repo.upsert_article(
        article("new", ExtractionStatus.FAILED, hours=1, error="HTTP 403 from a.example.com")
    )
    repo.upsert_article(
        article("slow", ExtractionStatus.FAILED, hours=1, error_type="timeout", error="x")
    )
    repo.upsert_article(
        article(
            "cut",
            ExtractionStatus.PARTIAL,
            hours=1,
            domain="b.example.org",
            error="only 20 words",
        )
    )
    repo.upsert_article(article("fine", ExtractionStatus.OK, hours=1))
    return repo


def test_latest_run_is_the_default(repo: InMemoryRepository) -> None:
    report = build_report(repo, [])

    assert report.run_ids == ["r2"]
    by_domain = {d.domain: d for d in report.domains}
    assert by_domain["a.example.com"].ok_rate == 0.75
    assert by_domain["b.example.org"].counts == {"partial": 1}
    assert report.trend == []


def test_failures_are_grouped_by_domain_and_reason_for_the_chosen_run(
    repo: InMemoryRepository,
) -> None:
    report = build_report(repo, [])

    groups = {(g.domain, g.reason, g.status): g for g in report.failures}
    assert set(groups) == {
        ("a.example.com", "http_403", "failed"),
        ("a.example.com", "timeout", "failed"),
        ("b.example.org", "too_short", "partial"),
    }
    assert groups[("a.example.com", "http_403", "failed")].urls == ["https://a.example.com/new"]


def test_run_option_selects_an_older_run(repo: InMemoryRepository) -> None:
    report = build_report(repo, [], run_id="r1")

    assert report.run_ids == ["r1"]
    assert report.domains[0].median_word_count == 300
    assert [g.urls for g in report.failures] == [["https://a.example.com/old"]]


def test_since_aggregates_runs_and_adds_a_trend(repo: InMemoryRepository) -> None:
    report = build_report(repo, [], since=T0)

    assert report.run_ids == ["r2", "r1"]
    a = next(d for d in report.domains if d.domain == "a.example.com")
    assert (a.total, a.counts["ok"]) == (6, 4)
    assert [t.run_id for t in report.trend] == ["r1", "r2"]
    assert report.trend[0].ok_rate_by_domain == {"a.example.com": 0.5}


def test_unknown_run_raises(repo: InMemoryRepository) -> None:
    with pytest.raises(UnknownRunError):
        build_report(repo, [], run_id="nope")


def test_failing_feeds_are_sorted_by_consecutive_failures(repo: InMemoryRepository) -> None:
    repo.save_source_state(SourceState(source_id="healthy"))
    repo.save_source_state(
        SourceState(source_id="flaky", consecutive_failures=2, last_error="HTTP 500")
    )
    repo.save_source_state(
        SourceState(source_id="dead", consecutive_failures=7, last_error="timeout")
    )

    report = build_report(repo, ["healthy", "flaky", "dead", "unseen"])

    assert [(f.source_id, f.consecutive_failures) for f in report.feeds] == [
        ("dead", 7),
        ("flaky", 2),
    ]
    assert "dead: 7 consecutive failure(s), last error: timeout" in render_text(report)


def test_empty_repository_reports_no_runs() -> None:
    report = build_report(InMemoryRepository(), [])

    assert report.run_ids == []
    assert "No runs found." in render_text(report)


def test_text_lists_urls_but_no_article_text(repo: InMemoryRepository) -> None:
    text = render_text(build_report(repo, []))

    assert "a.example.com: http_403 (failed) x1" in text
    assert "https://a.example.com/new" in text
