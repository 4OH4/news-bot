"""Tests for the ingest and extract stages, using real SQLite and filesystem adapters."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from newsfeed.adapters.localfs_blobstore import LocalBlobStore
from newsfeed.adapters.sqlite_repository import SqliteRepository
from newsfeed.config import UrlParamsConfig
from newsfeed.extract.chain import ExtractorChain
from newsfeed.extract.overrides import ExtractionConfig
from newsfeed.extract.retry import RetryPolicy
from newsfeed.extract.trafilatura_extractor import TrafilaturaExtractor
from newsfeed.models import (
    Article,
    ArticleExtraction,
    ExtractionResult,
    ExtractionStatus,
    PipelineStage,
    RunStats,
    Source,
    SourceState,
    SourceType,
)
from newsfeed.pipeline import (
    NotAnArticleError,
    PageUnavailableError,
    RunTally,
    _known_ids,
    _split_stale,
    decode_html,
    extract_saved_url,
    fetch_page,
    ingest_sources,
    inspect_url,
    run_pipeline,
)
from newsfeed.ports import Extractor
from newsfeed.report import build_report
from newsfeed.wiring import Services, build_ingesters
from tests.fakes import FakeExtractor, FakeFetcher, FakeResponse, FakeSourceIngester

FIXTURES = Path(__file__).parent / "fixtures"
FEED_URL = "https://news.example.com/feed.xml"
BRIDGE_URL = "https://news.example.com/2026/10/bridge?utm_source=rss"
BRIDGE_CANONICAL = "https://news.example.com/local/2025/03/marrowby-floating-bridge"
HTML = {"content-type": "text/html; charset=utf-8"}

SOURCE = Source(id="gazette", type=SourceType.RSS, url=FEED_URL)


def _ok(text: str = "Invented body text.", **changes: object) -> ExtractionResult:
    return ExtractionResult.model_validate(
        {
            "status": ExtractionStatus.OK,
            "title": "Extracted title",
            "content_html": f"<p>{text}</p>",
            "text": text,
            "word_count": 150,
            **changes,
        }
    )


def _fetcher(*, etag: str | None = None) -> FakeFetcher:
    fetcher = FakeFetcher(fixtures_dir=FIXTURES)
    fetcher.add_file(FEED_URL, "feeds/pipeline_feed.xml", etag=etag)
    fetcher.add_file(BRIDGE_URL, "extract/news_article.html")
    fetcher.add_file("https://blog.example.org/2026/10/post", "extract/blog_post.html")
    fetcher.add(
        "https://news.example.com/files/report.pdf",
        b"%PDF-1.7",
        headers={"content-type": "application/pdf"},
    )
    return fetcher


@pytest.fixture
def repository(tmp_path: Path) -> Iterator[SqliteRepository]:
    with SqliteRepository(tmp_path / "newsfeed.db") as repo:
        yield repo


def _services(
    tmp_path: Path,
    repository: SqliteRepository,
    fetcher: FakeFetcher,
    extractor: Extractor | None = None,
    **overrides: object,
) -> Services:
    values: dict[str, object] = {
        "repository": repository,
        "blobs": LocalBlobStore(tmp_path / "blobs"),
        "fetcher": fetcher,
        "ingesters": build_ingesters(),
        "extractor": extractor or ExtractorChain([TrafilaturaExtractor()]),
        "url_rules": UrlParamsConfig(),
        "concurrency": 2,
        **overrides,
    }
    return Services(**values)  # type: ignore[arg-type]


def _unranked(repository: SqliteRepository) -> dict[str, dict[str, object]]:
    """Stored articles without the scores, which drift with the clock between runs."""
    return {
        a.id: a.model_dump(exclude={"score", "score_breakdown"}) for a in repository.list_articles()
    }


def _by_url(repository: SqliteRepository) -> dict[str, Article]:
    return {a.url: a for a in repository.list_articles()}


# --- End to end ------------------------------------------------------------------


def test_end_to_end_stores_articles_with_content(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    services = _services(tmp_path, repository, _fetcher())

    stats = run_pipeline(services, [SOURCE])

    articles = _by_url(repository)
    assert len(articles) == 4
    bridge = articles["https://news.example.com/2026/10/bridge?utm_source=rss"]
    # The page's rel=canonical replaced the feed URL's id.
    assert bridge.canonical_url == BRIDGE_CANONICAL
    assert bridge.extraction.status is ExtractionStatus.OK
    assert bridge.content_ref == f"articles/{bridge.id}.html"
    html = services.blobs.get(bridge.content_ref).decode()
    assert "floating cycle bridge" in html
    # The feed's own published_at (with a real time) is kept over the page's.
    assert bridge.published_at == datetime(2026, 10, 5, 8, 30, tzinfo=UTC)
    assert bridge.author == "Priya Fenwick-Oduya"  # filled in from the page
    assert stats.finished_at is not None
    assert repository.get_run_stats(stats.run_id) == stats


def test_run_stats_have_stage_counts_and_per_domain_outcomes(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    services = _services(tmp_path, repository, _fetcher())

    stats = run_pipeline(services, [SOURCE])

    ingest = stats.stage_counts[PipelineStage.INGEST]
    assert (ingest["sources"], ingest["entries"], ingest["new"], ingest["duplicates"]) == (
        1,
        4,
        4,
        0,
    )
    extract = stats.stage_counts[PipelineStage.EXTRACT]
    assert extract["attempted"] == 4
    assert extract["failed"] == 1
    assert extract["skipped"] == 1
    assert extract["refined"] >= 1
    assert stats.extraction_by_domain["news.example.com"][ExtractionStatus.OK] == 1
    assert stats.extraction_by_domain["news.example.com"][ExtractionStatus.FAILED] == 1
    assert stats.extraction_by_domain["news.example.com"][ExtractionStatus.SKIPPED] == 1
    assert sum(stats.extraction_by_domain["blog.example.org"].values()) == 1


def test_second_run_is_idempotent_and_sends_conditional_headers(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    fetcher = _fetcher(etag='"v1"')
    services = _services(tmp_path, repository, fetcher)
    run_pipeline(services, [SOURCE])
    before = _unranked(repository)
    page_fetches = len(fetcher.fetched_urls()) - 1

    stats = run_pipeline(services, [SOURCE])

    assert _unranked(repository) == before
    assert fetcher.calls[-1].url == FEED_URL
    assert fetcher.calls[-1].etag == '"v1"'
    assert stats.stage_counts[PipelineStage.INGEST]["new"] == 0
    # Nothing was pending, so no article page was fetched again.
    assert len(fetcher.fetched_urls()) - 2 == page_fetches


def test_second_run_without_etag_does_not_duplicate_or_refetch(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    fetcher = _fetcher()
    services = _services(tmp_path, repository, fetcher)
    run_pipeline(services, [SOURCE])
    before = {a.id for a in repository.list_articles()}
    page_fetches = len(fetcher.fetched_urls()) - 1

    stats = run_pipeline(services, [SOURCE])

    # The bridge article was re-keyed by its canonical URL, yet the feed's
    # original URL is still recognised, so it isn't ingested and merged again.
    assert {a.id for a in repository.list_articles()} == before
    assert stats.stage_counts[PipelineStage.INGEST]["duplicates"] == 4
    assert len(fetcher.fetched_urls()) - 2 == page_fetches


# --- Errors ------------------------------------------------------------------------


def test_failing_article_is_recorded_logged_and_counted(
    tmp_path: Path, repository: SqliteRepository, log_records: list[logging.LogRecord]
) -> None:
    services = _services(tmp_path, repository, _fetcher())

    stats = run_pipeline(services, [SOURCE])

    missing = _by_url(repository)["https://news.example.com/2026/10/missing"]
    assert missing.extraction.status is ExtractionStatus.FAILED
    assert missing.extraction.error is not None
    assert "404" in missing.extraction.error
    assert stats.error_count == 1
    warnings = [r for r in log_records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    record = warnings[0]
    assert record.stage == "extract"  # type: ignore[attr-defined]
    assert record.source_id == "gazette"  # type: ignore[attr-defined]
    assert record.url == "https://news.example.com/2026/10/missing"  # type: ignore[attr-defined]
    assert record.error_type == "PageUnavailableError"  # type: ignore[attr-defined]
    assert "404" in record.error  # type: ignore[attr-defined]


def test_extractor_exception_is_caught_per_article(
    tmp_path: Path, repository: SqliteRepository, log_records: list[logging.LogRecord]
) -> None:
    class Exploding:
        name = "exploding"

        def extract(self, html: str, url: str) -> ExtractionResult:
            if "blog" in url:
                raise RuntimeError("boom")
            return _ok()

    services = _services(tmp_path, repository, _fetcher(), Exploding())

    stats = run_pipeline(services, [SOURCE])

    blog = _by_url(repository)["https://blog.example.org/2026/10/post"]
    assert blog.extraction.status is ExtractionStatus.FAILED
    assert blog.extraction.error == "RuntimeError: boom"
    # The other pages were still extracted.
    assert stats.extraction_by_domain["news.example.com"][ExtractionStatus.OK] == 1
    record = next(
        r
        for r in log_records
        if r.levelno == logging.WARNING and "boom" in r.getMessage() + str(getattr(r, "error", ""))
    )
    assert record.error_type == "RuntimeError"  # type: ignore[attr-defined]


def test_failing_source_is_logged_counted_and_does_not_stop_others(
    tmp_path: Path, repository: SqliteRepository, log_records: list[logging.LogRecord]
) -> None:
    good = Article(
        canonical_url="https://ok.example.com/a",
        url="https://ok.example.com/a",
        source_id="good",
        title="Good",
        fetched_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    ingester = FakeSourceIngester(
        SourceType.RSS, {"good": [good]}, errors={"bad": ValueError("feed is broken")}
    )
    fetcher = FakeFetcher(
        {"https://ok.example.com/a": FakeResponse(b"<html></html>", headers=HTML)}
    )
    services = _services(
        tmp_path,
        repository,
        fetcher,
        FakeExtractor(default=_ok()),
        ingesters={SourceType.RSS: ingester},
    )
    sources = [
        Source(id="bad", type=SourceType.RSS, url="https://bad.example.com/feed"),
        Source(id="good", type=SourceType.RSS, url="https://ok.example.com/feed"),
    ]

    stats = run_pipeline(services, sources)

    assert stats.error_count == 1
    assert stats.stage_counts[PipelineStage.INGEST]["sources_failed"] == 1
    assert repository.get_article(good.id) is not None
    bad_state = repository.get_source_state("bad")
    assert bad_state is not None
    assert (bad_state.consecutive_failures, bad_state.last_error) == (
        1,
        "ValueError: feed is broken",
    )
    (record,) = [r for r in log_records if r.levelno == logging.WARNING]
    assert record.stage == "ingest"  # type: ignore[attr-defined]
    assert record.source_id == "bad"  # type: ignore[attr-defined]
    assert record.url == "https://bad.example.com/feed"  # type: ignore[attr-defined]
    assert record.error_type == "ValueError"  # type: ignore[attr-defined]
    assert record.error == "feed is broken"  # type: ignore[attr-defined]


def test_unusable_entry_url_is_logged_and_counted(
    tmp_path: Path, repository: SqliteRepository, log_records: list[logging.LogRecord]
) -> None:
    bad = Article(
        canonical_url="/relative",
        url="/relative",
        source_id="s",
        title="Relative",
        fetched_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    ingester = FakeSourceIngester(SourceType.RSS, {"s": [bad]})
    services = _services(
        tmp_path, repository, FakeFetcher(), FakeExtractor(), ingesters={SourceType.RSS: ingester}
    )

    stats = run_pipeline(
        services,
        [Source(id="s", type=SourceType.RSS, url="https://x.example.com/f")],
        extract=False,
    )

    assert stats.error_count == 1
    assert repository.list_articles() == []
    (record,) = [r for r in log_records if r.levelno == logging.WARNING]
    assert (record.stage, record.source_id, record.url) == ("ingest", "s", "/relative")  # type: ignore[attr-defined]
    assert record.error_type == "ValueError"  # type: ignore[attr-defined]


def test_extractor_reported_failure_is_logged_and_counted(
    tmp_path: Path, repository: SqliteRepository, log_records: list[logging.LogRecord]
) -> None:
    article = _article_for("https://x.example.com/1")
    fetcher = FakeFetcher({article.url: FakeResponse(b"<html></html>", headers=HTML)})
    failed = ExtractionResult(status=ExtractionStatus.FAILED, error="no main content found")
    services = _services(tmp_path, repository, fetcher, FakeExtractor(default=failed))
    repository.upsert_article(article)

    stats = run_pipeline(services, [], ingest=False)

    stored = repository.get_article(article.id)
    assert stored is not None
    assert stored.extraction.status is ExtractionStatus.FAILED
    assert stats.error_count == 1
    (record,) = [r for r in log_records if r.levelno == logging.WARNING]
    assert record.error == "no main content found"  # type: ignore[attr-defined]
    assert (record.stage, record.url) == ("extract", article.url)  # type: ignore[attr-defined]


# --- Logging and metrics --------------------------------------------------------


def _finished(log_records: list[logging.LogRecord]) -> list[logging.LogRecord]:
    return [r for r in log_records if r.getMessage() == "extraction finished"]


def test_every_processed_article_logs_one_extraction_finished_entry(
    tmp_path: Path, repository: SqliteRepository, log_records: list[logging.LogRecord]
) -> None:
    services = _services(tmp_path, repository, _fetcher())

    run_pipeline(services, [SOURCE])

    entries = _finished(log_records)
    assert len(entries) == 4
    by_url = {r.url: r for r in entries}  # type: ignore[attr-defined]
    assert len(by_url) == 4
    ok = by_url[BRIDGE_URL]
    assert (ok.stage, ok.source_id, ok.domain, ok.status) == (  # type: ignore[attr-defined]
        "extract",
        "gazette",
        "news.example.com",
        "ok",
    )
    assert ok.word_count > 0  # type: ignore[attr-defined]
    assert ok.method == "trafilatura"  # type: ignore[attr-defined]
    assert isinstance(ok.elapsed_ms, int)  # type: ignore[attr-defined]
    assert ok.chain[0]["method"] == "trafilatura"  # type: ignore[attr-defined]
    failed = by_url["https://news.example.com/2026/10/missing"]
    assert failed.status == "failed"  # type: ignore[attr-defined]
    assert "404" in failed.error  # type: ignore[attr-defined]
    statuses = sorted(r.status for r in entries)  # type: ignore[attr-defined]
    assert statuses == ["failed", "ok", "ok", "skipped"]


def test_run_finished_carries_metrics_and_failure_reasons_are_counted(
    tmp_path: Path, repository: SqliteRepository, log_records: list[logging.LogRecord]
) -> None:
    services = _services(tmp_path, repository, _fetcher())

    stats = run_pipeline(services, [SOURCE])

    assert "failed:http_4xx" not in stats.stage_counts[PipelineStage.EXTRACT]
    assert stats.failures_by_reason == {"http_4xx": 1}
    (record,) = [r for r in log_records if r.getMessage() == "run finished"]
    metrics = record.metrics  # type: ignore[attr-defined]
    assert metrics["run_id"] == stats.run_id
    assert set(metrics["duration_s"]) == {"ingest", "extract", "rank", "publish"}
    assert metrics["failures_by_reason"] == {"http_4xx": 1}
    assert metrics["feeds"]["gazette"]["outcome"] == "ok"
    assert metrics["feeds"]["gazette"]["new"] == 4
    assert metrics["articles_by_domain"]["news.example.com"]["failed"] == 1
    news = metrics["words_by_domain"]["news.example.com"]
    assert news["n"] == 1 and news["median"] == news["min"] > 0


def test_failed_feed_is_a_feed_outcome(
    tmp_path: Path, repository: SqliteRepository, log_records: list[logging.LogRecord]
) -> None:
    services = _services(tmp_path, repository, FakeFetcher())

    run_pipeline(services, [SOURCE])

    (record,) = [r for r in log_records if r.getMessage() == "run finished"]
    feed = record.metrics["feeds"]["gazette"]  # type: ignore[attr-defined]
    assert feed["outcome"] == "failed"
    assert feed["http_status"] == 404


def test_second_run_of_unchanged_feed_is_not_modified(
    tmp_path: Path, repository: SqliteRepository, log_records: list[logging.LogRecord]
) -> None:
    services = _services(tmp_path, repository, _fetcher(etag='"v1"'))

    run_pipeline(services, [SOURCE], extract=False)
    run_pipeline(services, [SOURCE], extract=False)

    first, second = [r for r in log_records if r.getMessage() == "run finished"]
    assert first.metrics["feeds"]["gazette"]["outcome"] == "ok"  # type: ignore[attr-defined]
    feed = second.metrics["feeds"]["gazette"]  # type: ignore[attr-defined]
    assert feed["outcome"] == "not_modified"
    assert feed["http_status"] == 304


def test_merged_article_also_logs_one_entry(
    tmp_path: Path, repository: SqliteRepository, log_records: list[logging.LogRecord]
) -> None:
    first = _article_for("https://x.example.com/1")
    second = _article_for("https://x.example.com/2")
    page = b'<html><head><link rel="canonical" href="https://x.example.com/same"></head></html>'
    fetcher = FakeFetcher(
        {first.url: FakeResponse(page, headers=HTML), second.url: FakeResponse(page, headers=HTML)}
    )
    services = _services(tmp_path, repository, fetcher, FakeExtractor(default=_ok()), concurrency=1)
    repository.upsert_article(first)
    repository.upsert_article(second)

    run_pipeline(services, [], ingest=False)

    statuses = sorted(r.status for r in _finished(log_records))  # type: ignore[attr-defined]
    assert statuses == ["merged", "ok"]


# --- Skipping --------------------------------------------------------------------


def test_hn_discussion_pages_and_non_html_are_skipped_without_failure(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    fetcher = _fetcher()
    services = _services(tmp_path, repository, fetcher)
    run_pipeline(services, [SOURCE])
    article = Article(
        canonical_url="https://news.ycombinator.com/item?id=42",
        url="https://news.ycombinator.com/item?id=42",
        source_id="hn",
        title="Ask HN: invented question",
        summary="Hacker News discussion (10 points)",
        fetched_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    repository.upsert_article(article)

    stats = run_pipeline(services, [], ingest=False)

    stored = repository.get_article(article.id)
    assert stored is not None
    assert stored.extraction.status is ExtractionStatus.SKIPPED
    assert stored.summary == article.summary
    assert "https://news.ycombinator.com/item?id=42" not in fetcher.fetched_urls()
    pdf = _by_url(repository)["https://news.example.com/files/report.pdf"]
    assert pdf.extraction.status is ExtractionStatus.SKIPPED
    assert pdf.extraction.error is not None
    assert "application/pdf" in pdf.extraction.error
    assert stats.error_count == 0


def test_summary_only_domain_is_not_fetched(tmp_path: Path, repository: SqliteRepository) -> None:
    config = ExtractionConfig.model_validate(
        {"domains": {"blog.example.org": {"mode": "summary_only"}}}
    )
    fetcher = _fetcher()
    services = _services(
        tmp_path,
        repository,
        fetcher,
        ExtractorChain([TrafilaturaExtractor()], config),
    )

    run_pipeline(services, [SOURCE])

    blog = _by_url(repository)["https://blog.example.org/2026/10/post"]
    assert blog.extraction.status is ExtractionStatus.SKIPPED
    assert "https://blog.example.org/2026/10/post" not in fetcher.fetched_urls()


# --- Canonical refinement ------------------------------------------------------------


def _article_for(url: str, source_id: str = "s") -> Article:
    return Article(
        canonical_url=url,
        url=url,
        source_id=source_id,
        title=f"Title {url[-1]}",
        fetched_at=datetime(2026, 10, 1, tzinfo=UTC),
    )


def test_articles_with_the_same_page_canonical_merge_into_one(
    tmp_path: Path, repository: SqliteRepository, log_records: list[logging.LogRecord]
) -> None:
    page = '<html><head><link rel="canonical" href="https://a.example.com/story"></head></html>'
    a, b = _article_for("https://a.example.com/x/1"), _article_for("https://a.example.com/y/2")
    fetcher = FakeFetcher(
        {
            a.url: FakeResponse(page.encode(), headers=HTML),
            b.url: FakeResponse(page.encode(), headers=HTML),
        }
    )
    services = _services(tmp_path, repository, fetcher, FakeExtractor(default=_ok()), concurrency=1)
    repository.upsert_article(a)
    repository.upsert_article(b)

    stats = run_pipeline(services, [], ingest=False)

    (stored,) = repository.list_articles()
    assert stored.canonical_url == "https://a.example.com/story"
    assert stored.content_ref == f"articles/{stored.id}.html"
    assert repository.get_article(a.id) is None
    assert repository.get_article(b.id) is None
    extract = stats.stage_counts[PipelineStage.EXTRACT]
    assert (extract["refined"], extract["merged"]) == (1, 1)
    refinements = [r for r in log_records if r.getMessage() == "canonical URL refined"]
    assert len(refinements) == 2
    assert refinements[0].canonical == "https://a.example.com/story"  # type: ignore[attr-defined]
    assert refinements[0].stage == "extract"  # type: ignore[attr-defined]
    assert {refinements[0].old_id, refinements[1].old_id} == {a.id, b.id}  # type: ignore[attr-defined]


def test_extractor_gets_the_final_url_after_redirects(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    article = _article_for("https://short.example.com/1")
    fetcher = FakeFetcher(
        {
            article.url: FakeResponse(
                b"<html></html>", headers=HTML, final_url="https://long.example.org/real"
            )
        }
    )
    extractor = FakeExtractor(default=_ok())
    services = _services(tmp_path, repository, fetcher, extractor)
    repository.upsert_article(article)

    stats = run_pipeline(services, [], ingest=False)

    assert [url for url, _ in extractor.calls] == ["https://long.example.org/real"]
    assert "long.example.org" in stats.extraction_by_domain


# --- Selection ---------------------------------------------------------------------


def test_limit_and_since(tmp_path: Path, repository: SqliteRepository) -> None:
    services = _services(tmp_path, repository, _fetcher())

    run_pipeline(services, [SOURCE], extract=False, limit=2)
    assert len(repository.list_articles()) == 2

    stats = run_pipeline(services, [], ingest=False, limit=1)
    assert stats.stage_counts[PipelineStage.EXTRACT]["attempted"] == 1

    future = datetime(2999, 1, 1, tzinfo=UTC)
    stats = run_pipeline(services, [], ingest=False, since=future)
    assert stats.stage_counts[PipelineStage.EXTRACT]["attempted"] == 0


def test_since_drops_old_entries_at_ingest(tmp_path: Path, repository: SqliteRepository) -> None:
    services = _services(tmp_path, repository, _fetcher())

    run_pipeline(services, [SOURCE], extract=False, since=datetime(2026, 10, 6, tzinfo=UTC))

    # Only the entry dated 5 Oct is dropped; the undated ones are kept.
    urls = set(_by_url(repository))
    assert "https://news.example.com/2026/10/bridge?utm_source=rss" not in urls
    assert len(urls) == 3


def test_source_option_narrows_extraction(tmp_path: Path, repository: SqliteRepository) -> None:
    a = _article_for("https://a.example.com/x/1", "gazette")
    b = _article_for("https://b.example.com/y/2", "other")
    fetcher = FakeFetcher(
        {
            a.url: FakeResponse(b"<html></html>", headers=HTML),
            b.url: FakeResponse(b"<html></html>", headers=HTML),
        }
    )
    services = _services(tmp_path, repository, fetcher, FakeExtractor(default=_ok()))
    repository.upsert_article(a)
    repository.upsert_article(b)

    stats = run_pipeline(services, [SOURCE], ingest=False, extract_source_ids=["gazette"])

    assert stats.stage_counts[PipelineStage.EXTRACT]["attempted"] == 1
    stored_a, stored_b = repository.get_article(a.id), repository.get_article(b.id)
    assert stored_a is not None and stored_a.extraction.status is ExtractionStatus.OK
    assert stored_b is not None and stored_b.extraction.status is ExtractionStatus.PENDING


def test_plain_run_extracts_pending_articles_of_every_source(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    """Regression: without an explicit source filter, sources not passed to ingest
    (disabled or removed since) and ad hoc rows are still extracted."""
    articles = [
        _article_for("https://a.example.com/x/1", "gazette"),
        _article_for("https://b.example.com/y/2", "removed-source"),
        _article_for("https://c.example.com/z/3", "adhoc"),
    ]
    fetcher = FakeFetcher({a.url: FakeResponse(b"<html></html>", headers=HTML) for a in articles})
    services = _services(tmp_path, repository, fetcher, FakeExtractor(default=_ok()))
    for article in articles:
        repository.upsert_article(article)

    stats = run_pipeline(services, [SOURCE], ingest=False)

    assert stats.stage_counts[PipelineStage.EXTRACT]["attempted"] == 3
    assert {a.extraction.status for a in repository.list_articles()} == {ExtractionStatus.OK}


@pytest.mark.parametrize("option", [{"limit": 1}, {"since": datetime(2026, 10, 6, tzinfo=UTC)}])
def test_dropped_entries_keep_the_old_source_state(
    tmp_path: Path, repository: SqliteRepository, option: dict[str, object]
) -> None:
    services = _services(tmp_path, repository, _fetcher(etag='"v1"'))

    run_pipeline(services, [SOURCE], extract=False, **option)  # type: ignore[arg-type]

    kept = repository.get_source_state(SOURCE.id)
    assert kept is not None
    assert kept.etag is None  # only the health is recorded, not the conditional-GET state
    assert kept.last_success_at is not None
    # Without the option the next run still sees the whole feed (no 304).
    run_pipeline(services, [SOURCE], extract=False)
    assert len(repository.list_articles()) == 4
    state = repository.get_source_state(SOURCE.id)
    assert state is not None
    assert state.etag == '"v1"'


def test_source_without_ingester_is_skipped(tmp_path: Path, repository: SqliteRepository) -> None:
    services = _services(tmp_path, repository, FakeFetcher(), ingesters={})

    stats = run_pipeline(services, [SOURCE])

    assert stats.stage_counts[PipelineStage.INGEST]["sources_skipped"] == 1
    assert stats.error_count == 0


# --- Single URLs -------------------------------------------------------------------


def test_inspect_url_persists_nothing() -> None:
    fetcher = FakeFetcher({"https://x.example.com/a": FakeResponse(b"<html></html>", headers=HTML)})

    result = inspect_url(fetcher, FakeExtractor(default=_ok()), "https://x.example.com/a")

    assert result.title == "Extracted title"


def test_inspect_url_reports_unavailable_and_non_html_pages() -> None:
    fetcher = FakeFetcher(
        {
            "https://x.example.com/pdf": FakeResponse(
                b"%PDF", headers={"content-type": "application/pdf"}
            )
        }
    )
    extractor = FakeExtractor()

    with pytest.raises(PageUnavailableError, match="404"):
        inspect_url(fetcher, extractor, "https://x.example.com/none")
    with pytest.raises(NotAnArticleError, match="application/pdf"):
        inspect_url(fetcher, extractor, "https://x.example.com/pdf")


def test_extract_saved_url_stores_like_a_feed_article(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    url = "https://x.example.com/a?utm_source=x"
    fetcher = FakeFetcher({url: FakeResponse(b"<html></html>", headers=HTML)})
    services = _services(tmp_path, repository, fetcher, FakeExtractor(default=_ok()))

    article = extract_saved_url(services, url)

    assert article.canonical_url == "https://x.example.com/a"
    assert article.title == "Extracted title"  # replaces the placeholder title (the URL)
    assert article.source_id == "adhoc"
    assert article.extraction.status is ExtractionStatus.OK
    assert repository.get_article(article.id) == article


def test_extract_saved_url_keeps_an_existing_feed_article(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    url = "https://x.example.com/a"
    feed_article = Article(
        canonical_url=url,
        url=url,
        source_id="gazette",
        title="Feed title",
        summary="Feed summary",
        fetched_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    repository.upsert_article(feed_article)
    fetcher = FakeFetcher({url: FakeResponse(b"<html></html>", headers=HTML)})
    services = _services(tmp_path, repository, fetcher, FakeExtractor(default=_ok()))

    article = extract_saved_url(services, url)

    assert article.id == feed_article.id
    assert (article.source_id, article.title, article.summary) == (
        "gazette",
        "Feed title",
        "Feed summary",
    )
    assert article.extraction.status is ExtractionStatus.OK
    assert len(repository.list_articles()) == 1


_STORY_PAGE = b'<html><head><link rel="canonical" href="https://a.example.com/story"></head></html>'


def test_extract_saved_url_finds_an_article_already_rekeyed(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    """Regression: the feed URL's article was re-keyed by canonical refinement, so
    --save must re-extract that article and report its id, not an ad hoc one."""
    url = "https://a.example.com/x/1"
    fetcher = FakeFetcher({url: FakeResponse(_STORY_PAGE, headers=HTML)})
    services = _services(tmp_path, repository, fetcher, FakeExtractor(default=_ok()))
    repository.upsert_article(_article_for(url, "gazette"))
    run_pipeline(services, [], ingest=False)
    (rekeyed,) = repository.list_articles()
    assert rekeyed.canonical_url == "https://a.example.com/story"

    article = extract_saved_url(services, url)

    assert article.id == rekeyed.id
    assert article.source_id == "gazette"
    assert article.extraction.status is ExtractionStatus.OK
    assert repository.list_articles() == [article]
    assert len(fetcher.calls) == 2  # re-fetched, not just looked up


def test_extract_saved_url_returns_the_article_it_merged_into(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    url = "https://a.example.com/x/1"
    fetcher = FakeFetcher({url: FakeResponse(_STORY_PAGE, headers=HTML)})
    services = _services(tmp_path, repository, fetcher, FakeExtractor(default=_ok()))
    target = Article(
        canonical_url="https://a.example.com/story",
        url="https://a.example.com/y/2",
        source_id="gazette",
        title="Feed title",
        fetched_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    repository.upsert_article(target)

    article = extract_saved_url(services, url)

    assert article == target
    assert repository.list_articles() == [target]


# --- Decoding ----------------------------------------------------------------------


def test_decode_prefers_header_charset() -> None:
    content = "<p>café</p>".encode("latin-1")

    assert decode_html(content, {"content-type": "text/html; charset=ISO-8859-1"}) == "<p>café</p>"


def test_decode_uses_meta_charset_without_header() -> None:
    content = '<meta charset="windows-1252"><p>café “quoted”</p>'.encode("cp1252")

    assert "café “quoted”" in decode_html(content, {"content-type": "text/html"})


def test_decode_falls_back_when_declared_charset_is_wrong_or_unknown() -> None:
    utf8 = "<p>café</p>".encode()
    assert decode_html(utf8, {"content-type": "text/html; charset=nonsense"}) == "<p>café</p>"
    # Declared ASCII but really UTF-8 bytes: fall through to UTF-8.
    assert decode_html(utf8, {"content-type": "text/html; charset=ascii"}) == "<p>café</p>"


def test_decode_legacy_bytes_without_declaration_use_cp1252() -> None:
    assert decode_html("<p>café</p>".encode("cp1252")) == "<p>café</p>"


def test_decode_one_bad_byte_in_declared_utf8_does_not_garble_the_page() -> None:
    content = b"\xff" + "café — résumé".encode()

    decoded = decode_html(content, {"content-type": "text/html; charset=utf-8"})

    assert decoded == "�café — résumé"


def test_decode_strips_bom() -> None:
    assert decode_html(b"\xef\xbb\xbf<p>hi</p>") == "<p>hi</p>"


def test_fetch_page_decodes_and_returns_final_url() -> None:
    fetcher = FakeFetcher(
        {
            "https://x.example.com/a": FakeResponse(
                b"<p>hi</p>", headers=HTML, final_url="https://y.example.com/b"
            )
        }
    )

    page = fetch_page(fetcher, "https://x.example.com/a")

    assert (page.html, page.final_url) == ("<p>hi</p>", "https://y.example.com/b")


# --- Bounded dedupe lookup ------------------------------------------------------------


class _RecordingRepository:
    """Wraps a repository and records the ``since`` of each ``list_articles`` call."""

    def __init__(self, inner: SqliteRepository) -> None:
        self._inner = inner
        self.since_args: list[datetime | None] = []

    def list_articles(self, **kwargs: object) -> list[Article]:
        self.since_args.append(kwargs.get("since"))  # type: ignore[arg-type]
        return self._inner.list_articles(**kwargs)  # type: ignore[arg-type]

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def _stored(
    repository: SqliteRepository, url: str, fetched_at: datetime, **changes: object
) -> Article:
    article = Article.model_validate(
        {
            "canonical_url": url,
            "url": url,
            "source_id": "gazette",
            "title": "Stored",
            "fetched_at": fetched_at,
            **changes,
        }
    )
    repository.upsert_article(article)
    return article


def test_known_ids_reads_only_the_window_including_the_boundary(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    now = datetime(2026, 10, 18, tzinfo=UTC)
    services = _services(tmp_path, repository, _fetcher())
    edge = _stored(repository, "https://e.example.com/edge", now - timedelta(days=14))
    old = _stored(repository, "https://e.example.com/old", now - timedelta(days=14, seconds=1))
    recent = _stored(repository, "https://e.example.com/recent", now - timedelta(days=1))

    known = _known_ids(services, now - timedelta(days=14))

    assert known == {edge.id, recent.id}
    assert old.id not in known


def test_ingest_uses_the_configured_window_and_since_widens_it(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    recording = _RecordingRepository(repository)
    services = _services(tmp_path, recording, _fetcher(), dedupe_window_days=3)  # type: ignore[arg-type]
    before = datetime.now(UTC)

    ingest_sources(services, [], RunTally())
    window = recording.since_args[-1]
    assert window is not None
    assert before - timedelta(days=3) <= window <= datetime.now(UTC) - timedelta(days=3)

    long_ago = datetime(2020, 1, 1, tzinfo=UTC)
    ingest_sources(services, [], RunTally(), since=long_ago)
    assert recording.since_args[-1] == long_ago

    recent = datetime.now(UTC) - timedelta(days=1)
    ingest_sources(services, [], RunTally(), since=recent)
    last = recording.since_args[-1]
    assert last is not None and last < recent  # the window is wider than --since


def test_old_article_that_reappears_keeps_its_stored_state(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    # Stored 30 days ago, so outside the 14-day window, but already extracted.
    fetched = datetime.now(UTC) - timedelta(days=30)
    stored = _stored(
        repository,
        "https://blog.example.org/2026/10/post",
        fetched,
        content_ref="blobs/post.html",
        score=0.9,
        extraction={"status": "ok", "method": "trafilatura", "word_count": 150},
    )
    services = _services(tmp_path, repository, _fetcher())
    tally = RunTally()

    ingest_sources(services, [SOURCE], tally)

    assert repository.get_article(stored.id) == stored
    assert len(repository.list_articles()) == 4  # the other entries are new
    stats = tally.stats("run", fetched, fetched)
    assert stats.stage_counts[PipelineStage.INGEST]["new"] == 3


def _age_everything(repository: SqliteRepository, days: int) -> None:
    """Move every stored article's ``fetched_at`` ``days`` into the past."""
    for article in repository.list_articles():
        repository.upsert_article(
            article.model_copy(update={"fetched_at": article.fetched_at - timedelta(days=days)})
        )


