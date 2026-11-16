"""Tests for ``story_weights`` and ``build_profile`` (#323)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from newsfeed.models import FeedbackEvent, Profile
from newsfeed.rank.profile import build_profile, story_weights
from tests.rank.support import NOW, SMALL_PROFILE, config, cosine, feedback, nearest, unit

READ, OPEN, LIKE, DISMISS = (
    FeedbackEvent.READ,
    FeedbackEvent.OPEN,
    FeedbackEvent.LIKE,
    FeedbackEvent.DISMISS,
)


def test_reads_and_opens_weigh_by_kind_and_seconds(tmp_path: Path) -> None:
    events = [
        feedback("A", READ, 120),
        feedback("B", READ, 120),
        feedback("C", OPEN, None),
        feedback("D", READ, 60),
        feedback("E", READ, 600),  # capped at a full read
    ]

    weights = story_weights(events, config(tmp_path), NOW)

    assert weights == pytest.approx({"A": 1.0, "B": 1.0, "C": 0.3, "D": 0.5, "E": 1.0})


def test_an_old_event_halves_every_half_life(tmp_path: Path) -> None:
    events = [feedback("A", READ, 120, ago=timedelta(days=45))]

    assert story_weights(events, config(tmp_path), NOW)["A"] == pytest.approx(0.5)


def test_a_like_then_an_unlike_adds_nothing(tmp_path: Path) -> None:
    events = [
        feedback("A", LIKE, 1, at=NOW.replace(hour=9, minute=0)),
        feedback("A", LIKE, 0, at=NOW.replace(hour=9, minute=5)),
        feedback("B", LIKE, 1, at=NOW.replace(hour=9, minute=0)),
    ]

    weights = story_weights(events, config(tmp_path), NOW)

    assert weights.get("A", 0.0) == pytest.approx(0.0, abs=1e-6)
    assert weights["B"] > 1.9


def test_a_dismiss_is_negative_and_an_undone_one_is_not(tmp_path: Path) -> None:
    events = [
        feedback("D", DISMISS, 1, ago=timedelta(hours=3)),
        feedback("U", DISMISS, 1, ago=timedelta(hours=3)),
        feedback("U", DISMISS, 0, ago=timedelta(hours=2)),
    ]

    weights = story_weights(events, config(tmp_path), NOW)

    assert weights["D"] < -0.9
    assert weights.get("U", 0.0) == pytest.approx(0.0, abs=1e-6)


def test_mute_source_is_ignored(tmp_path: Path) -> None:
    weights = story_weights([feedback("A", FeedbackEvent.MUTE_SOURCE, 1)], config(tmp_path), NOW)

    assert weights.get("A", 0.0) == 0.0


def group_vectors(n: int, axis: int) -> dict[str, list[float]]:
    """``n`` slightly different unit vectors around one axis of 4 dimensions."""
    out: dict[str, list[float]] = {}
    for i in range(n):
        raw = [0.0, 0.0, 0.0, 0.0]
        raw[axis] = 1.0
        raw[(axis + 1) % 4] = 0.02 * i
        out[f"g{axis}-{i}"] = unit(*raw)
    return out


def build(
    weights: dict[str, float], vectors: dict[str, list[float]], tmp_path: Path, text: str = ""
) -> Profile:
    return build_profile(
        weights,
        vectors,
        config=config(tmp_path, text),
        now=NOW,
        model="m1",
        last_feedback_at=NOW - timedelta(hours=1),
    )


def test_fewer_than_min_events_gives_no_centroids_but_still_names_the_model(
    tmp_path: Path,
) -> None:
    vectors = group_vectors(9, 0)

    profile = build({k: 1.0 for k in vectors}, vectors, tmp_path)  # min_events defaults to 10

    assert profile.centroids == []
    assert profile.embedding_model == "m1"


def test_two_separated_groups_give_ceil_n_over_15_centroids(tmp_path: Path) -> None:
    vectors = group_vectors(20, 0) | group_vectors(20, 2)

    profile = build({k: 1.0 for k in vectors}, vectors, tmp_path)

    assert len(profile.centroids) == 3  # ceil(40 / 15)
    assert profile.embedding_model == "m1"
    assert profile.last_feedback_at == NOW - timedelta(hours=1)
    centres = {i: c.vector for i, c in enumerate(profile.centroids)}
    near = {
        axis: {nearest(v, centres) for k, v in vectors.items() if k.startswith(f"g{axis}-")}
        for axis in (0, 2)
    }
    assert near[0].isdisjoint(near[2])
    for axis, owned in near.items():
        pole = unit(*[1.0 if j == axis else 0.0 for j in range(4)])
        for i in owned:  # a centroid is built mostly from its own group
            assert cosine(centres[i], pole) > 0.9


def test_the_same_inputs_give_equal_profiles(tmp_path: Path) -> None:
    vectors = group_vectors(20, 0) | group_vectors(20, 2)
    weights = {k: 1.0 for k in vectors}

    assert build(weights, vectors, tmp_path) == build(weights, vectors, tmp_path)


def test_a_dismissed_story_forms_the_negative_vector_not_a_centroid(tmp_path: Path) -> None:
    vectors = group_vectors(12, 0) | {"D": unit(0, 0, 0, 1)}
    weights = {k: 1.0 for k in vectors if k != "D"} | {"D": -1.0}

    profile = build(weights, vectors, tmp_path, SMALL_PROFILE)

    assert profile.negative == pytest.approx(vectors["D"])
    assert profile.centroids
    assert all(cosine(c.vector, vectors["D"]) < 0.1 for c in profile.centroids)


def test_articles_without_a_vector_are_skipped(tmp_path: Path) -> None:
    vectors = group_vectors(12, 0)
    weights = {k: 1.0 for k in vectors} | {"no-vector": 1.0}

    assert build(weights, vectors, tmp_path).centroids
