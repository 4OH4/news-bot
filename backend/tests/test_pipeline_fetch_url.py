"""The extract stage fetches an article's tracker-stripped URL (issue #273).

Some publishers tag every feed link with a tracker that their robots.txt refuses
while allowing the plain URL, so the fetch must use the canonical form. The
stored ``article.url`` stays as first seen.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from newsfeed.adapters.localfs_blobstore import LocalBlobStore
from newsfeed.adapters.sqlite_repository import SqliteRepository
from newsfeed.config import DomainParamRules, UrlParamsConfig, load_url_params
from newsfeed.ingest.canonical import normalise_url
from newsfeed.models import Article, ExtractionResult, ExtractionStatus
from newsfeed.pipeline import RunTally, extract_pending, extract_saved_url
from newsfeed.wiring import Services, build_ingesters
from tests.fakes import FakeExtractor, FakeFetcher, FakeResponse

HTML = {"content-type": "text/html; charset=utf-8"}
PAGE = FakeResponse(b"<html><body><p>Invented page.</p></body></html>", headers=HTML)
REPO_URL_PARAMS = Path(__file__).parents[2] / "config" / "url_params.yaml"


@pytest.fixture
def repository(tmp_path: Path) -> Iterator[SqliteRepository]:
    with SqliteRepository(tmp_path / "newsfeed.db") as repo:
        yield repo


def _ok() -> ExtractionResult:
    return ExtractionResult.model_validate(
        {
            "status": ExtractionStatus.OK,
            "title": "Extracted title",
            "content_html": "<p>Invented body text.</p>",
            "text": "Invented body text.",
            "word_count": 150,
        }
    )


def _services(
    tmp_path: Path,
    repository: SqliteRepository,
    fetcher: FakeFetcher,
    rules: UrlParamsConfig | None = None,
) -> Services:
    return Services(
        repository=repository,
        blobs=LocalBlobStore(tmp_path / "blobs"),
        fetcher=fetcher,
        ingesters=build_ingesters(),
        extractor=FakeExtractor(default=_ok()),
        url_rules=rules or UrlParamsConfig(),
        concurrency=1,
    )


def _store(repository: SqliteRepository, url: str) -> Article:
    # The canonical key is what ingest would have stored; the url is as first seen.
    article = Article(
        canonical_url=normalise_url(url),
        url=url,
        source_id="s",
        title="Feed title",
        fetched_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    repository.upsert_article(article)
    return article


def test_extract_fetches_the_stripped_url_that_robots_allows(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    raw = "https://news.example.com/story-123?ref=rss&utm_source=rss_feed"
    plain = "https://news.example.com/story-123"
    fetcher = FakeFetcher({plain: PAGE}, disallowed=["https://news.example.com/story-123?ref="])
    article = _store(repository, raw)
    services = _services(tmp_path, repository, fetcher)

    extract_pending(services, RunTally())

    assert fetcher.fetched_urls() == [plain]
    stored = repository.get_article(article.id)
    assert stored is not None
    assert stored.extraction.status is ExtractionStatus.OK
    assert stored.url == raw


def test_extract_applies_the_domain_rules_to_the_fetch_url(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    raw = "https://www.example.org/news/a?traffic_source=rss"
    plain = "https://www.example.org/news/a"
    rules = UrlParamsConfig(
        domains={"example.org": DomainParamRules(strip=frozenset({"traffic_source"}))}
    )
    fetcher = FakeFetcher({plain: PAGE})
    _store(repository, raw)

    extract_pending(_services(tmp_path, repository, fetcher, rules), RunTally())

    assert fetcher.fetched_urls() == [plain]


def test_extract_keeps_content_parameters_in_the_fetch_url(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    url = "https://example.com/view?id=42"
    fetcher = FakeFetcher({url: PAGE})
    _store(repository, url)

    extract_pending(_services(tmp_path, repository, fetcher), RunTally())

    assert fetcher.fetched_urls() == [url]


def test_extract_saved_url_fetches_the_stripped_url(
    tmp_path: Path, repository: SqliteRepository
) -> None:
    plain = "https://news.example.com/story-123"
    fetcher = FakeFetcher({plain: PAGE}, disallowed=["https://news.example.com/story-123?ref="])

    article = extract_saved_url(
        _services(tmp_path, repository, fetcher), "https://news.example.com/story-123?ref=rss"
    )

    assert fetcher.fetched_urls() == [plain]
    assert article.extraction.status is ExtractionStatus.OK


def test_repo_config_strips_al_jazeera_traffic_source() -> None:
    rules = load_url_params(REPO_URL_PARAMS)

    stripped = normalise_url(
        "https://www.aljazeera.com/news/2026/11/8/x?traffic_source=rss", rules=rules
    )

    assert stripped == "https://www.aljazeera.com/news/2026/11/8/x"