def test_rekeyed_article_older_than_the_window_is_not_ingested_again(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    # The probe from PR #106's review: the bridge entry was re-keyed by its
    # page's rel=canonical, then everything aged past the 14-day window while
    # the feed still lists the entry.
    fetcher = _fetcher()
    services = _services(tmp_path, repository, fetcher)
    run_pipeline(services, [SOURCE])
    bridge = _by_url(repository)[BRIDGE_URL]
    assert bridge.canonical_url == BRIDGE_CANONICAL  # re-keyed away from the feed URL
    assert bridge.published_at is not None
    assert bridge.published_at < datetime.now(UTC) - timedelta(days=14)
    _age_everything(repository, 30)
    before = _unranked(repository)
    calls = len(fetcher.calls)

    stats = run_pipeline(services, [SOURCE])

    ingest = stats.stage_counts[PipelineStage.INGEST]
    assert (ingest["new"], ingest.get("stale", 0), ingest["duplicates"]) == (0, 1, 3)
    assert stats.stage_counts.get(PipelineStage.EXTRACT, {}).get("merged", 0) == 0
    assert [call.url for call in fetcher.calls[calls:]] == [FEED_URL]  # no page fetched
    assert _unranked(repository) == before


def test_without_the_stale_check_the_probe_refetches_the_page(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    # The same probe on a source never read in full before: the entry can't be
    # judged stale, so it is ingested, fetched and merged (the #106 behaviour).
    fetcher = _fetcher()
    services = _services(tmp_path, repository, fetcher)
    run_pipeline(services, [SOURCE])
    _age_everything(repository, 30)
    repository.save_source_state(SourceState(source_id=SOURCE.id))
    calls = len(fetcher.calls)

    stats = run_pipeline(services, [SOURCE])

    assert stats.stage_counts[PipelineStage.INGEST]["new"] == 1
    assert stats.stage_counts[PipelineStage.EXTRACT]["merged"] == 1
    assert [call.url for call in fetcher.calls[calls:]] == [FEED_URL, BRIDGE_URL]


def test_stale_entries_need_a_date_before_the_cutoff_and_a_source_read_before() -> None:
    cutoff = datetime(2026, 10, 6, tzinfo=UTC)
    read_before = SourceState(source_id="gazette", last_fetched_at=cutoff)
    old = _article_for("https://e.example.com/old").model_copy(
        update={"published_at": cutoff - timedelta(seconds=1)}
    )
    edge = _article_for("https://e.example.com/edge").model_copy(update={"published_at": cutoff})
    undated = _article_for("https://e.example.com/undated")

    assert _split_stale([old, edge, undated], read_before, cutoff) == ([edge, undated], [old])
    never_read = SourceState(source_id="gazette")
    assert _split_stale([old, edge, undated], never_read, cutoff) == ([old, edge, undated], [])


def test_short_dedupe_window_never_drops_entries_inside_the_feed_window(
    tmp_path: Path, repository: SqliteRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression for PR #126's review: with a 1-day dedupe window, an entry
    # dated ~48 h back (before the lookup, but inside the 72 h feed window)
    # from a source read before must still be stored, not counted stale.
    now = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
    monkeypatch.setattr("newsfeed.pipeline._utc_now", lambda: now)
    repository.save_source_state(
        SourceState(source_id=SOURCE.id, last_fetched_at=now - timedelta(days=3))
    )
    services = _services(tmp_path, repository, _fetcher(), dedupe_window_days=1)
    tally = RunTally()

    ingest_sources(services, [SOURCE], tally)

    bridge = _by_url(repository)[BRIDGE_URL]
    assert bridge.published_at == datetime(2026, 10, 5, 8, 30, tzinfo=UTC)
    ingest = tally.stats("run", now, now).stage_counts[PipelineStage.INGEST]
    assert ingest.get("stale", 0) == 0
    assert ingest["new"] == 4


class _CountingRepository(_RecordingRepository):
    """Also counts the documents ``list_articles`` returns and the ``get_article`` calls."""

    def __init__(self, inner: SqliteRepository) -> None:
        super().__init__(inner)
        self.listed = 0
        self.gets = 0

    def list_articles(self, **kwargs: object) -> list[Article]:
        articles = super().list_articles(**kwargs)
        self.listed += len(articles)
        return articles

    def get_article(self, article_id: str) -> Article | None:
        self.gets += 1
        return self._inner.get_article(article_id)


@pytest.mark.parametrize("history", [0, 200])
def test_ingest_reads_do_not_grow_with_history(
    tmp_path: Path, repository: SqliteRepository, history: int
) -> None:
    # The probe again, with ``history`` older articles stored: reads stay the same.
    services = _services(tmp_path, repository, _fetcher())
    run_pipeline(services, [SOURCE])
    _age_everything(repository, 30)
    for n in range(history):
        _stored(repository, f"https://e.example.com/{n}", datetime.now(UTC) - timedelta(days=60))
    counting = _CountingRepository(repository)
    services = _services(tmp_path, counting, _fetcher())  # type: ignore[arg-type]

    ingest_sources(services, [SOURCE], RunTally())

    assert len(counting.since_args) == 1  # one windowed listing
    assert counting.listed == 0  # nothing was fetched inside the window
    # One get_article per undated entry left after dedupe; the stale one costs nothing.
    assert counting.gets == 3


# --- Paywalls (#84) ------------------------------------------------------------------

PAYWALL_CONFIG = ExtractionConfig.model_validate(
    {"domains": {"subs.example.com": {"paywall": True}}}
)


def _paywall_run(
    tmp_path: Path, repository: SqliteRepository, url: str, response: FakeResponse
) -> tuple[Article, ExtractionStatus, int]:
    """Extract one article served ``response``; return it as stored, its status and errors."""
    article = _article_for(url)
    repository.upsert_article(article)
    chain = ExtractorChain([TrafilaturaExtractor()], PAYWALL_CONFIG)
    services = _services(tmp_path, repository, FakeFetcher({url: response}), chain)

    stats = run_pipeline(services, [], ingest=False)

    stored = repository.get_article(article.id)
    assert stored is not None
    ((_, statuses),) = stats.extraction_by_domain.items()
    (status,) = statuses
    return stored, status, stats.error_count


@pytest.mark.parametrize(
    ("url", "status", "reason"),
    [
        ("https://news.example.com/a/1", 402, "HTTP 402 Payment Required"),
        ("https://subs.example.com/a/1", 402, "HTTP 402 Payment Required"),
        (
            "https://subs.example.com/a/1",
            403,
            "HTTP 403 from subs.example.com, configured paywall: true",
        ),
        (
            "https://www.subs.example.com/a/1",
            403,
            "HTTP 403 from subs.example.com, configured paywall: true",
        ),
    ],
)
def test_payment_required_and_paywall_domain_403_are_stored_paywalled(
    tmp_path: Path,
    repository: SqliteRepository,
    log_records: list[logging.LogRecord],
    url: str,
    status: int,
    reason: str,
) -> None:
    stored, counted, errors = _paywall_run(
        tmp_path, repository, url, FakeResponse(b"Subscription required", status=status)
    )

    assert stored.extraction.status is ExtractionStatus.PAYWALLED
    assert stored.extraction.error == reason
    assert counted is ExtractionStatus.PAYWALLED
    # Respecting a paywall is not an error (rule 6).
    assert errors == 0
    assert not [r for r in log_records if r.levelno >= logging.WARNING]
    (finished,) = [r for r in log_records if r.getMessage() == "extraction finished"]
    assert (finished.status, finished.error) == ("paywalled", reason)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("url", "status"),
    [
        ("https://news.example.com/a/1", 403),  # 403 elsewhere may be bot blocking
        ("https://subs.example.com/a/1", 401),
        ("https://subs.example.com/a/1", 404),
    ],
)
def test_other_http_errors_stay_failed(
    tmp_path: Path, repository: SqliteRepository, url: str, status: int
) -> None:
    stored, counted, errors = _paywall_run(
        tmp_path, repository, url, FakeResponse(b"", status=status)
    )

    assert stored.extraction.status is ExtractionStatus.FAILED
    assert stored.extraction.error == f"PageUnavailableError: HTTP {status}"
    assert counted is ExtractionStatus.FAILED
    assert errors == 1


def test_a_paywall_domain_403_without_a_chain_stays_failed(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    # Overrides come from the chain; a bare extractor has none to consult.
    url = "https://subs.example.com/a/1"
    article = _article_for(url)
    repository.upsert_article(article)
    fetcher = FakeFetcher({url: FakeResponse(b"", status=403)})
    services = _services(tmp_path, repository, fetcher, FakeExtractor())

    run_pipeline(services, [], ingest=False)

    stored = repository.get_article(article.id)
    assert stored is not None
    assert stored.extraction.status is ExtractionStatus.FAILED


def test_a_served_teaser_is_stored_paywalled_with_the_reason(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    teaser = (FIXTURES / "extract" / "paywall" / "notice_members_only.html").read_bytes()
    stored, counted, errors = _paywall_run(
        tmp_path, repository, "https://news.example.com/a/1", FakeResponse(teaser, headers=HTML)
    )

    assert stored.extraction.status is ExtractionStatus.PAYWALLED
    assert stored.extraction.error == (
        "paywall detected: subscriber notice ('This post is for paid members')"
    )
    assert counted is ExtractionStatus.PAYWALLED
    assert errors == 0


def test_fetch_page_reports_the_http_status_of_an_error() -> None:
    fetcher = FakeFetcher(
        {"https://x.example.com/paid": FakeResponse(b"", status=402)},
        disallowed=["https://x.example.com/private"],
    )

    with pytest.raises(PageUnavailableError) as paid:
        fetch_page(fetcher, "https://x.example.com/paid")
    with pytest.raises(PageUnavailableError) as refused:
        fetch_page(fetcher, "https://x.example.com/private")

    assert paid.value.status_code == 402
    assert refused.value.status_code is None


# --- Rank --------------------------------------------------------------------------


def test_run_ranks_recent_articles_and_counts_them(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    services = _services(tmp_path, repository, _fetcher())

    stats = run_pipeline(services, [SOURCE])

    articles = repository.list_articles()
    assert articles
    for article in articles:
        assert article.score is not None
        assert {"source", "recency"} <= article.score_breakdown.keys()
    assert stats.stage_counts[PipelineStage.RANK]["scored"] == len(articles)


def test_rank_can_be_skipped(tmp_path: Path, repository: SqliteRepository) -> None:
    services = _services(tmp_path, repository, _fetcher())

    stats = run_pipeline(services, [SOURCE], rank=False)

    assert all(a.score is None for a in repository.list_articles())
    assert PipelineStage.RANK not in stats.stage_counts


# --- Publish -----------------------------------------------------------------------


def test_run_publishes_the_feed_and_counts_items(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    services = _services(tmp_path, repository, _fetcher())

    stats = run_pipeline(services, [SOURCE])

    feed = json.loads((tmp_path / "blobs" / "feed.json").read_bytes())
    assert feed["run_id"] == stats.run_id
    assert feed["items"]
    assert (tmp_path / "blobs" / "feeds" / f"{stats.run_id}.json").exists()
    assert stats.stage_counts[PipelineStage.PUBLISH]["items"] == len(feed["items"])


def test_publish_can_be_skipped(tmp_path: Path, repository: SqliteRepository) -> None:
    services = _services(tmp_path, repository, _fetcher())

    stats = run_pipeline(services, [SOURCE], publish=False)

    assert not (tmp_path / "blobs" / "feed.json").exists()
    assert PipelineStage.PUBLISH not in stats.stage_counts


class _FailingFeedBlobs(LocalBlobStore):
    """Fails the write of the fixed ``feed.json`` key."""

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        if key == "feed.json":
            raise OSError("disk full")
        return super().put(key, data, content_type)


def test_failed_publish_is_counted_and_keeps_the_previous_feed(
    tmp_path: Path, repository: SqliteRepository, log_records: list[logging.LogRecord]
) -> None:
    previous = b'{"previous": true}'
    LocalBlobStore(tmp_path / "blobs").put("feed.json", previous)
    services = _services(
        tmp_path, repository, _fetcher(), blobs=_FailingFeedBlobs(tmp_path / "blobs")
    )

    stats = run_pipeline(services, [SOURCE], ingest=False, extract=False, rank=False)

    assert (tmp_path / "blobs" / "feed.json").read_bytes() == previous
    assert stats.stage_counts[PipelineStage.PUBLISH] == {"failed": 1}
    assert stats.error_count == 1
    assert any(r.getMessage().startswith("publish failed") for r in log_records)


# --- Stored feed health, failure categories and run metrics (#99) -----------------


class _ScriptedIngester:
    """Fails, succeeds, or answers 304 (the old state untouched) as scripted per call."""

    source_type = SourceType.RSS

    def __init__(self, script: list[str]) -> None:
        self._script = list(script)

    def ingest(
        self, source: Source, fetcher: object, state: SourceState
    ) -> tuple[list[Article], SourceState]:
        step = self._script.pop(0)
        if step == "fail":
            raise ValueError("feed is broken")
        if step == "304":
            return [], state
        return [], state.model_copy(
            update={"last_fetched_at": datetime.now(UTC), "last_status": 200}
        )


def test_feed_health_follows_failures_success_and_not_modified(
    tmp_path: Path, repository: SqliteRepository, log_records: list[logging.LogRecord]
) -> None:
    ingester = _ScriptedIngester(["fail", "fail", "ok", "fail", "304"])
    services = _services(tmp_path, repository, FakeFetcher(), ingesters={SourceType.RSS: ingester})

    def state_after_run() -> SourceState:
        run_pipeline(services, [SOURCE], extract=False, rank=False, publish=False)
        state = repository.get_source_state("gazette")
        assert state is not None
        return state

    first = state_after_run()
    assert (first.consecutive_failures, first.last_error) == (1, "ValueError: feed is broken")
    assert first.last_success_at is None
    assert state_after_run().consecutive_failures == 2

    ok = state_after_run()
    assert (ok.consecutive_failures, ok.last_error) == (0, None)
    assert ok.last_success_at is not None

    failed_again = state_after_run()
    assert failed_again.consecutive_failures == 1
    assert failed_again.last_success_at == ok.last_success_at  # a failure keeps the last success
    assert failed_again.last_status == 200

    not_modified = state_after_run()
    assert (not_modified.consecutive_failures, not_modified.last_error) == (0, None)
    assert not_modified.last_success_at is not None
    assert not_modified.last_success_at >= ok.last_success_at
    # The 304 is still reported as such, not as a fresh fetch.
    last = [r for r in log_records if r.getMessage() == "run finished"][-1]
    assert last.metrics["feeds"]["gazette"]["outcome"] == "not_modified"  # type: ignore[attr-defined]


def test_failed_and_paywalled_articles_store_error_type(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    services = _services(tmp_path, repository, _fetcher())

    run_pipeline(services, [SOURCE], rank=False, publish=False)

    by_url = _by_url(repository)
    failed = by_url["https://news.example.com/2026/10/missing"]
    assert failed.extraction.status is ExtractionStatus.FAILED
    assert failed.extraction.error_type == "http_4xx"
    others = [a for a in by_url.values() if a is not failed]
    assert others
    assert all(a.extraction.error_type is None for a in others)


def test_paywalled_article_stores_paywall_error_type(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    stored, _, _ = _paywall_run(
        tmp_path,
        repository,
        "https://news.example.com/a/1",
        FakeResponse(b"Subscription required", status=402),
    )

    assert stored.extraction.status is ExtractionStatus.PAYWALLED
    assert stored.extraction.error_type == "paywall"


def test_stored_run_stats_hold_durations_failures_and_medians(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    services = _services(tmp_path, repository, _fetcher())

    stats = run_pipeline(services, [SOURCE])

    stored = repository.get_run_stats(stats.run_id)
    assert stored is not None
    assert stored == stats
    assert set(stored.stage_durations_ms) == set(PipelineStage)
    assert stored.failures_by_reason == {"http_4xx": 1}
    assert stored.median_word_count_by_domain["news.example.com"] > 0


def test_skipped_stages_have_no_duration(tmp_path: Path, repository: SqliteRepository) -> None:
    services = _services(tmp_path, repository, _fetcher())

    stats = run_pipeline(services, [SOURCE], extract=False, rank=False, publish=False)

    assert set(stats.stage_durations_ms) == {PipelineStage.INGEST}


def test_report_lists_a_failed_article_from_a_real_run(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    """Regression: a failed article from a real run appears in the report."""
    run_pipeline(_services(tmp_path, repository, _fetcher()), [SOURCE])

    report = build_report(repository, [SOURCE.id])

    urls = [u for g in report.failures for u in g.urls]
    assert "https://news.example.com/2026/10/missing" in urls


# --- Retries -----------------------------------------------------------------------

RETRY_URLS = {
    "due": "https://r.example.com/a/1",  # transient failure, backoff elapsed
    "recent": "https://r.example.com/a/2",  # transient failure, tried minutes ago
    "gone": "https://r.example.com/a/3",  # permanent failure
    "capped": "https://r.example.com/a/4",  # transient, but out of retries
}
RETRY_NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def _failed(url: str, error_type: str, *, attempts: int, minutes_ago: int) -> Article:
    return _article_for(url).model_copy(
        update={
            "fetched_at": RETRY_NOW - timedelta(hours=10),
            "extraction": ArticleExtraction(
                status=ExtractionStatus.FAILED,
                error=f"PageUnavailableError: {error_type}",
                error_type=error_type,
                attempts=attempts,
                last_attempt_at=RETRY_NOW - timedelta(minutes=minutes_ago),
            ),
        }
    )


def _retry_run(tmp_path: Path, repository: SqliteRepository, **settings: object) -> RunStats:
    for article in (
        _failed(RETRY_URLS["due"], "http_5xx", attempts=1, minutes_ago=90),
        _failed(RETRY_URLS["recent"], "timeout", attempts=1, minutes_ago=5),
        _failed(RETRY_URLS["gone"], "http_4xx", attempts=1, minutes_ago=5000),
        _failed(RETRY_URLS["capped"], "timeout", attempts=4, minutes_ago=5000),
    ):
        repository.upsert_article(article)
    fetcher = FakeFetcher()
    for url in RETRY_URLS.values():
        fetcher.add(url, "<html><body><p>Invented.</p></body></html>", headers=HTML)
    services = _services(tmp_path, repository, fetcher, FakeExtractor(default=_ok()), **settings)
    return run_pipeline(
        services, [], ingest=False, rank=False, publish=False, clock=lambda: RETRY_NOW
    )


def test_extract_retries_only_eligible_failed_articles(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    stats = _retry_run(tmp_path, repository)

    by_url = _by_url(repository)
    due = by_url[RETRY_URLS["due"]].extraction
    assert due.status is ExtractionStatus.OK
    assert due.attempts == 2
    assert due.last_attempt_at == RETRY_NOW
    for name in ("recent", "gone", "capped"):
        untouched = by_url[RETRY_URLS[name]].extraction
        assert untouched.status is ExtractionStatus.FAILED
        assert untouched.last_attempt_at != RETRY_NOW
    counts = stats.stage_counts[PipelineStage.EXTRACT]
    assert counts["attempted"] == 1
    assert counts["retried"] == 1


def test_retries_can_be_disabled(tmp_path: Path, repository: SqliteRepository) -> None:
    stats = _retry_run(tmp_path, repository, retry=RetryPolicy(backoff_hours=()))

    assert stats.stage_counts[PipelineStage.EXTRACT] == {"attempted": 0}


def test_a_retry_that_fails_again_counts_the_attempt(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    url = RETRY_URLS["due"]
    repository.upsert_article(_failed(url, "http_5xx", attempts=1, minutes_ago=90))
    fetcher = FakeFetcher()
    fetcher.add(url, b"", status=503)
    services = _services(tmp_path, repository, fetcher)

    run_pipeline(services, [], ingest=False, rank=False, publish=False, clock=lambda: RETRY_NOW)

    stored = _by_url(repository)[url].extraction
    assert stored.status is ExtractionStatus.FAILED
    assert (stored.attempts, stored.last_attempt_at) == (2, RETRY_NOW)


@pytest.mark.parametrize("failing", [False, True])
def test_every_first_attempt_records_attempts_and_time(
    tmp_path: Path, repository: SqliteRepository, failing: bool
) -> None:
    url = "https://r.example.com/a/9"
    repository.upsert_article(_article_for(url))
    fetcher = FakeFetcher()
    fetcher.add(url, b"" if failing else "<html><p>x</p></html>", status=503 if failing else 200)
    services = _services(tmp_path, repository, fetcher, FakeExtractor(default=_ok()))

    run_pipeline(services, [], ingest=False, rank=False, publish=False, clock=lambda: RETRY_NOW)

    stored = _by_url(repository)[url].extraction
    assert (stored.attempts, stored.last_attempt_at) == (1, RETRY_NOW)


def test_skipped_attempts_are_counted(tmp_path: Path, repository: SqliteRepository) -> None:
    pdf = "https://r.example.com/files/report.pdf"
    repository.upsert_article(_article_for(pdf))
    fetcher = FakeFetcher()
    fetcher.add(pdf, b"%PDF-1.7", headers={"content-type": "application/pdf"})
    services = _services(tmp_path, repository, fetcher)

    run_pipeline(services, [], ingest=False, rank=False, publish=False, clock=lambda: RETRY_NOW)

    stored = _by_url(repository)[pdf].extraction
    assert stored.status is ExtractionStatus.SKIPPED
    assert (stored.attempts, stored.last_attempt_at) == (1, RETRY_NOW)


# --- Retrying partial articles keeps the better result -----------------------------

PARTIAL_POLICY = RetryPolicy(retry_partial=True)
OLD_TEXT = "<p>Earlier partial body, invented.</p>"


def _stored_partial(
    services: Services, url: str, *, words: int = 160, source_id: str = "s"
) -> Article:
    """A partial article, due a retry, whose earlier content sits in the blob store."""
    base = _article_for(url, source_id)
    ref = services.blobs.put(f"articles/{base.id}.html", OLD_TEXT.encode(), "text/html")
    article = base.model_copy(
        update={
            "fetched_at": RETRY_NOW - timedelta(hours=10),
            "content_ref": ref,
            "extraction": ArticleExtraction(
                status=ExtractionStatus.PARTIAL,
                method="earlier",
                word_count=words,
                attempts=1,
                last_attempt_at=RETRY_NOW - timedelta(minutes=90),
            ),
        }
    )
    services.repository.upsert_article(article)
    return article


def _retry_partial(services: Services, **kwargs: object) -> RunStats:
    return run_pipeline(
        services,
        [],
        ingest=False,
        rank=False,
        publish=False,
        clock=lambda: RETRY_NOW,
        **kwargs,  # type: ignore[arg-type]
    )


def _kept_unchanged(services: Services, url: str, *, words: int = 160) -> None:
    stored = {a.url: a for a in services.repository.list_articles()}[url]
    assert stored.extraction.status is ExtractionStatus.PARTIAL
    assert stored.extraction.word_count == words
    assert stored.extraction.method == "earlier"
    assert stored.content_ref is not None
    assert services.blobs.get(stored.content_ref).decode() == OLD_TEXT
    assert (stored.extraction.attempts, stored.extraction.last_attempt_at) == (2, RETRY_NOW)


@pytest.mark.parametrize(
    "response",
    [
        {"status": None, "error": "timeout"},
        {"status": 503},
        {"status": 200, "body": "<html><p>x</p></html>"},  # the extractor then gives a shorter page
    ],
    ids=["timeout", "server-error", "thinner-page"],
)
def test_a_worse_retry_keeps_the_partial_articles_text_and_status(
    tmp_path: Path, repository: SqliteRepository, response: dict[str, object]
) -> None:
    url = "https://r.example.com/p/1"
    fetcher = FakeFetcher()
    fetcher.add(
        url,
        response.get("body", b""),  # type: ignore[arg-type]
        status=response["status"],  # type: ignore[arg-type]
        error=response.get("error"),  # type: ignore[arg-type]
        headers=HTML,
    )
    thinner = _ok("Thin.", status=ExtractionStatus.PARTIAL, word_count=30)
    services = _services(
        tmp_path, repository, fetcher, FakeExtractor(default=thinner), retry=PARTIAL_POLICY
    )
    _stored_partial(services, url)

    _retry_partial(services)

    _kept_unchanged(services, url)


def test_a_worse_retry_still_records_the_latest_error(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    url = "https://r.example.com/p/2"
    fetcher = FakeFetcher()
    fetcher.add(url, b"", status=503)
    services = _services(tmp_path, repository, fetcher, retry=PARTIAL_POLICY)
    _stored_partial(services, url)

    _retry_partial(services)

    stored = _by_url(repository)[url].extraction
    assert stored.error is not None
    assert "503" in stored.error


def test_a_retry_that_fails_to_extract_keeps_the_partial_article(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    url = "https://r.example.com/p/3"
    fetcher = FakeFetcher()
    fetcher.add(url, "<html><p>x</p></html>", headers=HTML)
    failing = ExtractionResult(status=ExtractionStatus.FAILED, error="no content found")
    services = _services(
        tmp_path, repository, fetcher, FakeExtractor(default=failing), retry=PARTIAL_POLICY
    )
    _stored_partial(services, url)

    _retry_partial(services)

    _kept_unchanged(services, url)


@pytest.mark.parametrize(
    ("result", "status", "words"),
    [
        (
            _ok("Longer partial body.", status=ExtractionStatus.PARTIAL, word_count=240),
            "partial",
            240,
        ),
        (_ok("Full body.", word_count=500), "ok", 500),
    ],
    ids=["longer-partial", "ok"],
)
def test_a_better_retry_replaces_the_partial_article(
    tmp_path: Path,
    repository: SqliteRepository,
    result: ExtractionResult,
    status: str,
    words: int,
) -> None:
    url = "https://r.example.com/p/4"
    fetcher = FakeFetcher()
    fetcher.add(url, "<html><p>x</p></html>", headers=HTML)
    services = _services(
        tmp_path, repository, fetcher, FakeExtractor(default=result), retry=PARTIAL_POLICY
    )
    _stored_partial(services, url)

    _retry_partial(services)

    stored = _by_url(repository)[url]
    assert stored.extraction.status.value == status
    assert stored.extraction.word_count == words
    assert (stored.extraction.attempts, stored.extraction.last_attempt_at) == (2, RETRY_NOW)
    assert stored.content_ref is not None
    assert result.text in services.blobs.get(stored.content_ref).decode()


def test_a_retry_with_the_same_length_partial_keeps_the_earlier_one(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    url = "https://r.example.com/p/5"
    fetcher = FakeFetcher()
    fetcher.add(url, "<html><p>x</p></html>", headers=HTML)
    same = _ok("Same length.", status=ExtractionStatus.PARTIAL, word_count=160)
    services = _services(
        tmp_path, repository, fetcher, FakeExtractor(default=same), retry=PARTIAL_POLICY
    )
    _stored_partial(services, url)

    _retry_partial(services)

    _kept_unchanged(services, url)


def test_a_failed_article_retried_into_a_failure_is_still_overwritten(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    """Failed articles behave as before: the new error replaces the old one."""
    url = RETRY_URLS["due"]
    repository.upsert_article(_failed(url, "timeout", attempts=1, minutes_ago=90))
    fetcher = FakeFetcher()
    fetcher.add(url, b"", status=503)
    services = _services(tmp_path, repository, fetcher)

    _retry_partial(services)

    stored = _by_url(repository)[url].extraction
    assert stored.error is not None
    assert "503" in stored.error
    assert stored.error_type == "http_5xx"
    assert stored.attempts == 2


def test_partial_retries_respect_the_limit_and_keep_the_better_result(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    fetcher = FakeFetcher()
    urls = ["https://r.example.com/p/6", "https://r.example.com/p/7"]
    for url in urls:
        fetcher.add(url, b"", status=503)
    services = _services(tmp_path, repository, fetcher, retry=PARTIAL_POLICY)
    for url in urls:
        _stored_partial(services, url)

    stats = _retry_partial(services, limit=1)

    counts = stats.stage_counts[PipelineStage.EXTRACT]
    assert (counts["attempted"], counts["retried"]) == (1, 1)
    stored = _by_url(repository)
    tried = [u for u in urls if stored[u].extraction.attempts == 2]
    assert len(tried) == 1
    _kept_unchanged(services, tried[0])
    untried = next(u for u in urls if u not in tried)
    assert stored[untried].extraction.last_attempt_at != RETRY_NOW


def test_partial_retries_respect_the_source_filter_and_keep_the_better_result(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    mine, other = "https://r.example.com/p/8", "https://r.example.com/p/9"
    fetcher = FakeFetcher()
    fetcher.add(mine, b"", status=503)
    fetcher.add(other, b"", status=503)
    services = _services(tmp_path, repository, fetcher, retry=PARTIAL_POLICY)
    _stored_partial(services, mine, source_id="wanted")
    _stored_partial(services, other, source_id="unwanted")

    stats = _retry_partial(services, extract_source_ids=["wanted"])

    assert stats.stage_counts[PipelineStage.EXTRACT]["retried"] == 1
    _kept_unchanged(services, mine)
    assert _by_url(repository)[other].extraction.attempts == 1
    assert [c.url for c in fetcher.calls] == [mine]


# --- Review follow-ups: every write path keeps the partial; mutation guards --------


def _fetcher_with(url: str, body: object = b"", **kwargs: object) -> FakeFetcher:
    fetcher = FakeFetcher()
    fetcher.add(url, body, **kwargs)  # type: ignore[arg-type]
    return fetcher


def test_a_non_html_retry_keeps_the_partial_article(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    url = "https://r.example.com/p/10"
    fetcher = _fetcher_with(url, b"{}", headers={"content-type": "application/json"})
    services = _services(tmp_path, repository, fetcher, retry=PARTIAL_POLICY)
    _stored_partial(services, url)

    _retry_partial(services)

    _kept_unchanged(services, url)
    error = _by_url(repository)[url].extraction.error
    assert error is not None
    assert "not an HTML page" in error


def test_a_paywalled_retry_keeps_the_partial_article(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    url = "https://r.example.com/p/11"
    services = _services(tmp_path, repository, _fetcher_with(url, status=402), retry=PARTIAL_POLICY)
    _stored_partial(services, url)

    _retry_partial(services)

    _kept_unchanged(services, url)
    error = _by_url(repository)[url].extraction.error
    assert error is not None
    assert "402" in error


def test_a_failed_article_retried_into_a_paywall_is_still_overwritten(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    url = RETRY_URLS["due"]
    repository.upsert_article(_failed(url, "timeout", attempts=1, minutes_ago=90))
    services = _services(tmp_path, repository, _fetcher_with(url, status=402))

    _retry_partial(services)

    stored = _by_url(repository)[url].extraction
    assert stored.status is ExtractionStatus.PAYWALLED
    assert stored.attempts == 2


def test_a_failed_article_retried_into_ok_is_replaced(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    url = RETRY_URLS["due"]
    repository.upsert_article(_failed(url, "timeout", attempts=1, minutes_ago=90))
    fetcher = _fetcher_with(url, "<html><p>x</p></html>", headers=HTML)
    services = _services(
        tmp_path, repository, fetcher, FakeExtractor(default=_ok("Fresh.", word_count=300))
    )

    _retry_partial(services)

    stored = _by_url(repository)[url].extraction
    assert (stored.status, stored.word_count) == (ExtractionStatus.OK, 300)


def test_a_kept_partial_records_the_error_category(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    url = "https://r.example.com/p/12"
    services = _services(tmp_path, repository, _fetcher_with(url, status=503), retry=PARTIAL_POLICY)
    _stored_partial(services, url)

    _retry_partial(services)

    assert _by_url(repository)[url].extraction.error_type == "http_5xx"


def test_a_kept_partial_records_the_extractors_error(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    url = "https://r.example.com/p/13"
    failing = ExtractionResult(status=ExtractionStatus.FAILED, error="no content found")
    services = _services(
        tmp_path,
        repository,
        _fetcher_with(url, "<html><p>x</p></html>", headers=HTML),
        FakeExtractor(default=failing),
        retry=PARTIAL_POLICY,
    )
    _stored_partial(services, url)

    _retry_partial(services)

    assert _by_url(repository)[url].extraction.error == "no content found"
    assert _by_url(repository)[url].extraction.error_type == "other"


# --- Second review: refinement waits for a better result; keep_earlier is reached --------

CANONICAL_PAGE = (
    '<html><head><link rel="canonical" href="https://r.example.com/story/elsewhere">'
    "</head><body><p>x</p></body></html>"
)
THIN = ExtractionResult(status=ExtractionStatus.PARTIAL, text="thin", word_count=30)


def test_a_worse_retry_does_not_merge_the_partial_into_a_stored_canonical(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    url = "https://r.example.com/story/1"
    other = "https://r.example.com/story/elsewhere"
    repository.upsert_article(_failed(other, "timeout", attempts=3, minutes_ago=5))
    services = _services(
        tmp_path,
        repository,
        _fetcher_with(url, CANONICAL_PAGE, headers=HTML),
        FakeExtractor(default=THIN),
        retry=PARTIAL_POLICY,
    )
    _stored_partial(services, url)

    stats = _retry_partial(services)

    _kept_unchanged(services, url)
    assert len(repository.list_articles()) == 2
    assert "merged" not in stats.stage_counts["extract"]


def test_a_worse_retry_does_not_rekey_the_partial(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    url = "https://r.example.com/story/2"
    services = _services(
        tmp_path,
        repository,
        _fetcher_with(url, CANONICAL_PAGE, headers=HTML),
        FakeExtractor(default=THIN),
        retry=PARTIAL_POLICY,
    )
    before = _stored_partial(services, url)

    _retry_partial(services)

    _kept_unchanged(services, url)
    (stored,) = repository.list_articles()
    assert (stored.id, stored.canonical_url) == (before.id, before.canonical_url)


def test_a_better_retry_still_refines_the_canonical_url(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    url = "https://r.example.com/story/3"
    services = _services(
        tmp_path,
        repository,
        _fetcher_with(url, CANONICAL_PAGE, headers=HTML),
        FakeExtractor(default=_ok("Full text.", word_count=400)),
        retry=PARTIAL_POLICY,
    )
    _stored_partial(services, url)

    _retry_partial(services)

    (stored,) = repository.list_articles()
    assert stored.canonical_url == "https://r.example.com/story/elsewhere"
    assert stored.extraction.status is ExtractionStatus.OK


def test_a_longer_partial_retry_refines_the_canonical_url(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    url = "https://r.example.com/story/5"
    longer = ExtractionResult(status=ExtractionStatus.PARTIAL, text="longer", word_count=240)
    services = _services(
        tmp_path,
        repository,
        _fetcher_with(url, CANONICAL_PAGE, headers=HTML),
        FakeExtractor(default=longer),
        retry=PARTIAL_POLICY,
    )
    _stored_partial(services, url)

    _retry_partial(services)

    (stored,) = repository.list_articles()
    assert stored.canonical_url == "https://r.example.com/story/elsewhere"
    assert stored.extraction.word_count == 240


def test_a_better_retry_merges_into_a_stored_article_that_has_text(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    url = "https://r.example.com/story/6"
    other = "https://r.example.com/story/elsewhere"
    target = _article_for(other).model_copy(
        update={
            "extraction": ArticleExtraction(
                status=ExtractionStatus.OK, method="earlier", word_count=300
            )
        }
    )
    repository.upsert_article(target)
    services = _services(
        tmp_path,
        repository,
        _fetcher_with(url, CANONICAL_PAGE, headers=HTML),
        FakeExtractor(default=_ok("Full text.", word_count=400)),
        retry=PARTIAL_POLICY,
    )
    _stored_partial(services, url)

    stats = _retry_partial(services)

    (stored,) = repository.list_articles()
    assert stored.id == target.id
    assert stats.stage_counts["extract"]["merged"] == 1


class _FailingArticleBlobs(LocalBlobStore):
    """Fails writes of article bodies once ``failing`` is set."""

    failing = False

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        if self.failing and key.startswith("articles/"):
            raise OSError("disk full")
        return super().put(key, data, content_type)


def test_a_blob_failure_after_a_rekey_leaves_one_row_under_the_new_id(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    url = "https://r.example.com/story/7"
    blobs = _FailingArticleBlobs(tmp_path / "blobs")
    services = _services(
        tmp_path,
        repository,
        _fetcher_with(url, CANONICAL_PAGE, headers=HTML),
        FakeExtractor(default=_ok("Full text.", word_count=400)),
        blobs=blobs,
        retry=PARTIAL_POLICY,
    )
    _stored_partial(services, url)
    blobs.failing = True

    stats = _retry_partial(services)

    (stored,) = repository.list_articles()
    assert stored.canonical_url == "https://r.example.com/story/elsewhere"
    assert stored.extraction.status is ExtractionStatus.PARTIAL
    assert stored.extraction.word_count == 160
    assert stats.error_count == 1


def test_an_extractor_paywalled_retry_replaces_the_partial(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    url = "https://r.example.com/story/4"
    paywalled = ExtractionResult(status=ExtractionStatus.PAYWALLED, error="subscriber notice found")
    services = _services(
        tmp_path,
        repository,
        _fetcher_with(url, "<html><p>x</p></html>", headers=HTML),
        FakeExtractor(default=paywalled),
        retry=PARTIAL_POLICY,
    )
    _stored_partial(services, url)

    _retry_partial(services)

    stored = _by_url(repository)[url].extraction
    assert stored.status is ExtractionStatus.PAYWALLED
    assert stored.error == "subscriber notice found"


@pytest.mark.parametrize(
    ("result", "status"),
    [
        (ExtractionResult(status=ExtractionStatus.FAILED, error="no content found"), "failed"),
        (ExtractionResult(status=ExtractionStatus.PAYWALLED, error="notice"), "paywalled"),
    ],
    ids=["failed", "paywalled"],
)
def test_a_failed_article_retried_into_an_extractor_result_takes_its_status_and_error(
    tmp_path: Path, repository: SqliteRepository, result: ExtractionResult, status: str
) -> None:
    """Reaches the keep_earlier line (the fetch succeeds); it must not keep the old failure."""
    url = RETRY_URLS["due"]
    repository.upsert_article(_failed(url, "timeout", attempts=1, minutes_ago=90))
    services = _services(
        tmp_path,
        repository,
        _fetcher_with(url, "<html><p>x</p></html>", headers=HTML),
        FakeExtractor(default=result),
    )

    _retry_partial(services)

    stored = _by_url(repository)[url].extraction
    assert stored.status.value == status
    assert stored.error == result.error
    assert stored.attempts == 2


def test_extract_saved_url_keeps_a_stored_partial_against_a_thinner_page(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    url = "https://r.example.com/story/5"
    services = _services(
        tmp_path,
        repository,
        _fetcher_with(url, "<html><p>x</p></html>", headers=HTML),
        FakeExtractor(default=THIN),
    )
    _stored_partial(services, url)

    article = extract_saved_url(services, url, clock=lambda: RETRY_NOW)

    _kept_unchanged(services, url)
    assert article.extraction.status is ExtractionStatus.PARTIAL
