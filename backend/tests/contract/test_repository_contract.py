"""Contract tests every ``Repository`` implementation must pass.

Run against the in-memory fake and the SQLite adapter through the fixtures in
``conftest.py``; Phase 2 adds the Firestore adapter there. The tests pin down
the semantics documented on ``newsfeed.ports.Repository``, including details a
backend could otherwise choose freely, such as the tie-break order of
``list_articles`` and ``list_feedback`` (ascending ``id``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from newsfeed.models import (
    Article,
    ArticleExtraction,
    ExtractionStatus,
    Feedback,
    FeedbackEvent,
    PipelineStage,
    RunStats,
    SourceState,
)
from newsfeed.ports import Repository
from tests.contract.conftest import RepositoryOpener, run_threads

T0 = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def make_article(
    n: int,
    *,
    fetched_at: datetime = T0,
    status: ExtractionStatus = ExtractionStatus.PENDING,
) -> Article:
    url = f"https://example.com/story-{n}"
    return Article(
        canonical_url=url,
        url=url,
        source_id="example",
        title=f"Invented headline {n}",
        fetched_at=fetched_at,
        extraction=ArticleExtraction(status=status),
    )


def full_article() -> Article:
    """An article with every optional field set, to check nothing is lost."""
    return Article(
        canonical_url="https://example.com/full",
        url="https://example.com/full?utm_source=feed",
        source_id="example",
        title='Invented headline with ünïcödé and "quotes"',
        author="A. Writer",
        published_at=datetime(2026, 10, 10, 8, 30, 15, 250000, tzinfo=UTC),
        fetched_at=datetime(2026, 10, 11, 12, 0, 0, 123456, tzinfo=UTC),
        summary="An invented summary.",
        lead_image="https://example.com/lead.jpg",
        extraction=ArticleExtraction(
            status=ExtractionStatus.OK, method="trafilatura", error=None, word_count=812
        ),
        content_ref="articles/full.html",
        score=0.734,
        score_breakdown={"freshness": 0.5, "topic": 0.234},
        cluster_id="cluster-7",
    )


def ids(articles: list[Article]) -> list[str]:
    return [a.id for a in articles]


# --- Articles ----------------------------------------------------------------


class TestArticles:
    def test_round_trip_keeps_every_field(self, repo: Repository) -> None:
        article = full_article()
        repo.upsert_article(article)
        assert repo.get_article(article.id) == article

    def test_get_missing_returns_none(self, repo: Repository) -> None:
        assert repo.get_article("0" * 16) is None

    def test_upsert_is_idempotent(self, repo: Repository) -> None:
        article = full_article()
        repo.upsert_article(article)
        repo.upsert_article(article)
        assert repo.list_articles() == [article]

    def test_upsert_replaces_the_whole_article(self, repo: Repository) -> None:
        article = full_article()
        repo.upsert_article(article)
        replaced = make_article(0)
        replaced = Article.model_validate(
            {**replaced.model_dump(), "canonical_url": article.canonical_url, "id": None}
        )
        repo.upsert_article(replaced)
        stored = repo.get_article(article.id)
        assert stored == replaced
        assert stored is not None
        assert stored.author is None
        assert stored.score_breakdown == {}
        assert len(repo.list_articles()) == 1

    def test_returned_articles_are_copies(self, repo: Repository) -> None:
        article = full_article()
        repo.upsert_article(article)
        fetched = repo.get_article(article.id)
        assert fetched is not None
        fetched.title = "Mutated"
        fetched.score_breakdown["topic"] = 99.0
        listed = repo.list_articles()[0]
        listed.extraction.word_count = 1
        assert repo.get_article(article.id) == article

    def test_list_orders_newest_first_with_ascending_id_tiebreak(self, repo: Repository) -> None:
        old = make_article(1, fetched_at=T0 - timedelta(hours=1))
        new = make_article(2, fetched_at=T0 + timedelta(hours=1))
        tied = [make_article(n, fetched_at=T0) for n in range(3, 9)]
        for article in [*tied, old, new]:
            repo.upsert_article(article)
        expected_tied = sorted(a.id for a in tied)
        assert ids(repo.list_articles()) == [new.id, *expected_tied, old.id]

    def test_list_orders_by_instant_not_text(self, repo: Repository) -> None:
        # Sub-second and whole-second times, and an article stored in another
        # timezone, must order by the instant they denote.
        whole = make_article(1, fetched_at=T0)
        fraction = make_article(2, fetched_at=T0 + timedelta(microseconds=500000))
        earlier_offset = make_article(
            3, fetched_at=datetime(2026, 10, 11, 13, 59, 59, tzinfo=timezone(timedelta(hours=2)))
        )
        for article in (earlier_offset, whole, fraction):
            repo.upsert_article(article)
        assert ids(repo.list_articles()) == [fraction.id, whole.id, earlier_offset.id]

    def test_list_filters_by_status(self, repo: Repository) -> None:
        ok = make_article(1, status=ExtractionStatus.OK)
        pending = make_article(2)
        failed = make_article(3, status=ExtractionStatus.FAILED)
        for article in (ok, pending, failed):
            repo.upsert_article(article)
        assert ids(repo.list_articles(status=ExtractionStatus.PENDING)) == [pending.id]
        assert ids(repo.list_articles(status=ExtractionStatus.OK)) == [ok.id]
        assert repo.list_articles(status=ExtractionStatus.PAYWALLED) == []

    def test_list_status_follows_updates(self, repo: Repository) -> None:
        article = make_article(1)
        repo.upsert_article(article)
        done = Article.model_validate(
            {**article.model_dump(), "extraction": {"status": "ok", "word_count": 10}}
        )
        repo.upsert_article(done)
        assert repo.list_articles(status=ExtractionStatus.PENDING) == []
        assert ids(repo.list_articles(status=ExtractionStatus.OK)) == [article.id]

    def test_list_since_is_inclusive(self, repo: Repository) -> None:
        before = make_article(1, fetched_at=T0 - timedelta(microseconds=1))
        at = make_article(2, fetched_at=T0)
        after = make_article(3, fetched_at=T0 + timedelta(seconds=1))
        for article in (before, at, after):
            repo.upsert_article(article)
        assert ids(repo.list_articles(since=T0)) == [after.id, at.id]

    def test_list_since_accepts_other_timezones(self, repo: Repository) -> None:
        article = make_article(1, fetched_at=T0)
        repo.upsert_article(article)
        plus_two = timezone(timedelta(hours=2))
        assert ids(repo.list_articles(since=datetime(2026, 10, 11, 14, 0, tzinfo=plus_two))) == [
            article.id
        ]
        assert repo.list_articles(since=datetime(2026, 10, 11, 14, 0, 1, tzinfo=plus_two)) == []

    def test_list_limit(self, repo: Repository) -> None:
        articles = [make_article(n, fetched_at=T0 + timedelta(minutes=n)) for n in range(5)]
        for article in articles:
            repo.upsert_article(article)
        assert ids(repo.list_articles(limit=2)) == [articles[4].id, articles[3].id]
        assert repo.list_articles(limit=0) == []
        assert len(repo.list_articles(limit=None)) == 5
        assert len(repo.list_articles(limit=50)) == 5

    def test_list_combines_filters(self, repo: Repository) -> None:
        articles = [
            make_article(
                n,
                fetched_at=T0 + timedelta(minutes=n),
                status=ExtractionStatus.PENDING if n % 2 else ExtractionStatus.OK,
            )
            for n in range(10)
        ]
        for article in articles:
            repo.upsert_article(article)
        result = repo.list_articles(
            status=ExtractionStatus.PENDING, since=T0 + timedelta(minutes=3), limit=2
        )
        assert ids(result) == [articles[9].id, articles[7].id]

    def test_list_rejects_naive_since_and_negative_limit(self, repo: Repository) -> None:
        with pytest.raises(ValueError):
            repo.list_articles(since=datetime(2026, 10, 11, 12, 0))
        with pytest.raises(ValueError):
            repo.list_articles(limit=-1)

    def test_delete_article(self, repo: Repository) -> None:
        article = make_article(1)
        other = make_article(2)
        repo.upsert_article(article)
        repo.upsert_article(other)
        assert repo.delete_article(article.id) is True
        assert repo.get_article(article.id) is None
        assert repo.delete_article(article.id) is False
        assert ids(repo.list_articles()) == [other.id]


# --- Source state ------------------------------------------------------------


class TestSourceState:
    def test_missing_returns_none(self, repo: Repository) -> None:
        assert repo.get_source_state("nope") is None

    def test_round_trip_and_replace(self, repo: Repository) -> None:
        state = SourceState(
            source_id="example",
            etag='W/"abc"',
            last_modified="Sat, 11 Oct 2026 12:00:00 GMT",
            last_fetched_at=T0,
            last_status=200,
        )
        repo.save_source_state(state)
        assert repo.get_source_state("example") == state
        cleared = SourceState(source_id="example", last_status=None)
        repo.save_source_state(cleared)
        assert repo.get_source_state("example") == cleared

    def test_returned_state_is_a_copy(self, repo: Repository) -> None:
        state = SourceState(source_id="example", etag="1")
        repo.save_source_state(state)
        fetched = repo.get_source_state("example")
        assert fetched is not None
        fetched.etag = "2"
        assert repo.get_source_state("example") == state


# --- Feedback ----------------------------------------------------------------


def make_feedback(
    fid: str, created_at: datetime, event: FeedbackEvent = FeedbackEvent.OPEN
) -> Feedback:
    return Feedback(id=fid, article_id="a" * 16, event=event, created_at=created_at)


class TestFeedback:
    def test_empty(self, repo: Repository) -> None:
        assert repo.list_feedback() == []

    def test_round_trip_and_replace_by_id(self, repo: Repository) -> None:
        feedback = Feedback(
            id="f1", article_id="a" * 16, event=FeedbackEvent.READ, value=42.5, created_at=T0
        )
        repo.save_feedback(feedback)
        assert repo.list_feedback() == [feedback]
        replaced = make_feedback("f1", T0, FeedbackEvent.LIKE)
        repo.save_feedback(replaced)
        assert repo.list_feedback() == [replaced]

    def test_oldest_first_with_ascending_id_tiebreak(self, repo: Repository) -> None:
        late = make_feedback("a-late", T0 + timedelta(seconds=1))
        early = make_feedback("z-early", T0 - timedelta(seconds=1))
        tied = [make_feedback(fid, T0) for fid in ("m", "c", "x", "b")]
        for item in [late, *tied, early]:
            repo.save_feedback(item)
        assert [f.id for f in repo.list_feedback()] == ["z-early", "b", "c", "m", "x", "a-late"]

    def test_since_is_inclusive_and_timezone_aware(self, repo: Repository) -> None:
        for item in (
            make_feedback("before", T0 - timedelta(microseconds=1)),
            make_feedback("at", T0),
            make_feedback("after", T0 + timedelta(microseconds=1)),
        ):
            repo.save_feedback(item)
        assert [f.id for f in repo.list_feedback(since=T0)] == ["at", "after"]
        minus_five = timezone(timedelta(hours=-5))
        since = datetime(2026, 10, 11, 7, 0, tzinfo=minus_five)
        assert [f.id for f in repo.list_feedback(since=since)] == ["at", "after"]
        with pytest.raises(ValueError):
            repo.list_feedback(since=datetime(2026, 10, 11, 12, 0))


# --- Run stats ---------------------------------------------------------------


class TestRunStats:
    def test_missing_returns_none(self, repo: Repository) -> None:
        assert repo.get_run_stats("nope") is None

    def test_saved_at_start_and_again_at_finish(self, repo: Repository) -> None:
        started = RunStats(run_id="run-1", started_at=T0)
        repo.save_run_stats(started)
        assert repo.get_run_stats("run-1") == started
        finished = RunStats(
            run_id="run-1",
            started_at=T0,
            finished_at=T0 + timedelta(minutes=3, microseconds=7),
            stage_counts={
                PipelineStage.INGEST: {"new": 12, "seen": 40},
                PipelineStage.EXTRACT: {"ok": 10, "failed": 2},
            },
            extraction_by_domain={
                "example.com": {ExtractionStatus.OK: 9, ExtractionStatus.FAILED: 1},
                "example.org": {ExtractionStatus.PAYWALLED: 1},
            },
            error_count=3,
        )
        repo.save_run_stats(finished)
        assert repo.get_run_stats("run-1") == finished
        assert repo.get_run_stats("run-2") is None


# --- Persistence and concurrency ---------------------------------------------


def test_data_persists_across_reopening(open_repository: RepositoryOpener) -> None:
    first = open_repository()
    article = full_article()
    state = SourceState(source_id="example", etag="1", last_fetched_at=T0)
    feedback = make_feedback("f1", T0)
    stats = RunStats(run_id="run-1", started_at=T0, error_count=1)
    first.upsert_article(article)
    first.save_source_state(state)
    first.save_feedback(feedback)
    first.save_run_stats(stats)

    second = open_repository()
    assert second.get_article(article.id) == article
    assert second.list_articles() == [article]
    assert second.get_source_state("example") == state
    assert second.list_feedback() == [feedback]
    assert second.get_run_stats("run-1") == stats


def test_concurrent_writes_from_eight_threads(repo: Repository) -> None:
    per_thread = 25

    def worker(i: int) -> None:
        for n in range(per_thread):
            article = make_article(i * 1000 + n, fetched_at=T0 + timedelta(seconds=n))
            repo.upsert_article(article)
            # Everyone also rewrites one shared article, to contend on one row.
            repo.upsert_article(make_article(-1))
            repo.save_feedback(make_feedback(f"t{i}-{n}", T0))
            repo.save_source_state(SourceState(source_id=f"source-{i}", last_status=200 + n))
            repo.save_run_stats(RunStats(run_id="shared-run", started_at=T0, error_count=n))
            repo.list_articles(limit=5)

    run_threads(worker)
    assert len(repo.list_articles()) == 8 * per_thread + 1
    assert len(repo.list_feedback()) == 8 * per_thread
    for i in range(8):
        state = repo.get_source_state(f"source-{i}")
        assert state is not None
        assert state.last_status == 200 + per_thread - 1
    assert repo.get_run_stats("shared-run") is not None
