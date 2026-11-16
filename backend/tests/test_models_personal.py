"""Tests for the Phase 4 personalisation contract in ``newsfeed.models`` (#320).

Covers the embedding on ``Article``, the A/B fields on ``Feedback`` and the
``Profile`` of interest centroids keyed by user id.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from newsfeed.models import (
    DEFAULT_USER_ID,
    Article,
    Feedback,
    InterestCentroid,
    Profile,
    RankingArm,
)

NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
MODEL = "BAAI/bge-small-en-v1.5"


def make_article(**overrides: Any) -> Article:
    url = "https://example.com/invented-story"
    return Article.model_validate(
        {
            "canonical_url": url,
            "url": url,
            "source_id": "example",
            "title": "An invented headline",
            "fetched_at": NOW,
            **overrides,
        }
    )


def test_article_accepts_an_embedding_with_its_model() -> None:
    article = make_article(embedding=[0.6, 0.8], embedding_model=MODEL)
    assert article.embedding == [0.6, 0.8]
    assert article.embedding_model == MODEL
    plain = make_article()
    assert (plain.embedding, plain.embedding_model) == (None, None)


@pytest.mark.parametrize(
    "fields",
    [
        {"embedding": [0.6, 0.8]},
        {"embedding": [0.6, math.nan], "embedding_model": MODEL},
        {"embedding": [math.inf, 0.0], "embedding_model": MODEL},
        {"embedding": [], "embedding_model": MODEL},
    ],
    ids=["no-model", "nan", "infinity", "empty"],
)
def test_article_rejects_a_bad_embedding(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        make_article(**fields)


def test_feedback_stored_before_ab_fields_loads_with_defaults() -> None:
    legacy = {"id": "f1", "article_id": "a" * 16, "event": "open", "created_at": NOW.isoformat()}
    feedback = Feedback.model_validate(legacy)
    assert DEFAULT_USER_ID == "owner"
    assert (feedback.user_id, feedback.view, feedback.arm) == ("owner", None, None)


def test_feedback_accepts_ab_fields() -> None:
    feedback = Feedback.model_validate(
        {"article_id": "a" * 16, "event": "open", "created_at": NOW, "view": "feed", "arm": "rules"}
    )
    assert feedback.arm is RankingArm.RULES
    assert RankingArm.PERSONAL == "personal"


@pytest.mark.parametrize(
    "fields",
    [
        {"user_id": "someone@example.com"},
        {"user_id": ""},
        {"user_id": "Owner"},
        {"view": "Feed View"},
        {"view": "x" * 41},
        {"arm": "random"},
    ],
)
def test_feedback_rejects_bad_ab_fields(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Feedback.model_validate(
            {"article_id": "a" * 16, "event": "open", "created_at": NOW, **fields}
        )


def test_profile_defaults_to_an_empty_profile_for_the_owner() -> None:
    profile = Profile()
    assert profile.user_id == DEFAULT_USER_ID
    assert profile.centroids == []
    assert profile.negative is None
    assert profile.events_used == 0


def test_profile_round_trips_through_json() -> None:
    profile = Profile(
        embedding_model=MODEL,
        centroids=[InterestCentroid(vector=[0.6, 0.8], weight=1.5, size=4)],
        negative=[1.0, 0.0],
        built_at=NOW,
        events_used=4,
        last_feedback_at=NOW,
    )
    assert Profile.model_validate_json(profile.model_dump_json()) == profile


@pytest.mark.parametrize(
    ("centroid_lengths", "negative_length"),
    [((384, 384), 383), ((384, 383), None)],
    ids=["negative-differs", "centroids-differ"],
)
def test_profile_rejects_vectors_of_different_lengths(
    centroid_lengths: tuple[int, ...], negative_length: int | None
) -> None:
    centroids = [{"vector": [0.1] * n, "weight": 1.0, "size": 1} for n in centroid_lengths]
    negative = None if negative_length is None else [0.1] * negative_length
    with pytest.raises(ValidationError):
        Profile.model_validate({"centroids": centroids, "negative": negative})


@pytest.mark.parametrize(
    "fields",
    [
        {"vector": [], "weight": 1.0, "size": 1},
        {"vector": [math.nan], "weight": 1.0, "size": 1},
        {"vector": [0.5], "weight": -1.0, "size": 1},
        {"vector": [0.5], "weight": 1.0, "size": -1},
    ],
    ids=["empty", "nan", "negative-weight", "negative-size"],
)
def test_interest_centroid_rejects_bad_values(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        InterestCentroid.model_validate(fields)
