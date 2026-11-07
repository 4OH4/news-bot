"""Tests for ``newsfeed.report``, against the in-memory repository."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from newsfeed import cli
from newsfeed.adapters.sqlite_repository import SqliteRepository
from newsfeed.models import (
    Article,
    ArticleExtraction,
    ExtractionStatus,
    Feedback,
    FeedbackEvent,
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


# --- Feedback section (issue #185) -------------------------------------------
#
# These run `newsfeed report` against a real SQLite database. The issue names no
# field names for FeedbackSummary, so they check the text a reader sees.

runner = CliRunner()
MARK = "Zebra"  # in every seeded title, so lines that show a title can be told apart


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = tmp_path / "config"
    config.mkdir()
    (config / "sources.yaml").write_text("sources: []\n")
    (config / "url_params.yaml").write_text("domains: {}\n")
    (config / "extraction.yaml").write_text("domains: {}\n")
    data = tmp_path / "data"
    monkeypatch.setenv("NEWSFEED_CONFIG_DIR", str(config))
    monkeypatch.setenv("NEWSFEED_DATA_DIR", str(data))
    return data


def fb_article(slug: str, source_id: str = "fbsource") -> Article:
    return Article(
        canonical_url=f"https://fb.example.com/{slug}",
        url=f"https://fb.example.com/{slug}",
        source_id=source_id,
        title=f"{MARK} headline {slug}",
        fetched_at=T0,
    )


def seed(data: Path, articles: list[Article], events: list[Feedback]) -> None:
    with SqliteRepository(data / "newsfeed.db") as repo:
        for a in articles:
            repo.upsert_article(a)
        for e in events:
            repo.save_feedback(e)


def event(
    a: Article | str, kind: FeedbackEvent, ago: timedelta, value: float | None = None
) -> Feedback:
    article_id = a if isinstance(a, str) else a.id
    return Feedback(
        article_id=article_id,
        event=kind,
        value=value,
        created_at=datetime.now(UTC) - ago,
    )


def feedback_output(data: Path) -> str:
    result = runner.invoke(cli.app, ["report"])
    assert result.exit_code == 0, result.output
    assert "Feedback" in result.output
    return result.output


def day_lines(output: str, day: datetime) -> list[str]:
    """Lines that name ``day`` and show no article title: the daily counts."""
    stamp = f"{day:%Y-%m-%d}"
    return [ln for ln in output.splitlines() if stamp in ln and MARK not in ln]


def numbers_besides_date(line: str) -> list[str]:
    return re.findall(r"\d+", re.sub(r"\d{4}-\d{2}-\d{2}", "", line))


def test_report_says_there_is_no_feedback_when_none_is_stored(data_dir: Path) -> None:
    out = feedback_output(data_dir)

    section = out[out.index("Feedback") :]
    assert "none" in section.lower()


def test_a_day_without_events_between_two_active_days_shows_zero(data_dir: Path) -> None:
    a = fb_article("one")
    seed(
        data_dir,
        [a],
        [
            event(a, FeedbackEvent.OPEN, timedelta(days=2, minutes=1)),
            event(a, FeedbackEvent.OPEN, timedelta(minutes=1)),
        ],
    )

    out = feedback_output(data_dir)

    middle = day_lines(out, datetime.now(UTC) - timedelta(days=1))
    assert middle, "the empty middle day is missing from the daily counts"
    assert set(numbers_besides_date(middle[0])) <= {"0"}
    busy = day_lines(out, datetime.now(UTC) - timedelta(days=2, minutes=1))
    assert "1" in numbers_besides_date(busy[0])


def test_feedback_for_a_missing_article_shows_its_id(data_dir: Path) -> None:
    seed(data_dir, [], [event("ghost0123456789a", FeedbackEvent.LIKE, timedelta(minutes=1), 1)])

    out = feedback_output(data_dir)

    assert "ghost0123456789a" in out


def test_two_mutes_on_one_source_are_listed_with_a_count_of_two(data_dir: Path) -> None:
    a, b = fb_article("m1", "noisysite"), fb_article("m2", "noisysite")
    seed(
        data_dir,
        [a, b],
        [
            event(a, FeedbackEvent.MUTE_SOURCE, timedelta(minutes=5), 1),
            event(b, FeedbackEvent.MUTE_SOURCE, timedelta(minutes=4), 1),
        ],
    )

    out = feedback_output(data_dir)

    lines = [ln for ln in out.splitlines() if "noisysite" in ln]
    assert len(lines) == 1
    assert "2" in re.findall(r"\d+", lines[0])


def test_a_source_unmuted_by_a_later_zero_event_is_not_listed(data_dir: Path) -> None:
    a, b = fb_article("u1", "calmsite"), fb_article("u2", "kept-muted")
    seed(
        data_dir,
        [a, b],
        [
            event(a, FeedbackEvent.MUTE_SOURCE, timedelta(minutes=5), 1),
            event(a, FeedbackEvent.MUTE_SOURCE, timedelta(minutes=4), 0),
            event(b, FeedbackEvent.MUTE_SOURCE, timedelta(minutes=3), 1),
        ],
    )

    out = feedback_output(data_dir)

    assert "calmsite" not in out
    assert "kept-muted" in out


def test_muting_an_unknown_article_counts_under_unknown(data_dir: Path) -> None:
    seed(
        data_dir,
        [],
        [event("ghost9876543210b", FeedbackEvent.MUTE_SOURCE, timedelta(minutes=2), 1)],
    )

    out = feedback_output(data_dir)

    assert any("unknown" in ln.lower() and "1" in ln for ln in out.splitlines())


def test_a_dismiss_undone_the_same_day_leaves_no_dismissals_in_the_day(data_dir: Path) -> None:
    a = fb_article("d1")
    seed(
        data_dir,
        [a],
        [
            event(a, FeedbackEvent.DISMISS, timedelta(minutes=2), 1),
            event(a, FeedbackEvent.DISMISS, timedelta(minutes=1), 0),
        ],
    )

    out = feedback_output(data_dir)

    today = day_lines(out, datetime.now(UTC) - timedelta(minutes=2))
    assert today, "today is missing from the daily counts"
    assert set(numbers_besides_date(today[0])) <= {"0"}
    # The Recent list still shows the undo as it was stored.
    assert any(MARK in ln and "dismiss" in ln for ln in out.splitlines())


def test_feedback_written_by_the_real_repository_appears_in_the_report(data_dir: Path) -> None:
    a = fb_article("real")
    seed(data_dir, [a], [event(a, FeedbackEvent.LIKE, timedelta(minutes=1), 1)])
    with SqliteRepository(data_dir / "newsfeed.db") as repo:
        assert [f.event for f in repo.list_feedback()] == [FeedbackEvent.LIKE]

    out = feedback_output(data_dir)

    assert any(a.title in ln and "like" in ln for ln in out.splitlines())


def test_only_the_recent_ten_events_cause_article_reads(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    articles = [fb_article(f"bulk{i:02d}") for i in range(25)]
    seed(
        data_dir,
        articles,
        [event(a, FeedbackEvent.OPEN, timedelta(minutes=60 - i)) for i, a in enumerate(articles)],
    )
    reads: list[str] = []
    real_open = cli.open_storage

    def counting_open(settings: Any) -> Any:
        repo, blobs = real_open(settings)
        original = repo.get_article

        def get_article(article_id: str) -> Article | None:
            reads.append(article_id)
            return original(article_id)

        repo.get_article = get_article  # type: ignore[method-assign]
        return repo, blobs

    monkeypatch.setattr(cli, "open_storage", counting_open)

    out = feedback_output(data_dir)

    assert len(set(reads)) == 10
    assert set(reads) == {a.id for a in articles[-10:]}
    assert articles[-1].title in out
    assert articles[0].title not in out


def test_summarise_feedback_is_pure_and_counts_per_day() -> None:
    from newsfeed.report import FeedbackSummary, summarise_feedback

    now = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    a = fb_article("pure")
    events = [
        Feedback(article_id=a.id, event=FeedbackEvent.OPEN, created_at=now - timedelta(hours=1)),
        Feedback(
            article_id=a.id,
            event=FeedbackEvent.DISMISS,
            value=1,
            created_at=now - timedelta(hours=2),
        ),
        Feedback(
            article_id=a.id,
            event=FeedbackEvent.DISMISS,
            value=0,
            created_at=now - timedelta(hours=3),
        ),
    ]
    before = [e.model_copy() for e in events]

    first = summarise_feedback(events, {a.id: a}, now=now)
    second = summarise_feedback(events, {a.id: a}, now=now)

    assert isinstance(first, FeedbackSummary)
    assert first == second
    assert events == before
    assert summarise_feedback([], {}, now=now) != first


# --- #240: --since also limits the feedback section ---------------------------


def test_since_limits_the_feedback_section_to_events_from_that_date(data_dir: Path) -> None:
    old, new = fb_article("lastweek"), fb_article("today")
    seed(
        data_dir,
        [old, new],
        [
            event(old, FeedbackEvent.LIKE, timedelta(days=7), 1),
            event(new, FeedbackEvent.LIKE, timedelta(minutes=1), 1),
        ],
    )
    since = (datetime.now(UTC) - timedelta(days=2)).strftime("%Y-%m-%d")

    result = runner.invoke(cli.app, ["report", "--since", since])

    assert result.exit_code == 0, result.output
    out = result.output
    assert new.title in out
    assert old.title not in out
    week_ago = day_lines(out, datetime.now(UTC) - timedelta(days=7))
    assert all(set(numbers_besides_date(ln)) <= {"0"} for ln in week_ago)
    today = day_lines(out, datetime.now(UTC) - timedelta(minutes=1))
    assert "1" in numbers_besides_date(today[0])


def test_since_with_only_older_feedback_reports_no_feedback(data_dir: Path) -> None:
    old = fb_article("ancient")
    seed(data_dir, [old], [event(old, FeedbackEvent.LIKE, timedelta(days=7), 1)])
    since = (datetime.now(UTC) - timedelta(days=2)).strftime("%Y-%m-%d")

    result = runner.invoke(cli.app, ["report", "--since", since])

    assert result.exit_code == 0, result.output
    section = result.output[result.output.index("Feedback") :]
    assert "none" in section.lower()
    assert old.title not in result.output


def test_build_report_since_counts_only_feedback_from_that_time() -> None:
    now = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    repo = InMemoryRepository()
    for slug, ago in (("a", timedelta(days=7)), ("b", timedelta(hours=1))):
        item = fb_article(slug)
        repo.upsert_article(item)
        repo.save_feedback(
            Feedback(article_id=item.id, event=FeedbackEvent.OPEN, created_at=now - ago)
        )

    report = build_report(repo, [], since=now - timedelta(days=1), now=now)

    assert report.feedback is not None
    assert report.feedback.total == 1
    assert sum(d.counts["open"] for d in report.feedback.days) == 1
