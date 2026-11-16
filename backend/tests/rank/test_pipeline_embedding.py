"""Embeddings through the real pipeline and the backfill, with the real local adapters."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from newsfeed import cli
from newsfeed.adapters.localfs_blobstore import LocalBlobStore
from newsfeed.adapters.sqlite_repository import SqliteRepository
from newsfeed.config import UrlParamsConfig
from newsfeed.models import Article, Feedback, FeedbackEvent, PipelineStage
from newsfeed.pipeline import run_pipeline
from newsfeed.rank.stage import backfill_embeddings
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


def test_backfill_uses_title_and_summary_once_the_body_has_expired(tmp_path: Path) -> None:
    with SqliteRepository(tmp_path / "n.db") as repo:
        blobs = LocalBlobStore(tmp_path / "blobs")
        old = article("old", content_ref="articles/gone.html")  # blob already expired
        repo.upsert_article(old)
        yesterday = NOW - timedelta(days=1)
        repo.save_feedback(
            Feedback(article_id=old.id, event=FeedbackEvent.OPEN, created_at=yesterday)
        )
        repo.save_feedback(  # feedback on an article no longer stored
            Feedback(article_id="0" * 16, event=FeedbackEvent.OPEN, created_at=NOW)
        )
        embedder = FakeEmbedder()

        summary = backfill_embeddings(
            services(repo, blobs, embedder), embedder, since=NOW - timedelta(days=180)
        )

        assert embedder.calls == [["Story old\n\nSummary old"]]
        assert summary.counts() == {
            "articles": 2,
            "missing": 1,
            "current": 0,
            "embedded": 1,
            "errors": 0,
            "no_body": 1,
        }
        stored = repo.get_article(old.id)
        assert stored is not None
        assert stored.embedding is not None


def test_backfill_without_a_model_folder_says_how_to_get_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    for name, body in [
        ("sources.yaml", "sources: []\n"),
        ("url_params.yaml", "domains: {}\n"),
        ("extraction.yaml", "domains: {}\n"),
    ]:
        (config / name).write_text(body)
    monkeypatch.setenv("NEWSFEED_CONFIG_DIR", str(config))
    monkeypatch.setenv("NEWSFEED_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("NEWSFEED_EMBED_MODEL_DIR", "")

    result = CliRunner().invoke(cli.app, ["embed", "backfill"])

    assert result.exit_code == 1
    assert "embed download" in result.output
