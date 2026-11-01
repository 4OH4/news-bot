"""One story, one feed item: rows linked by the canonical URL their page declared (#147).

A page can declare another stored row as its canonical address. Extract keeps
both rows whenever the target has no readable text, and records the declared
address on the page's own row. Publish then shows one item per story: the row
with readable text, or the canonical target if both have it.

Everything runs through ``run_pipeline`` with the real SQLite and filesystem
adapters, and is judged on the ``feed.json`` it writes. The name of the stored
field is left to the implementation.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from newsfeed.adapters.localfs_blobstore import LocalBlobStore
from newsfeed.adapters.sqlite_repository import SqliteRepository
from newsfeed.config import UrlParamsConfig
from newsfeed.extract.retry import RetryPolicy
from newsfeed.models import (
    Article,
    ArticleExtraction,
    ExtractionResult,
    ExtractionStatus,
    RunStats,
    Source,
    SourceType,
)
from newsfeed.pipeline import run_pipeline
from newsfeed.ports import Extractor
from newsfeed.publish.feed import FEED_KEY, FEED_LIMIT
from newsfeed.wiring import Services, build_ingesters
from tests.fakes import FakeExtractor, FakeFetcher

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=2)  # past the first retry backoff of one hour
HTML = {"content-type": "text/html; charset=utf-8"}
FEED_URL = "https://news.example.com/feed.xml"
SOURCE = Source(id="gazette", type=SourceType.RSS, url=FEED_URL)
TARGET = "https://news.example.com/story"
ALIAS = "https://news.example.com/feed-link/1"
SECOND_ALIAS = "https://news.example.com/feed-link/2"
VALIDATOR = Draft202012Validator(
    json.loads((Path(__file__).parents[2] / "docs" / "feed.schema.json").read_text()),
    format_checker=Draft202012Validator.FORMAT_CHECKER,
)


def _page(canonical: str | None = None, *, body: str = "Invented page body.") -> str:
    link = f'<link rel="canonical" href="{canonical}">' if canonical else ""
    return f"<html><head>{link}</head><body><p>{body}</p></body></html>"


def _ok(text: str, words: int = 300) -> ExtractionResult:
    return ExtractionResult(
        status=ExtractionStatus.OK, content_html=f"<p>{text}</p>", text=text, word_count=words
    )


def _partial(text: str, words: int = 160) -> ExtractionResult:
    return ExtractionResult(
        status=ExtractionStatus.PARTIAL, content_html=f"<p>{text}</p>", text=text, word_count=words
    )


def _failed_result(error: str = "no usable content") -> ExtractionResult:
    return ExtractionResult(status=ExtractionStatus.FAILED, error=error)


def _row(
    url: str,
    *,
    source_id: str = "gazette",
    age_minutes: int = 60,
    extraction: ArticleExtraction | None = None,
) -> Article:
    """A stored row. ``list_articles`` is newest first, so age sets the extraction order."""
    return Article(
        canonical_url=url,
        url=url,
        source_id=source_id,
        title=f"Story at {url.rsplit('/', 1)[1]}",
        fetched_at=NOW - timedelta(minutes=age_minutes),
        extraction=extraction or ArticleExtraction(),
    )


def _failed_row(url: str = TARGET, *, error_type: str = "http_4xx") -> Article:
    """A failed row; the default category is never retried, ``timeout`` is."""
    return _row(
        url,
        extraction=ArticleExtraction(
            status=ExtractionStatus.FAILED,
            error=f"PageUnavailableError: {error_type}",
            error_type=error_type,
            attempts=1,
            last_attempt_at=NOW,
        ),
    )


def _pending_alias(url: str = ALIAS) -> Article:
    """A new row, younger than the target rows, so that it is extracted first."""
    return _row(url, age_minutes=30)


@pytest.fixture
def repository(tmp_path: Path) -> Iterator[SqliteRepository]:
    with SqliteRepository(tmp_path / "newsfeed.db") as repo:
        yield repo


def _services(
    tmp_path: Path,
    repository: SqliteRepository,
    fetcher: FakeFetcher,
    extractor: Extractor,
    **overrides: object,
) -> Services:
    values: dict[str, object] = {
        "repository": repository,
        "blobs": LocalBlobStore(tmp_path / "blobs"),
        "fetcher": fetcher,
        "ingesters": build_ingesters(),
        "extractor": extractor,
        "url_rules": UrlParamsConfig(),
        "concurrency": 1,
        **overrides,
    }
    return Services(**values)  # type: ignore[arg-type]


def _fetcher(pages: dict[str, str]) -> FakeFetcher:
    fetcher = FakeFetcher()
    for url, html in pages.items():
        fetcher.add(url, html, headers=HTML)
    return fetcher


def _run(services: Services, at: datetime = NOW, *, extract: bool = True) -> RunStats:
    return run_pipeline(services, [SOURCE], ingest=False, extract=extract, clock=lambda: at)


def _items(services: Services) -> list[dict[str, Any]]:
    feed = json.loads(services.blobs.get(FEED_KEY))
    VALIDATOR.validate(feed)
    items: list[dict[str, Any]] = feed["items"]
    return items


def _only_item(services: Services) -> dict[str, Any]:
    items = _items(services)
    assert len(items) == 1, [i["url"] for i in items]
    return items[0]


def _assert_shows(services: Services, url: str, text: str) -> None:
    """The feed is one full article: ``url``, whose stored content holds ``text``."""
    item = _only_item(services)
    assert item["url"] == url
    assert item["display"] == "article"
    assert text in services.blobs.get(item["content_path"]).decode()


# --- Acceptance: a failed target, shown once ---------------------------------------


def test_a_failed_target_that_recovers_between_runs_leaves_one_article_in_the_feed(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    """Both stories come from a real feed; the target is down in run 1, up in run 2."""
    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Example Gazette</title><link>https://news.example.com/</link>
  <description>Invented headlines.</description>
  <item><title>Marrowby bridge opens</title><link>{TARGET}</link>
    <guid isPermaLink="false">dup-1</guid></item>
  <item><title>Marrowby bridge opens (feed link)</title><link>{ALIAS}</link>
    <guid isPermaLink="false">dup-2</guid></item>
</channel></rss>"""
    fetcher = _fetcher({ALIAS: _page(TARGET)})
    fetcher.add(FEED_URL, rss, headers={"content-type": "application/rss+xml"})
    fetcher.add(TARGET, status=503)
    extractor = FakeExtractor(results={ALIAS: _ok("Copy body."), TARGET: _ok("Target body.")})
    services = _services(tmp_path, repository, fetcher, extractor)
    # Ingest stamps fetched_at from the real clock, so run close to it.
    first = datetime.now(UTC)

    run_pipeline(services, [SOURCE], clock=lambda: first)

    _assert_shows(services, ALIAS, "Copy body.")

    fetcher.add(TARGET, _page(TARGET), headers=HTML)
    run_pipeline(services, [SOURCE], clock=lambda: first + timedelta(hours=2))

    _assert_shows(services, TARGET, "Target body.")
    # The lead's design: publish drops the duplicate; no row is moved or deleted.
    assert {a.url for a in repository.list_articles()} == {ALIAS, TARGET}


