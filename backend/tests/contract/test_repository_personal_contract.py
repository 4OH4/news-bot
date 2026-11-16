"""Contract tests for the Phase 4 personalisation fields (#320).

Every ``Repository`` must keep an article's embedding, the feedback fields the
A/B check reads (``user_id``, ``view``, ``arm``) and the reader's ``Profile``.
Vectors may be stored as float32, so they are compared within 1e-6.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from newsfeed.models import (
    DEFAULT_USER_ID,
    Article,
    Feedback,
    FeedbackEvent,
    InterestCentroid,
    Profile,
    RankingArm,
)
from newsfeed.ports import Repository
from tests.contract.conftest import RepositoryOpener

T0 = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
MODEL = "BAAI/bge-small-en-v1.5"


def vector(length: int, seed: float = 0.0) -> list[float]:
    """Deterministic values in [-1, 1] that are not exact in float32."""
    return [math.sin(seed + 0.37 * i) for i in range(length)]


def assert_vector_close(actual: list[float] | None, expected: list[float]) -> None:
    assert actual is not None
    assert actual == pytest.approx(expected, abs=1e-6)


def embedded_article(n: int = 1) -> Article:
    url = f"https://example.com/embedded-{n}"
    return Article(
        canonical_url=url,
        url=url,
        source_id="example",
        title=f"Invented headline {n}",
        fetched_at=T0,
        embedding=vector(384, seed=n),
        embedding_model=MODEL,
    )


def full_profile(user_id: str = DEFAULT_USER_ID) -> Profile:
    return Profile(
        user_id=user_id,
        embedding_model=MODEL,
        centroids=[
            InterestCentroid(vector=vector(384, 1.0), weight=2.5, size=12),
            InterestCentroid(vector=vector(384, 2.0), weight=0.5, size=3),
        ],
        negative=vector(384, 3.0),
        built_at=T0,
        events_used=15,
        last_feedback_at=T0 - timedelta(minutes=5, microseconds=7),
    )


def assert_profile_close(actual: Profile | None, expected: Profile) -> None:
    assert actual is not None
    vectors = {"centroids": {"__all__": {"vector"}}, "negative": True}
    assert actual.model_dump(exclude=vectors) == expected.model_dump(exclude=vectors)
    assert len(actual.centroids) == len(expected.centroids)
    for got, want in zip(actual.centroids, expected.centroids, strict=True):
        assert_vector_close(got.vector, want.vector)
    if expected.negative is None:
        assert actual.negative is None
    else:
        assert_vector_close(actual.negative, expected.negative)


# --- Articles ----------------------------------------------------------------


def test_article_embedding_round_trips_within_1e6(repo: Repository) -> None:
    article = embedded_article()
    repo.upsert_article(article)
    for stored in (repo.get_article(article.id), repo.list_articles()[0]):
        assert stored is not None
        assert stored.embedding_model == MODEL
        assert_vector_close(stored.embedding, article.embedding or [])
        assert stored.model_dump(exclude={"embedding"}) == article.model_dump(exclude={"embedding"})


def test_upsert_without_embedding_clears_a_stored_one(repo: Repository) -> None:
    article = embedded_article()
    repo.upsert_article(article)
    cleared = Article.model_validate(
        {**article.model_dump(), "embedding": None, "embedding_model": None}
    )
    repo.upsert_article(cleared)
    assert repo.get_article(article.id) == cleared


# --- Feedback ----------------------------------------------------------------


def test_feedback_ab_fields_round_trip(repo: Repository) -> None:
    feedback = Feedback(
        id="f1",
        article_id="a" * 16,
        event=FeedbackEvent.OPEN,
        created_at=T0,
        user_id="owner",
        view="feed",
        arm=RankingArm.PERSONAL,
    )
    plain = Feedback(id="f2", article_id="b" * 16, event=FeedbackEvent.LIKE, created_at=T0)
    repo.save_feedback(feedback)
    repo.save_feedback(plain)
    assert repo.list_feedback() == [feedback, plain]
    stored_plain = repo.list_feedback()[1]
    assert (stored_plain.user_id, stored_plain.view, stored_plain.arm) == ("owner", None, None)


# --- Profiles ----------------------------------------------------------------


def test_get_profile_returns_none_until_saved_then_an_equal_profile(repo: Repository) -> None:
    assert repo.get_profile("owner") is None
    profile = full_profile()
    repo.save_profile(profile)
    assert_profile_close(repo.get_profile("owner"), profile)
    assert repo.get_profile("someone-else") is None


def test_save_profile_replaces_by_user_id(repo: Repository) -> None:
    repo.save_profile(full_profile())
    other = full_profile("guest")
    repo.save_profile(other)
    empty = Profile()
    repo.save_profile(empty)
    assert repo.get_profile("owner") == empty
    assert_profile_close(repo.get_profile("guest"), other)


def test_returned_profile_is_a_copy(repo: Repository) -> None:
    profile = full_profile()
    repo.save_profile(profile)
    fetched = repo.get_profile("owner")
    assert fetched is not None
    fetched.centroids[0].vector[0] = 99.0
    fetched.events_used = 0
    assert_profile_close(repo.get_profile("owner"), profile)


def test_personal_data_persists_across_reopening(open_repository: RepositoryOpener) -> None:
    first = open_repository()
    article = embedded_article()
    profile = full_profile()
    first.upsert_article(article)
    first.save_profile(profile)

    second = open_repository()
    stored = second.get_article(article.id)
    assert stored is not None
    assert_vector_close(stored.embedding, article.embedding or [])
    assert_profile_close(second.get_profile("owner"), profile)
