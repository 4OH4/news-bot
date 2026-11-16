"""The rank stage clusters near-duplicate stories and applies the coverage factor (#324)."""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from newsfeed.adapters.localfs_blobstore import LocalBlobStore
from newsfeed.adapters.sqlite_repository import SqliteRepository
from newsfeed.models import Article, PipelineStage
from newsfeed.pipeline import run_pipeline
from newsfeed.rank.interests import InterestsConfig, SourceRule
from newsfeed.rank.personalisation import PersonalisationConfig
from newsfeed.rank.stage import RankSummary, rank_recent
from newsfeed.wiring import Services
from tests.fakes import InMemoryRepository
from tests.rank.support import NOW
from tests.rank.test_stage_profile import ScriptedEmbedder, article, services

GAMMA = 0.25
# Breakdown keys that are not factors of ``score`` (with no topics configured, the rule
# score is the product of the others).
MARKERS = {"coverage_sources", "similarity", "similarity_raw"}
THREE = (("one", "gazette"), ("two", "herald"), ("three", "courier"))


class SkippingEmbedder(ScriptedEmbedder):
    """Returns no usable vector for an article whose title says "broken"."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = super().embed(texts)
        return [[] if "broken" in text else v for text, v in zip(texts, vectors, strict=True)]


def story(name: str, source: str, group: str = "alpha", **changes: object) -> Article:
    """An article of a vector group: the same group means the same embedding."""
    return article(group, name, source_id=source, **changes)


def run(
    s: Services,
    *,
    now: datetime = NOW,
    interests: InterestsConfig | None = None,
    embedder: ScriptedEmbedder | None = None,
) -> RankSummary:
    return rank_recent(
        s,
        interests or InterestsConfig(),
        since=now - timedelta(hours=72),
        clock=lambda: now,
        embedder=embedder or ScriptedEmbedder(),
        personalisation=PersonalisationConfig(),
    )


def stored(s: Services) -> dict[str, Article]:
    return {a.title: a for a in s.repository.list_articles()}


def recomputed(a: Article) -> float:
    return math.prod(v for k, v in a.score_breakdown.items() if k not in MARKERS)


def test_a_story_covered_by_three_sources_lifts_each_score() -> None:
    s = services(InMemoryRepository())
    for name, source in THREE:
        s.repository.upsert_article(story(name, source))
    s.repository.upsert_article(story("lone", "tribune", group="beta"))  # the control

    summary = run(s)

    got = stored(s)
    control = got["Story beta lone"]
    assert control.score is not None
    assert control.cluster_id == control.id
    assert "coverage" not in control.score_breakdown
    factor = 1 + GAMMA * math.log(3)
    members = [got[f"Story alpha {name}"] for name, _ in THREE]
    assert len({a.cluster_id for a in members}) == 1
    for a in members:
        assert a.score == pytest.approx(control.score * factor)
        assert a.score_breakdown["coverage"] == pytest.approx(factor)
        assert a.score_breakdown["coverage_sources"] == 3
    assert summary.counts()["clusters_multi"] == 1


def test_the_score_can_be_recomputed_from_the_breakdown_factors() -> None:
    s = services(InMemoryRepository())
    for name, source in THREE:
        s.repository.upsert_article(story(name, source))
    s.repository.upsert_article(story("lone", "tribune", group="beta"))

    run(s)

    for a in stored(s).values():
        assert ("coverage" in a.score_breakdown) == (a.title != "Story beta lone")
        assert a.score == pytest.approx(recomputed(a))


def test_two_articles_from_one_source_share_a_cluster_without_a_coverage_factor() -> None:
    s = services(InMemoryRepository())
    for name in ("one", "two"):
        s.repository.upsert_article(story(name, "gazette"))

    summary = run(s)

    one, two = stored(s)["Story alpha one"], stored(s)["Story alpha two"]
    assert one.cluster_id is not None
    assert one.cluster_id == two.cluster_id
    for a in (one, two):
        assert "coverage" not in a.score_breakdown
        assert "coverage_sources" not in a.score_breakdown
    assert summary.counts()["clusters_multi"] == 0


def test_an_article_without_an_embedding_has_no_cluster_and_no_coverage() -> None:
    s = services(InMemoryRepository())
    s.repository.upsert_article(story("one", "gazette"))
    s.repository.upsert_article(story("two", "herald"))
    s.repository.upsert_article(story("broken", "courier"))

    run(s, embedder=SkippingEmbedder())

    got = stored(s)
    broken = got["Story alpha broken"]
    assert broken.embedding is None
    assert broken.cluster_id is None
    assert "coverage" not in broken.score_breakdown
    assert got["Story alpha one"].score_breakdown["coverage_sources"] == 2


def test_a_muted_article_in_a_covered_story_stays_at_zero() -> None:
    s = services(InMemoryRepository())
    s.repository.upsert_article(story("one", "gazette"))
    s.repository.upsert_article(story("two", "herald"))

    run(s, interests=InterestsConfig(sources={"herald": SourceRule(mute=True)}))

    got = stored(s)
    assert got["Story alpha two"].score == 0
    assert got["Story alpha one"].cluster_id is not None
    assert got["Story alpha two"].cluster_id == got["Story alpha one"].cluster_id


def test_clusters_are_recomputed_and_scores_do_not_compound_across_runs(tmp_path: Path) -> None:
    age = timedelta(hours=2)
    with SqliteRepository(tmp_path / "n.db") as repo:
        s = services(repo, LocalBlobStore(tmp_path / "blobs"))
        for name, source in THREE:
            repo.upsert_article(story(name, source, age=age))
        repo.upsert_article(story("lone", "tribune", group="beta", age=age))

        run(s)
        first = stored(s)["Story alpha one"]
        assert first.score_breakdown["coverage_sources"] == 3

        # A fourth source reports the story, earlier than the others, before the next run.
        # Published at the same time as the rest, so its recency, and so its rule score, matches.
        zero = story("zero", "bulletin", age=timedelta(hours=3), published_at=NOW - age)
        repo.upsert_article(zero)
        run(s, now=NOW + timedelta(hours=1))

        got = stored(s)
        control = got["Story beta lone"].score
        assert control is not None
        factor = 1 + GAMMA * math.log(4)
        earliest = got["Story alpha zero"].id
        assert first.cluster_id != earliest
        for name in ("zero", "one", "two", "three"):
            a = got[f"Story alpha {name}"]
            assert a.cluster_id == earliest
            assert a.score_breakdown["coverage_sources"] == 4
            # The factor applies to this run's fresh rule score, not the first run's boosted one.
            assert a.score == pytest.approx(control * factor)


def test_the_pipeline_clusters_stories_and_counts_them_in_the_run_stats() -> None:
    repo = InMemoryRepository()
    for name, source in THREE[:2]:
        repo.upsert_article(story(name, source))
    s = dataclasses.replace(services(repo), embedder=ScriptedEmbedder())

    stats = run_pipeline(s, [], ingest=False, extract=False, publish=False, clock=lambda: NOW)

    assert stats.stage_counts[PipelineStage.RANK]["clusters_multi"] == 1
    assert all(a.score_breakdown["coverage_sources"] == 2 for a in repo.list_articles())
