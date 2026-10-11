"""Tests for the in-memory fakes in ``tests/fakes.py``.

Behaviour shared with the real adapters (idempotent upserts, persistence across
reopening, concurrency) is covered by the contract tests in #8; these tests pin
down the port semantics the fakes implement and their test helpers.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from newsfeed.models import (
    Article,
    ArticleExtraction,
    ExtractionResult,
    ExtractionStatus,
    Feedback,
    FeedbackEvent,
    PipelineStage,
    RunStats,
    Source,
    SourceState,
    SourceType,
)
from newsfeed.ports import BlobNotFoundError, FetchNotAllowedError
from tests.fakes import (
    FakeExtractor,
    FakeFetcher,
    FakeResponse,
    FakeSourceIngester,
    FetchCall,
    InMemoryBlobStore,
    InMemoryRepository,
)

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


# --- InMemoryRepository ------------------------------------------------------


class TestInMemoryRepository:
    def test_upsert_and_get(self) -> None:
        repo = InMemoryRepository()
        article = make_article(1)
        repo.upsert_article(article)
        assert repo.get_article(article.id) == article
        assert repo.get_article("0" * 16) is None

    def test_upsert_replaces_and_is_idempotent(self) -> None:
        repo = InMemoryRepository()
        article = make_article(1)
        repo.upsert_article(article)
        repo.upsert_article(article)
        updated = Article.model_validate({**article.model_dump(), "title": "Changed"})
        repo.upsert_article(updated)
        assert len(repo.list_articles()) == 1
        stored = repo.get_article(article.id)
        assert stored is not None
        assert stored.title == "Changed"

    def test_returned_articles_are_copies(self) -> None:
        repo = InMemoryRepository()
        article = make_article(1)
        repo.upsert_article(article)
        article.title = "Mutated after upsert"
        fetched = repo.get_article(article.id)
        assert fetched is not None
        assert fetched.title == "Invented headline 1"
        fetched.extraction.status = ExtractionStatus.OK
        again = repo.get_article(article.id)
        assert again is not None
        assert again.extraction.status is ExtractionStatus.PENDING

    def test_list_orders_newest_first_with_id_tiebreak(self) -> None:
        repo = InMemoryRepository()
        old = make_article(1, fetched_at=T0 - timedelta(hours=1))
        tied = [make_article(n) for n in (2, 3, 4)]
        for article in [old, *tied]:
            repo.upsert_article(article)
        ids = [a.id for a in repo.list_articles()]
        assert ids == [*sorted(a.id for a in tied), old.id]

    def test_list_filters_and_limit(self) -> None:
        repo = InMemoryRepository()
        repo.upsert_article(make_article(1, fetched_at=T0 - timedelta(days=2)))
        repo.upsert_article(make_article(2, fetched_at=T0, status=ExtractionStatus.OK))
        repo.upsert_article(make_article(3, fetched_at=T0 + timedelta(hours=1)))
        repo.upsert_article(make_article(4, fetched_at=T0 + timedelta(hours=2)))

        pending = repo.list_articles(status=ExtractionStatus.PENDING)
        assert [a.title for a in pending] == [
            "Invented headline 4",
            "Invented headline 3",
            "Invented headline 1",
        ]
        recent = repo.list_articles(since=T0)  # inclusive
        assert {a.title[-1] for a in recent} == {"2", "3", "4"}
        both = repo.list_articles(status=ExtractionStatus.PENDING, since=T0, limit=1)
        assert [a.title for a in both] == ["Invented headline 4"]
        assert repo.list_articles(limit=0) == []

    def test_list_since_accepts_other_timezones(self) -> None:
        repo = InMemoryRepository()
        repo.upsert_article(make_article(1, fetched_at=T0))
        plus_one = timezone(timedelta(hours=1))
        assert len(repo.list_articles(since=datetime(2026, 10, 11, 13, 0, tzinfo=plus_one))) == 1
        assert repo.list_articles(since=datetime(2026, 10, 11, 13, 1, tzinfo=plus_one)) == []

    def test_list_rejects_naive_since_and_negative_limit(self) -> None:
        repo = InMemoryRepository()
        with pytest.raises(ValueError, match="timezone-aware"):
            repo.list_articles(since=datetime(2026, 10, 11))
        with pytest.raises(ValueError, match="non-negative"):
            repo.list_articles(limit=-1)

    def test_delete_article(self) -> None:
        repo = InMemoryRepository()
        article = make_article(1)
        repo.upsert_article(article)
        assert repo.delete_article(article.id) is True
        assert repo.get_article(article.id) is None
        assert repo.delete_article(article.id) is False

    def test_source_state_round_trip(self) -> None:
        repo = InMemoryRepository()
        assert repo.get_source_state("example") is None
        state = SourceState(source_id="example", etag='"v1"', last_fetched_at=T0, last_status=200)
        repo.save_source_state(state)
        assert repo.get_source_state("example") == state
        newer = SourceState(source_id="example", etag='"v2"')
        repo.save_source_state(newer)
        assert repo.get_source_state("example") == newer

    def test_feedback_since_and_order(self) -> None:
        repo = InMemoryRepository()
        events = [
            Feedback(id=f"f{n}", article_id="a" * 16, event=FeedbackEvent.OPEN, created_at=t)
            for n, t in enumerate([T0 + timedelta(minutes=5), T0 - timedelta(days=1), T0])
        ]
        for event in events:
            repo.save_feedback(event)
        repo.save_feedback(events[0])  # replaces by id, no duplicate
        assert [f.id for f in repo.list_feedback()] == ["f1", "f2", "f0"]
        assert [f.id for f in repo.list_feedback(T0)] == ["f2", "f0"]
        with pytest.raises(ValueError, match="timezone-aware"):
            repo.list_feedback(datetime(2026, 1, 1))

    def test_run_stats_upsert_by_run_id(self) -> None:
        repo = InMemoryRepository()
        assert repo.get_run_stats("run-1") is None
        started = RunStats(run_id="run-1", started_at=T0)
        repo.save_run_stats(started)
        assert repo.get_run_stats("run-1") == started
        finished = RunStats(
            run_id="run-1",
            started_at=T0,
            finished_at=T0 + timedelta(minutes=3),
            stage_counts={PipelineStage.INGEST: {"new": 3}},
        )
        repo.save_run_stats(finished)
        assert repo.get_run_stats("run-1") == finished

    def test_concurrent_upserts(self) -> None:
        repo = InMemoryRepository()

        def worker(offset: int) -> None:
            for n in range(50):
                repo.upsert_article(make_article(offset * 100 + n))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len(repo.list_articles()) == 400


# --- InMemoryBlobStore -------------------------------------------------------


class TestInMemoryBlobStore:
    def test_put_get_exists(self) -> None:
        store = InMemoryBlobStore()
        ref = store.put("articles/abc.html", b"<p>Invented</p>", "text/html")
        assert store.exists(ref)
        assert store.get(ref) == b"<p>Invented</p>"
        assert store.content_type(ref) == "text/html"
        assert store.keys() == ["articles/abc.html"]

    def test_ref_is_not_the_key(self) -> None:
        store = InMemoryBlobStore()
        ref = store.put("feed.json", b"{}")
        assert ref != "feed.json"
        assert not store.exists("feed.json")
        assert store.content_type(ref) == "application/octet-stream"

    def test_put_overwrites_with_stable_ref(self) -> None:
        store = InMemoryBlobStore()
        first = store.put("feed.json", b"1")
        second = store.put("feed.json", b"2")
        assert first == second
        assert store.get(second) == b"2"

    def test_missing_blob(self) -> None:
        store = InMemoryBlobStore()
        assert not store.exists("memory://nope")
        with pytest.raises(BlobNotFoundError):
            store.get("memory://nope")
        with pytest.raises(LookupError):  # BlobNotFoundError is a LookupError
            store.content_type("memory://nope")

    @pytest.mark.parametrize("key", ["../x", "/etc/passwd", "a/../../b", ""])
    def test_rejects_invalid_keys(self, key: str) -> None:
        with pytest.raises(ValueError, match="invalid blob key"):
            InMemoryBlobStore().put(key, b"x")


# --- FakeFetcher -------------------------------------------------------------


class TestFakeFetcher:
    def test_serves_canned_responses_from_dict(self) -> None:
        fetcher = FakeFetcher(
            {
                "https://example.com/a": "<html>Invented</html>",
                "https://example.com/b": FakeResponse(
                    body=b"bytes", headers={"Content-Type": "text/plain"}
                ),
            }
        )
        a = fetcher.fetch("https://example.com/a")
        assert (a.status_code, a.content, a.error) == (200, b"<html>Invented</html>", None)
        assert a.final_url == "https://example.com/a"
        b = fetcher.fetch("https://example.com/b")
        assert b.headers == {"content-type": "text/plain"}
        assert fetcher.calls == [
            FetchCall("https://example.com/a", None, None),
            FetchCall("https://example.com/b", None, None),
        ]
        assert fetcher.fetched_urls() == ["https://example.com/a", "https://example.com/b"]

    def test_unknown_url_is_404(self) -> None:
        result = FakeFetcher().fetch("https://example.com/missing")
        assert result.status_code == 404
        assert result.error is not None
        assert result.content == b""

    def test_etag_conditional_get(self) -> None:
        fetcher = FakeFetcher()
        fetcher.add("https://example.com/feed", b"<rss/>", etag='"v1"')
        fresh = fetcher.fetch("https://example.com/feed")
        assert fresh.status_code == 200
        assert fresh.headers["etag"] == '"v1"'
        unchanged = fetcher.fetch("https://example.com/feed", etag='"v1"')
        assert unchanged.not_modified
        assert unchanged.status_code == 304
        assert unchanged.content == b""
        assert unchanged.error is None
        changed = fetcher.fetch("https://example.com/feed", etag='"v0"')
        assert not changed.not_modified
        assert changed.content == b"<rss/>"
        assert fetcher.calls[1] == FetchCall("https://example.com/feed", '"v1"', None)

    def test_last_modified_conditional_get(self) -> None:
        stamp = "Sun, 11 Oct 2026 12:00:00 GMT"
        fetcher = FakeFetcher()
        fetcher.add("https://example.com/feed", b"<rss/>", last_modified=stamp)
        assert fetcher.fetch("https://example.com/feed").headers["last-modified"] == stamp
        assert fetcher.fetch("https://example.com/feed", last_modified=stamp).not_modified
        older = "Sat, 10 Oct 2026 12:00:00 GMT"
        assert not fetcher.fetch("https://example.com/feed", last_modified=older).not_modified

    def test_etag_takes_precedence_over_last_modified(self) -> None:
        stamp = "Sun, 11 Oct 2026 12:00:00 GMT"
        fetcher = FakeFetcher()
        fetcher.add("https://example.com/feed", b"x", etag='"v2"', last_modified=stamp)
        result = fetcher.fetch("https://example.com/feed", etag='"v1"', last_modified=stamp)
        assert not result.not_modified

    def test_no_validators_means_no_304(self) -> None:
        fetcher = FakeFetcher({"https://example.com/feed": b"x"})
        assert not fetcher.fetch("https://example.com/feed", etag='"v1"').not_modified

    def test_errors_redirects_and_status(self) -> None:
        fetcher = FakeFetcher()
        fetcher.add("https://example.com/down", status=None, error="connection refused")
        fetcher.add("https://example.com/gone", b"", status=410)
        fetcher.add("https://example.com/old", b"moved", final_url="https://example.com/new")
        down = fetcher.fetch("https://example.com/down")
        assert (down.status_code, down.error) == (None, "connection refused")
        gone = fetcher.fetch("https://example.com/gone")
        assert (gone.status_code, gone.error) == (410, "HTTP 410")
        assert fetcher.fetch("https://example.com/old").final_url == "https://example.com/new"

    def test_robots_disallow(self) -> None:
        fetcher = FakeFetcher(
            {"https://example.com/private/x": b"secret"},
            disallowed=["https://example.com/private/"],
        )
        assert fetcher.is_allowed("https://example.com/public")
        assert not fetcher.is_allowed("https://example.com/private/x")
        result = fetcher.fetch("https://example.com/private/x")
        assert result.status_code is None
        assert result.error == "disallowed by robots.txt"
        assert result.content == b""

    def test_permit(self) -> None:
        fetcher = FakeFetcher(disallowed=["https://blocked.example/"])
        entered = False
        with fetcher.permit("https://example.com/page"):
            entered = True
        assert entered
        with pytest.raises(FetchNotAllowedError), fetcher.permit("https://blocked.example/p"):
            pytest.fail("body must not run when disallowed")
        assert fetcher.permits == ["https://example.com/page", "https://blocked.example/p"]

    def test_add_file(self, tmp_path: Path) -> None:
        (tmp_path / "feeds").mkdir()
        (tmp_path / "feeds" / "main.xml").write_bytes(b"<rss>invented</rss>")
        fetcher = FakeFetcher(fixtures_dir=tmp_path)
        fetcher.add_file("https://example.com/rss", "feeds/main.xml", etag='"e"')
        fetcher.add_file(
            "https://example.com/abs",
            tmp_path / "feeds" / "main.xml",
            headers={"Content-Type": "application/rss+xml"},
        )
        result = fetcher.fetch("https://example.com/rss")
        assert result.content == b"<rss>invented</rss>"
        assert result.headers["content-type"] == "application/xml"
        assert fetcher.fetch("https://example.com/rss", etag='"e"').not_modified
        abs_result = fetcher.fetch("https://example.com/abs")
        assert abs_result.headers["content-type"] == "application/rss+xml"

    def test_add_file_relative_needs_fixtures_dir(self) -> None:
        with pytest.raises(ValueError, match="fixtures_dir"):
            FakeFetcher().add_file("https://example.com/x", "feeds/main.xml")

    def test_fixture_directory_by_host_and_path(self, tmp_path: Path) -> None:
        site = tmp_path / "news.example"
        (site / "2026" / "story").mkdir(parents=True)
        (site / "index.html").write_text("<html>home</html>")
        (site / "2026" / "story" / "index.html").write_text("<html>story</html>")
        (site / "feed.xml").write_text("<rss/>")
        (tmp_path / "secret.txt").write_text("outside")
        fetcher = FakeFetcher(fixtures_dir=tmp_path)

        home = fetcher.fetch("https://news.example/")
        assert home.content == b"<html>home</html>"
        assert home.headers["content-type"] == "text/html"
        assert fetcher.fetch("https://news.example").content == b"<html>home</html>"
        assert fetcher.fetch("https://news.example/2026/story/").content == b"<html>story</html>"
        assert fetcher.fetch("https://news.example/feed.xml").content == b"<rss/>"
        assert fetcher.fetch("https://news.example/feed.xml?page=2").status_code == 404
        assert fetcher.fetch("https://news.example/missing.html").status_code == 404
        assert fetcher.fetch("https://news.example/../secret.txt").status_code == 404

    def test_dict_entry_overrides_fixture_directory(self, tmp_path: Path) -> None:
        (tmp_path / "example.com").mkdir()
        (tmp_path / "example.com" / "a").write_text("from file")
        fetcher = FakeFetcher({"https://example.com/a": "from dict"}, fixtures_dir=tmp_path)
        assert fetcher.fetch("https://example.com/a").content == b"from dict"


# --- FakeExtractor -----------------------------------------------------------


class TestFakeExtractor:
    def test_canned_and_default_results(self) -> None:
        ok = ExtractionResult(status=ExtractionStatus.OK, word_count=300)
        extractor = FakeExtractor("stub", {"https://example.com/a": ok})
        a = extractor.extract("<html/>", "https://example.com/a")
        assert a.status is ExtractionStatus.OK
        assert a.method == "stub"
        missing = extractor.extract("<p/>", "https://example.com/b")
        assert missing.status is ExtractionStatus.FAILED
        assert missing.method == "stub"
        assert extractor.calls == [
            ("https://example.com/a", "<html/>"),
            ("https://example.com/b", "<p/>"),
        ]

    def test_default_result_and_explicit_method(self) -> None:
        partial = ExtractionResult(status=ExtractionStatus.PARTIAL, method="other", word_count=5)
        extractor = FakeExtractor(default=partial)
        result = extractor.extract("<p/>", "https://example.com/x")
        assert result == partial
        assert result is not partial
        assert extractor.name == "fake"


# --- FakeSourceIngester ------------------------------------------------------


class TestFakeSourceIngester:
    def test_returns_articles_and_updated_state(self) -> None:
        source = Source(id="example", type=SourceType.RSS, url="https://example.com/feed")
        articles = [make_article(1), make_article(2)]
        ingester = FakeSourceIngester(articles={"example": articles}, clock=lambda: T0)
        state = SourceState(source_id="example", etag='"v1"')
        got, new_state = ingester.ingest(source, FakeFetcher(), state)
        assert got == articles
        assert new_state == SourceState(
            source_id="example", etag='"v1"', last_fetched_at=T0, last_status=200
        )
        assert state.last_fetched_at is None  # input state untouched
        assert ingester.source_type is SourceType.RSS
        assert ingester.calls == ["example"]

    def test_unknown_source_yields_nothing(self) -> None:
        source = Source(id="other", type=SourceType.HN)
        ingester = FakeSourceIngester(SourceType.HN)
        got, _ = ingester.ingest(source, FakeFetcher(), SourceState(source_id="other"))
        assert got == []

    def test_configured_error_is_raised(self) -> None:
        source = Source(id="broken", type=SourceType.RSS, url="https://example.com/feed")
        ingester = FakeSourceIngester(errors={"broken": RuntimeError("boom")})
        with pytest.raises(RuntimeError, match="boom"):
            ingester.ingest(source, FakeFetcher(), SourceState(source_id="broken"))
