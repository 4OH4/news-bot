"""Build the concrete pieces of the pipeline from settings.

This is the one place that chooses adapters (CLAUDE.md, rule 1): stages receive
the ports through ``Services`` and never construct anything themselves.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from newsfeed.adapters.localfs_blobstore import LocalBlobStore
from newsfeed.adapters.sqlite_repository import SqliteRepository
from newsfeed.config import UrlParamsConfig, load_url_params
from newsfeed.extract.chain import ExtractorChain
from newsfeed.extract.newspaper_extractor import Newspaper4kExtractor
from newsfeed.extract.overrides import load_extraction_config
from newsfeed.extract.readability_extractor import ReadabilityExtractor
from newsfeed.extract.trafilatura_extractor import TrafilaturaExtractor
from newsfeed.fetching import HttpFetcher
from newsfeed.ingest.google_news import GoogleNewsIngester
from newsfeed.ingest.hn import HackerNewsIngester
from newsfeed.ingest.rss import RssIngester
from newsfeed.models import SourceType
from newsfeed.ports import BlobStore, Extractor, Fetcher, Repository, SourceIngester
from newsfeed.settings import Settings

__all__ = ["Services", "build_extractor", "build_ingesters", "open_fetcher", "open_services"]


@dataclass(frozen=True)
class Services:
    """Everything a pipeline run needs, behind the ports."""

    repository: Repository
    blobs: BlobStore
    fetcher: Fetcher
    ingesters: dict[SourceType, SourceIngester]
    extractor: Extractor
    url_rules: UrlParamsConfig
    concurrency: int = 8


def build_ingesters() -> dict[SourceType, SourceIngester]:
    """The ingester registry, keyed by source type. ``reddit`` has none yet."""
    ingesters: list[SourceIngester] = [RssIngester(), GoogleNewsIngester(), HackerNewsIngester()]
    return {ingester.source_type: ingester for ingester in ingesters}


def build_extractor(settings: Settings) -> Extractor:
    """The extractor chain: Trafilatura, then Readability, then newspaper4k.

    Per-domain overrides come from ``extraction.yaml``. The Playwright renderer
    (#15) isn't wired yet; domains asking for it log a warning and fall back to
    the static extractors.
    """
    return ExtractorChain(
        [TrafilaturaExtractor(), ReadabilityExtractor(), Newspaper4kExtractor()],
        load_extraction_config(settings.extraction_path),
    )


@contextmanager
def open_fetcher(settings: Settings) -> Iterator[HttpFetcher]:
    """Yield the polite HTTP fetcher configured from ``settings``, closing it after."""
    with HttpFetcher(settings.user_agent, min_interval=settings.host_interval) as fetcher:
        yield fetcher


@contextmanager
def open_services(settings: Settings) -> Iterator[Services]:
    """Build the services for a run and release the database and HTTP pool afterwards."""
    repository = SqliteRepository(settings.database_path)
    try:
        with open_fetcher(settings) as fetcher:
            yield Services(
                repository=repository,
                blobs=LocalBlobStore(settings.blob_dir),
                fetcher=fetcher,
                ingesters=build_ingesters(),
                extractor=build_extractor(settings),
                url_rules=load_url_params(settings.url_params_path),
                concurrency=settings.concurrency,
            )
    finally:
        repository.close()
