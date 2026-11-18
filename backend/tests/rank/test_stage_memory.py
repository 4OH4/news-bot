"""The rank stage fits in the job's memory: small batches, a per-run cap, a peak log (#376)."""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

import pytest

from newsfeed.models import Article
from newsfeed.rank.interests import InterestsConfig
from newsfeed.rank.stage import DEFAULT_EMBED_BATCH_SIZE, rank_recent
from newsfeed.wiring import Services
from tests.fakes import FakeEmbedder
from tests.rank.support import SMALL_PROFILE, config, feedback
from tests.rank.test_stage_embedding import NOW, SINCE, article, services, stored


def seed(s: Services, count: int) -> None:
    for n in range(count):
        s.repository.upsert_article(article(f"w{n:03d}"))


def rank(s: Services, embedder: FakeEmbedder, **kwargs: object) -> dict[str, int]:
    return rank_recent(
        s,
        InterestsConfig(),
        since=SINCE,
        clock=lambda: NOW,
        embedder=embedder,
        **kwargs,  # type: ignore[arg-type]
    ).counts()


def with_vector(s: Services) -> list[Article]:
    return [a for a in stored(s).values() if a.embedding is not None]


def test_default_batch_size_is_small_enough_for_a_gib() -> None:
    assert DEFAULT_EMBED_BATCH_SIZE <= 8


def test_no_embed_call_exceeds_the_default_batch_size() -> None:
    s = services()
    seed(s, 40)
    embedder = FakeEmbedder()

    rank(s, embedder)

    assert embedder.calls
    assert max(len(call) for call in embedder.calls) <= 8


def test_the_per_run_cap_drains_a_backlog_over_runs() -> None:
    s = services()
    seed(s, 300)
    embedder = FakeEmbedder()

    first = rank(s, embedder, max_embed_per_run=100)

    assert first["embedded"] == 100
    assert first["scored"] == 300  # the rest are scored without a vector
    assert len(with_vector(s)) == 100
    assert all(a.score is not None for a in stored(s).values())

    second = rank(s, embedder, max_embed_per_run=100)

    assert second["embedded"] == 100
    assert len(with_vector(s)) == 200  # the next 100, not the same ones again


def test_the_cap_counts_profile_backfill_and_window_embeddings(tmp_path: Path) -> None:
    s = services()
    old = timedelta(days=10)  # outside the rank window, so the profile backfills it
    for n in range(5):
        row = article(f"old{n}", fetched_at=NOW - old)
        s.repository.upsert_article(row)
        s.repository.save_feedback(feedback(row.id))
    seed(s, 5)
    embedder = FakeEmbedder()

    rank(s, embedder, personalisation=config(tmp_path, SMALL_PROFILE), max_embed_per_run=6)

    assert sum(len(call) for call in embedder.calls) == 6


def test_ranking_finished_log_carries_peak_rss_mb(caplog: pytest.LogCaptureFixture) -> None:
    s = services()
    seed(s, 2)

    with caplog.at_level(logging.INFO):
        rank(s, FakeEmbedder())

    record = next(r for r in caplog.records if r.getMessage() == "ranking finished")
    peak = getattr(record, "peak_rss_mb", None)
    assert isinstance(peak, int | float)
    assert peak > 0
