"""The rank stage's privacy guards and rebuild trigger, on their failure paths (#358 review)."""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import pytest

from newsfeed.models import Article, Profile
from tests.fakes import InMemoryRepository
from tests.rank.support import NOW, SMALL_PROFILE, config, feedback
from tests.rank.test_stage_profile import (
    ScriptedEmbedder,
    article,
    old,
    preseeded_profile,
    rank,
    read_all,
    services,
)

SECRET_ERROR = "embedder-error-detail"
PRIVATE_SOURCE = "private-source"


class FailingEmbedder(ScriptedEmbedder):
    """Raises with the texts in its message, or returns vectors that fail the checks."""

    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.mode == "raise":
            raise RuntimeError(f"{SECRET_ERROR}: {' | '.join(texts)}")
        return [[math.nan] * self.dimensions for _ in texts]


def unreadable_feedback_article(name: str) -> Article:
    """An old feedback article whose stored body has gone, from a source worth hiding."""
    a = article("gamma", name, age=timedelta(days=10), source_id=PRIVATE_SOURCE)
    return a.model_copy(update={"content_ref": f"memory://articles/{a.id}.html"})


@pytest.mark.parametrize(
    ("mode", "embed_warning"),
    [("raise", "embedding failed"), ("unusable", "embedder returned an unusable vector")],
)
def test_failures_on_feedback_articles_log_nothing_about_them(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, mode: str, embed_warning: str
) -> None:
    repo = InMemoryRepository()
    feedback_articles = [unreadable_feedback_article(n) for n in ("one", "two", "three")]
    read_all(repo, feedback_articles)

    with caplog.at_level(logging.DEBUG):
        counts = rank(services(repo), config(tmp_path, SMALL_PROFILE), FailingEmbedder(mode))

    assert counts["profile_rebuilt"] == 1
    messages = [r.getMessage() for r in caplog.records]
    assert sum("couldn't read article content" in m for m in messages) == 3
    assert any(embed_warning in m for m in messages)
    forbidden = [SECRET_ERROR, PRIVATE_SOURCE]
    forbidden += [v for a in feedback_articles for v in (a.url, a.title, a.id)]
    for record in caplog.records:
        text = " ".join(str(v) for v in (record.getMessage(), *vars(record).values()))
        for value in forbidden:
            assert value not in text, (record.getMessage(), value)


def test_no_rebuild_without_newer_feedback_until_some_arrives(tmp_path: Path) -> None:
    repo = InMemoryRepository()
    read_all(repo, [old("alpha", "1"), old("alpha", "2")])
    s, cfg = services(repo), config(tmp_path, SMALL_PROFILE)

    assert rank(s, cfg)["profile_rebuilt"] == 1
    # Seven hours on, the build is old enough, but no feedback is newer than it used.
    assert rank(s, cfg, now=NOW + timedelta(hours=7))["profile_rebuilt"] == 0
    repo.save_feedback(feedback(old("alpha", "1").id, at=NOW + timedelta(hours=8)))
    assert rank(s, cfg, now=NOW + timedelta(hours=9))["profile_rebuilt"] == 1


def test_feedback_at_the_profiles_last_feedback_time_is_not_new(tmp_path: Path) -> None:
    repo = InMemoryRepository()
    read_all(repo, [old("alpha", "1"), old("alpha", "2")])  # both an hour before NOW
    repo.save_profile(
        preseeded_profile(built_ago=timedelta(hours=7), feedback_ago=timedelta(hours=1))
    )

    assert rank(services(repo), config(tmp_path, SMALL_PROFILE))["profile_rebuilt"] == 0


def test_a_profile_from_another_model_is_rebuilt_once_due(tmp_path: Path) -> None:
    repo = InMemoryRepository()
    read_all(repo, [old("alpha", "1"), old("alpha", "2")])
    stale = preseeded_profile(built_ago=timedelta(hours=7), feedback_ago=timedelta(hours=1))
    repo.save_profile(stale.model_copy(update={"embedding_model": "another-model"}))

    assert rank(services(repo), config(tmp_path, SMALL_PROFILE))["profile_rebuilt"] == 1


class BrokenProfileRepository(InMemoryRepository):
    def get_profile(self, user_id: str) -> Profile | None:
        raise RuntimeError("profile store unavailable")


def test_a_failed_profile_step_is_one_error_and_ranking_goes_on(tmp_path: Path) -> None:
    repo = BrokenProfileRepository()
    repo.upsert_article(article("alpha", "w"))

    counts = rank(services(repo), config(tmp_path, SMALL_PROFILE))

    assert counts["errors"] == 1
    assert counts["scored"] == 1
    assert counts["with_similarity"] == 0
    assert counts["profile_rebuilt"] == 0