def test_a_failed_target_and_a_kept_article_publish_as_one_item_with_the_text(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    repository.upsert_article(_failed_row())
    repository.upsert_article(_pending_alias())
    fetcher = _fetcher({ALIAS: _page(TARGET)})
    services = _services(
        tmp_path, repository, fetcher, FakeExtractor(results={ALIAS: _ok("Copy body.")})
    )

    _run(services)

    _assert_shows(services, ALIAS, "Copy body.")


# --- Acceptance: skipped and pending targets follow the #144 rule -------------------


def test_a_stored_skipped_target_does_not_swallow_an_ok_article(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    target = _row(
        TARGET,
        extraction=ArticleExtraction(status=ExtractionStatus.SKIPPED, error="summary only"),
    )
    repository.upsert_article(target)
    repository.upsert_article(_pending_alias())
    fetcher = _fetcher({ALIAS: _page(TARGET)})
    services = _services(
        tmp_path, repository, fetcher, FakeExtractor(results={ALIAS: _ok("Copy body.")})
    )

    _run(services)

    assert {a.url for a in repository.list_articles()} == {ALIAS, TARGET}
    stored_target = repository.get_article(target.id)
    assert stored_target is not None
    assert stored_target.extraction.status is ExtractionStatus.SKIPPED
    _assert_shows(services, ALIAS, "Copy body.")


def test_a_pending_target_that_then_fails_does_not_swallow_an_ok_article(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    repository.upsert_article(_row(TARGET))
    repository.upsert_article(_pending_alias())
    fetcher = _fetcher({ALIAS: _page(TARGET)})
    fetcher.add(TARGET, status=404)
    services = _services(
        tmp_path, repository, fetcher, FakeExtractor(results={ALIAS: _ok("Copy body.")})
    )

    _run(services)

    assert fetcher.calls[0].url == ALIAS  # the target was still pending at that point
    _assert_shows(services, ALIAS, "Copy body.")


# --- Which row publish chooses ------------------------------------------------------


def test_when_both_rows_have_text_the_feed_shows_the_canonical_target(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    repository.upsert_article(_row(TARGET))
    repository.upsert_article(_pending_alias())
    fetcher = _fetcher({ALIAS: _page(TARGET), TARGET: _page(TARGET)})
    extractor = FakeExtractor(results={ALIAS: _ok("Copy body."), TARGET: _ok("Target body.")})
    services = _services(tmp_path, repository, fetcher, extractor)

    _run(services)

    assert fetcher.calls[0].url == ALIAS
    assert {a.url for a in repository.list_articles()} == {ALIAS, TARGET}
    _assert_shows(services, TARGET, "Target body.")


def test_two_copies_of_a_failed_target_publish_as_one_item(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    repository.upsert_article(_failed_row())
    repository.upsert_article(_pending_alias(ALIAS))
    repository.upsert_article(_pending_alias(SECOND_ALIAS))
    fetcher = _fetcher({ALIAS: _page(TARGET), SECOND_ALIAS: _page(TARGET)})
    services = _services(tmp_path, repository, fetcher, FakeExtractor(default=_ok("Copy body.")))

    _run(services)

    item = _only_item(services)
    assert item["url"] in {ALIAS, SECOND_ALIAS}
    assert item["display"] == "article"


def test_a_copy_with_no_text_and_a_failed_target_publish_as_one_item(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    repository.upsert_article(_failed_row())
    repository.upsert_article(_pending_alias())
    fetcher = _fetcher({ALIAS: _page(TARGET)})
    services = _services(tmp_path, repository, fetcher, FakeExtractor(default=_failed_result()))

    _run(services)

    assert _only_item(services)["display"] == "link"


def test_a_copy_stays_in_the_feed_when_its_target_cannot_be_shown(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    """The target's source was disabled: dropping the copy too would lose the story."""
    repository.upsert_article(_row(TARGET, source_id="retired"))
    repository.upsert_article(_pending_alias())
    fetcher = _fetcher({ALIAS: _page(TARGET), TARGET: _page(TARGET)})
    extractor = FakeExtractor(results={ALIAS: _ok("Copy body."), TARGET: _ok("Target body.")})
    services = _services(tmp_path, repository, fetcher, extractor)

    _run(services)

    _assert_shows(services, ALIAS, "Copy body.")


class _LostContentBlobs(LocalBlobStore):
    """A store whose copy of one article's cleaned HTML has gone."""

    def __init__(self, root: Path, lost_id: str) -> None:
        super().__init__(root)
        self.lost_id = lost_id

    def exists(self, ref: str) -> bool:
        return self.lost_id not in ref and super().exists(ref)


def test_the_copy_is_shown_when_the_targets_content_has_gone(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    target = _row(TARGET)
    repository.upsert_article(target)
    repository.upsert_article(_pending_alias())
    fetcher = _fetcher({ALIAS: _page(TARGET), TARGET: _page(TARGET)})
    extractor = FakeExtractor(results={ALIAS: _ok("Copy body."), TARGET: _ok("Target body.")})
    blobs = _LostContentBlobs(tmp_path / "blobs", target.id)
    services = _services(tmp_path, repository, fetcher, extractor, blobs=blobs)

    _run(services)

    _assert_shows(services, ALIAS, "Copy body.")


def test_a_dropped_duplicate_does_not_use_up_a_place_in_the_feed(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    repository.upsert_article(_failed_row())
    repository.upsert_article(_pending_alias())
    for n in range(FEED_LIMIT + 5):
        filler = _row(
            f"https://other.example.com/{n}",
            age_minutes=600,
            extraction=ArticleExtraction(status=ExtractionStatus.OK, word_count=200),
        )
        repository.upsert_article(filler)
    fetcher = _fetcher({ALIAS: _page(TARGET)})
    services = _services(
        tmp_path, repository, fetcher, FakeExtractor(results={ALIAS: _ok("Copy body.")})
    )

    _run(services)

    urls = [i["url"] for i in _items(services)]
    assert ALIAS in urls
    assert TARGET not in urls
    assert len(urls) == FEED_LIMIT


# --- What extract records -----------------------------------------------------------


@pytest.mark.parametrize(
    "result",
    [_ok("Copy body."), _partial("Copy body."), _failed_result()],
    ids=["ok", "partial", "failed"],
)
def test_the_declared_canonical_is_stored_on_the_row_for_every_extraction_outcome(
    tmp_path: Path, repository: SqliteRepository, result: ExtractionResult
) -> None:
    repository.upsert_article(_failed_row())
    alias = _pending_alias()
    repository.upsert_article(alias)
    fetcher = _fetcher({ALIAS: _page(TARGET)})
    services = _services(tmp_path, repository, fetcher, FakeExtractor(default=result))

    _run(services)

    stored = repository.get_article(alias.id)
    assert stored is not None
    assert stored.extraction.status is result.status
    assert TARGET in stored.extraction.model_dump().values()


def test_a_declared_canonical_with_tracking_parameters_still_finds_its_target(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    repository.upsert_article(_failed_row())
    repository.upsert_article(_pending_alias())
    fetcher = _fetcher({ALIAS: _page(f"{TARGET}?utm_source=rss")})
    services = _services(
        tmp_path, repository, fetcher, FakeExtractor(results={ALIAS: _ok("Copy body.")})
    )

    _run(services)

    _assert_shows(services, ALIAS, "Copy body.")


def _partial_run(
    tmp_path: Path, repository: SqliteRepository
) -> tuple[FakeFetcher, dict[str, ExtractionResult]]:
    """Run 1: a partial copy of a failed target, on a policy that retries partials."""
    repository.upsert_article(_failed_row())
    repository.upsert_article(_pending_alias())
    fetcher = _fetcher({ALIAS: _page(TARGET)})
    results = {ALIAS: _partial("Copy body.")}
    services = _services(
        tmp_path,
        repository,
        fetcher,
        FakeExtractor(results=results),
        retry=RetryPolicy(retry_partial=True),
    )
    _run(services)
    return fetcher, results


def test_a_better_retry_of_a_kept_partial_still_publishes_as_one_item(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    fetcher, _ = _partial_run(tmp_path, repository)
    services = _services(
        tmp_path,
        repository,
        fetcher,
        FakeExtractor(results={ALIAS: _ok("Better body.", words=500)}),
        retry=RetryPolicy(retry_partial=True),
    )

    _run(services, LATER)

    _assert_shows(services, ALIAS, "Better body.")


def test_an_outage_on_retry_of_a_kept_partial_still_publishes_as_one_item(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    fetcher, results = _partial_run(tmp_path, repository)
    fetcher.add(ALIAS, status=503)
    services = _services(
        tmp_path,
        repository,
        fetcher,
        FakeExtractor(results=results),
        retry=RetryPolicy(retry_partial=True),
    )

    _run(services, LATER)

    _assert_shows(services, ALIAS, "Copy body.")


@pytest.mark.parametrize("status", [503, 402], ids=["outage", "paywall"])
def test_a_retry_that_never_reads_the_page_does_not_forget_the_declared_canonical(
    tmp_path: Path, repository: SqliteRepository, status: int
) -> None:
    """A failed copy records the canonical, then can't refetch it as its target recovers."""
    repository.upsert_article(_failed_row(error_type="timeout"))
    repository.upsert_article(_pending_alias())
    fetcher = _fetcher({ALIAS: _page(TARGET)})
    extractor = FakeExtractor(
        results={ALIAS: _failed_result("upstream timed out"), TARGET: _ok("Target body.")}
    )
    services = _services(tmp_path, repository, fetcher, extractor)
    _run(services)

    fetcher.add(ALIAS, status=status)
    fetcher.add(TARGET, _page(TARGET), headers=HTML)
    _run(services, LATER)

    _assert_shows(services, TARGET, "Target body.")


# --- Guards: these pass before #147 and must still pass after it --------------------


def test_a_rekeyed_article_that_declares_itself_stays_in_the_feed(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    """Its declared canonical is its own row, which must not count as a duplicate."""
    repository.upsert_article(_pending_alias())
    fetcher = _fetcher({ALIAS: _page(TARGET)})
    services = _services(tmp_path, repository, fetcher, FakeExtractor(default=_ok("Copy body.")))

    _run(services)

    _assert_shows(services, TARGET, "Copy body.")


def test_rows_stored_before_the_field_existed_are_left_as_they_are(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    """Nothing says these belong together, so both stay: no migration, no guessing."""
    kept = _row(ALIAS, extraction=ArticleExtraction(status=ExtractionStatus.OK, word_count=300))
    repository.upsert_article(kept)
    repository.upsert_article(_failed_row())
    services = _services(tmp_path, repository, FakeFetcher(), FakeExtractor())

    _run(services, extract=False)

    assert {i["url"] for i in _items(services)} == {ALIAS, TARGET}


def test_two_textless_pages_declaring_each_other_still_show_one_item(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    """A loop must not drop both rows: each one is the other's duplicate."""
    repository.upsert_article(_row(ALIAS))
    repository.upsert_article(_row(SECOND_ALIAS, age_minutes=70))
    fetcher = _fetcher({ALIAS: _page(SECOND_ALIAS), SECOND_ALIAS: _page(ALIAS)})
    services = _services(tmp_path, repository, fetcher, FakeExtractor(default=_failed_result()))

    _run(services)

    _only_item(services)
