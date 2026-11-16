"""Tests for ``newsfeed embed backfill``, with a fake embedder in the services."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from newsfeed import cli, wiring
from newsfeed.adapters.localfs_blobstore import LocalBlobStore
from newsfeed.adapters.sqlite_repository import SqliteRepository
from newsfeed.models import Article, Feedback, FeedbackEvent
from newsfeed.settings import Settings
from tests.fakes import FakeEmbedder, FakeFetcher

runner = CliRunner()
STORED = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


@pytest.fixture
def embedder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeEmbedder:
    config = tmp_path / "config"
    config.mkdir()
    (config / "sources.yaml").write_text("sources: []\n")
    (config / "url_params.yaml").write_text("domains: {}\n")
    (config / "extraction.yaml").write_text("domains: {}\n")
    monkeypatch.setenv("NEWSFEED_CONFIG_DIR", str(config))
    monkeypatch.setenv("NEWSFEED_DATA_DIR", str(tmp_path / "data"))
    fake = FakeEmbedder()
    real = wiring.open_services

    @contextmanager
    def with_fake(settings: Settings) -> Iterator[wiring.Services]:
        with real(settings) as services:
            yield dataclasses.replace(services, embedder=fake)

    @contextmanager
    def open_fake(settings: object) -> Iterator[FakeFetcher]:
        yield FakeFetcher()

    monkeypatch.setattr(wiring, "open_fetcher", open_fake)
    monkeypatch.setattr(cli, "open_fetcher", open_fake)
    monkeypatch.setattr(cli, "open_services", with_fake)
    return fake


def seed(tmp_path: Path) -> Article:
    """Two articles with feedback: ``done`` already embedded, ``todo`` not. Returns ``todo``."""
    data = tmp_path / "data"
    blobs = LocalBlobStore(data / "blobs")
    ref = blobs.put("articles/todo.html", b"<p>Stored body of the second story.</p>")
    now = datetime.now(UTC)

    def make(name: str, **changes: object) -> Article:
        url = f"https://news.example.com/{name}"
        return Article.model_validate(
            {
                "canonical_url": url,
                "url": url,
                "source_id": "gazette",
                "title": f"Story {name}",
                "fetched_at": now - timedelta(days=2),
                **changes,
            }
        )

    done = make("done", embedding=STORED, embedding_model="fake-embedder")
    todo = make("todo", content_ref=ref)
    with SqliteRepository(data / "newsfeed.db") as repo:
        for a in (done, todo):
            repo.upsert_article(a)
            event = Feedback(
                article_id=a.id, event=FeedbackEvent.LIKE, created_at=now - timedelta(days=1)
            )
            repo.save_feedback(event)
    return todo


def test_backfill_embeds_only_feedback_articles_that_need_it(
    tmp_path: Path, embedder: FakeEmbedder
) -> None:
    todo = seed(tmp_path)

    result = runner.invoke(cli.app, ["embed", "backfill"])

    assert result.exit_code == 0, result.output
    assert len(embedder.calls) == 1
    assert len(embedder.calls[0]) == 1
    assert "Stored body of the second story." in embedder.calls[0][0]
    assert "Story todo" not in result.output  # counts only: no reading history
    assert todo.id not in result.output
    with SqliteRepository(tmp_path / "data" / "newsfeed.db") as repo:
        after = {a.title: a for a in repo.list_articles()}
    assert after["Story todo"].embedding is not None
    assert after["Story todo"].embedding_model == "fake-embedder"
    assert after["Story done"].embedding == pytest.approx(STORED)


def test_backfill_dry_run_writes_nothing(tmp_path: Path, embedder: FakeEmbedder) -> None:
    seed(tmp_path)

    result = runner.invoke(cli.app, ["embed", "backfill", "--dry-run"])

    assert result.exit_code == 0, result.output
    with SqliteRepository(tmp_path / "data" / "newsfeed.db") as repo:
        assert all(a.embedding is None for a in repo.list_articles() if a.title == "Story todo")
