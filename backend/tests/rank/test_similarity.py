"""Tests for ``similarity_scores`` and ``similarity_factors`` (#323)."""

from __future__ import annotations

import pytest

from newsfeed.models import InterestCentroid, Profile
from newsfeed.rank.similarity import similarity_factors, similarity_scores
from tests.rank.support import unit


def profile(negative: list[float] | None = None) -> Profile:
    return Profile(
        embedding_model="m1",
        centroids=[
            InterestCentroid(vector=[1.0, 0.0], weight=1.0, size=1),
            InterestCentroid(vector=[0.0, 1.0], weight=1.0, size=1),
        ],
        negative=negative,
    )


def test_raw_is_the_best_centroid_less_the_negative_part() -> None:
    vectors = {"a": [1.0, 0.0], "b": unit(0.6, 0.8)}

    plain = similarity_scores(vectors, profile(), 0.5)
    pushed = similarity_scores(vectors, profile(negative=[0.0, 1.0]), 0.5)

    assert plain == pytest.approx({"a": 1.0, "b": 0.8})
    assert pushed == pytest.approx({"a": 1.0, "b": 0.8 - 0.5 * 0.8})


def test_five_raws_give_the_factors_from_the_issue() -> None:
    raw = {"a": 0.1, "b": 0.2, "c": 0.3, "d": 0.4, "e": 0.5}

    factors = similarity_factors(raw, 2.0)

    assert factors == pytest.approx({"a": 1.0, "b": 1.0, "c": 1.0, "d": 2.0, "e": 3.0})


def test_ties_share_their_mean_rank_and_one_article_is_not_lifted() -> None:
    tied = similarity_factors({"a": 0.1, "b": 0.9, "c": 0.9, "d": 0.9, "e": 0.5}, 2.0)

    assert tied["b"] == tied["c"] == tied["d"]
    assert tied["b"] == pytest.approx(1 + 2.0 * (2 * 0.75 - 1))  # mean rank 4 of 5: r = 0.75
    assert similarity_factors({"a": 0.9}, 2.0) == {"a": 1.0}  # r = 0.5
    assert similarity_factors({}, 2.0) == {}
