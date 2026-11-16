"""Embeddings through the real pipeline, with the real local adapters."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from newsfeed.adapters.localfs_blobstore import LocalBlobStore
from newsfeed.adapters.sqlite_repository import SqliteRepository
from newsfeed.config import UrlParamsConfig
from newsfeed.models import Article, PipelineStage
from newsfeed.pipeline import run_pipeline
from newsfeed.wiring import Services
from tests.fakes import FakeEmbedder, FakeExtractor, FakeFetcher

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def services(repo: SqliteRepository, blobs: LocalBlobStore, embedder: FakeEmbedder) -> Services:
    return Services(
        repository=repo,
        blobs=blobs,
        fetcher=FakeFetcher(),
        ingesters={},
        extractor=FakeExtractor(),
        url_rules=UrlParamsConfig(),
        embedder=embedder,
    )


def article(name: str, **changes: object) -> Article:
    url = f"https://news.example.com/{name}"
    return Article.model_validate(
        {
            "canonical_url": url,
            "url": url,
            "source_id": "gazette",
            "title": f"Story {name}",
            "summary": f"Summary {name}",
            "fetched_at": NOW - timedelta(hours=1),
            **changes,
        }
    )


def test_a_pipeline_run_embeds_with_the_services_embedder(tmp_path: Path) -> None:
    with SqliteRepository(tmp_path / "n.db") as repo:
        blobs = LocalBlobStore(tmp_path / "blobs")
        repo.upsert_article(article("a"))
        embedder = FakeEmbedder()

        stats = run_pipeline(
            services(repo, blobs, embedder),
            [],
            ingest=False,
            extract=False,
            publish=False,
            clock=lambda: NOW,
        )

        assert stats.stage_counts[PipelineStage.RANK]["embedded"] == 1
        (stored,) = repo.list_articles()
        assert stored.embedding is not None
        assert stored.embedding_model == "fake-embedder"
