"""Tests for embedding articles in the rank stage."""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from newsfeed.adapters.localfs_blobstore import LocalBlobStore
from newsfeed.adapters.sqlite_repository import SqliteRepository
from newsfeed.config import UrlParamsConfig
from newsfeed.models import Article
from newsfeed.ports import BlobStore, Embedder, EmbeddingError, Repository
from newsfeed.rank.interests import InterestsConfig
from newsfeed.rank.stage import rank_recent
from newsfeed.wiring import Services
from tests.fakes import (
    FakeEmbedder,
    FakeExtractor,
    FakeFetcher,
    InMemoryBlobStore,
    InMemoryRepository,
)

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(hours=72)
STORED = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


class CountingRepository(InMemoryRepository):
    """Records every ``upsert_article`` call, to show one write per article."""

    def __init__(self) -> None:
        super().__init__()
        self.upserts: list[str] = []

    def upsert_article(self, article: Article) -> None:
        self.upserts.append(article.title)
        super().upsert_article(article)


class BrokenEmbedder:
    """An embedder that fails every batch."""

    model = "fake-embedder"
    dimensions = 8

    def __init__(self, error: Exception) -> None:
        self.error = error

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise self.error


class BadVectorEmbedder(FakeEmbedder):
    """Returns ``bad`` for any text that mentions "Bad", a good vector otherwise."""

    def __init__(self, bad: list[float]) -> None:
        super().__init__()
        self.bad = bad

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        good = super().embed(texts)
        return [
            self.bad if "Bad" in text else vector for text, vector in zip(texts, good, strict=True)
        ]


def services(repository: Repository | None = None, blobs: BlobStore | None = None) -> Services:
    return Services(
        repository=repository or InMemoryRepository(),
        blobs=blobs or InMemoryBlobStore(),
        fetcher=FakeFetcher(),
        ingesters={},
        extractor=FakeExtractor(),
        url_rules=UrlParamsConfig(),
    )


def article(name: str, **changes: object) -> Article:
    url = f"https://news.example.com/{name}"
    return Article.model_validate(
        {
            "canonical_url": url,
            "url": url,
            "source_id": "gazette",
            "title": f"Story {name}",
            "fetched_at": NOW - timedelta(hours=1),
            **changes,
        }
    )


def run(s: Services, embedder: Embedder | None) -> dict[str, int]:
    return rank_recent(
        s, InterestsConfig(), since=SINCE, clock=lambda: NOW, embedder=embedder
    ).counts()


def stored(s: Services) -> dict[str, Article]:
    return {a.title: a for a in s.repository.list_articles()}


def test_embeds_missing_articles_in_the_same_write_as_the_score() -> None:
    repo = CountingRepository()
    s = services(repo)
    ref = s.blobs.put("articles/a.html", b"<p>Opening words about bridges.</p>")
    for name in "abc":
        repo.upsert_article(article(name, content_ref=ref if name == "a" else None))
    repo.upserts.clear()
    embedder = FakeEmbedder()

    counts = run(s, embedder)

    assert counts["embedded"] == 3
    assert counts["embed_errors"] == 0
    assert sorted(repo.upserts) == ["Story a", "Story b", "Story c"]  # one write each
    for a in stored(s).values():
        assert a.embedding is not None
        assert len(a.embedding) == 8
        assert a.embedding_model == "fake-embedder"
        assert a.score is not None
    assert any("Opening words about bridges." in t for call in embedder.calls for t in call)


def test_an_article_with_a_current_embedding_is_left_alone() -> None:
    s = services()
    s.repository.upsert_article(article("a", embedding=STORED, embedding_model="fake-embedder"))
    embedder = FakeEmbedder()

    counts = run(s, embedder)

    assert embedder.calls == []
    assert counts["embedded"] == 0
    assert stored(s)["Story a"].embedding == pytest.approx(STORED)


def test_an_article_from_another_model_is_embedded_again() -> None:
    s = services()
    s.repository.upsert_article(article("a", embedding=STORED, embedding_model="older-model"))
    embedder = FakeEmbedder()

    run(s, embedder)

    assert len(embedder.calls) == 1
    assert stored(s)["Story a"].embedding_model == "fake-embedder"


@pytest.mark.parametrize("error", [EmbeddingError("model missing"), RuntimeError("boom")])
def test_a_failed_batch_leaves_articles_scored_without_embeddings(error: Exception) -> None:
    s = services()
    for name in "abc":
        s.repository.upsert_article(article(name))

    counts = run(s, BrokenEmbedder(error))

    assert counts["embed_errors"] == 3
    assert counts["embedded"] == 0
    assert counts["scored"] == 3
    assert all(a.embedding is None and a.score is not None for a in stored(s).values())


def test_a_failed_batch_is_logged_with_stage_and_error_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    s = services()
    s.repository.upsert_article(article("a"))

    with caplog.at_level(logging.WARNING):
        run(s, BrokenEmbedder(EmbeddingError("model missing")))

    record = next(r for r in caplog.records if getattr(r, "error_type", "") == "EmbeddingError")
    assert record.stage == "rank"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "bad",
    [
        [math.nan] * 8,
        [math.inf] + [0.0] * 7,
        [1e39] + [0.0] * 7,  # finite, but overflows float32 when packed
        [0.0] * 8,
    ],
    ids=["nan", "inf", "overflow", "zero"],
)
def test_a_bad_vector_fails_that_article_and_the_run_continues(bad: list[float]) -> None:
    s = services()
    s.repository.upsert_article(article("good"))
    s.repository.upsert_article(article("Bad"))

    counts = run(s, BadVectorEmbedder(bad))

    result = stored(s)
    assert result["Story good"].embedding is not None
    assert result["Story Bad"].embedding is None
    assert result["Story Bad"].score is not None
    assert counts["embedded"] == 1
    assert counts["embed_errors"] == 1


def test_batches_are_of_32_texts() -> None:
    s = services()
    for n in range(70):
        s.repository.upsert_article(article(f"n{n:02d}"))
    embedder = FakeEmbedder()

    counts = run(s, embedder)

    assert [len(call) for call in embedder.calls] == [32, 32, 6]
    assert counts["embedded"] == 70


def test_without_an_embedder_it_warns_once_and_ranks_as_before(
    caplog: pytest.LogCaptureFixture,
) -> None:
    s = services()
    for name in "abc":
        s.repository.upsert_article(article(name))

    with caplog.at_level(logging.WARNING):
        counts = run(s, None)

    warnings = [
        r for r in caplog.records if r.levelno == logging.WARNING and "embedding" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert counts["scored"] == 3
    assert counts["embedded"] == 0
    assert all(a.embedding is None for a in stored(s).values())


def test_log_records_never_hold_a_vector(caplog: pytest.LogCaptureFixture) -> None:
    s = services()
    for name in "abc":
        s.repository.upsert_article(article(name))

    with caplog.at_level(logging.DEBUG):
        run(s, FakeEmbedder())
        run(s, BrokenEmbedder(EmbeddingError("no")))

    assert caplog.records
    for record in caplog.records:
        for value in [record.getMessage(), *vars(record).values()]:
            assert not (isinstance(value, list) and any(isinstance(v, float) for v in value))
            assert not re.search(r"\d\.\d+, -?\d\.\d+, -?\d\.\d+", str(value))


def test_embeddings_survive_storage_and_a_second_run_does_not_repeat_them(
    tmp_path: Path,
) -> None:
    with SqliteRepository(tmp_path / "n.db") as repo:
        s = services(repo, LocalBlobStore(tmp_path / "blobs"))
        ref = s.blobs.put("articles/a.html", b"<p>Some body text.</p>")
        repo.upsert_article(article("a", content_ref=ref))
        repo.upsert_article(article("b"))

        assert run(s, FakeEmbedder())["embedded"] == 2
        second = FakeEmbedder()
        counts = run(s, second)

        assert second.calls == []
        assert counts["embedded"] == 0
        assert counts["scored"] == 2
        for a in repo.list_articles():
            assert a.embedding is not None
            assert a.embedding_model == "fake-embedder"
