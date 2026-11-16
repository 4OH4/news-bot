"""Near-duplicate clustering and the coverage factor, as pure functions (#324)."""

from __future__ import annotations

import math
import random
from datetime import timedelta

import pytest

from newsfeed.rank.cluster import ClusterInput, cluster_articles, coverage_factor
from tests.rank.support import NOW, cosine

THRESHOLD = 0.88
GAP = 48.0


def at_angle(degrees: float) -> list[float]:
    """A unit vector in the plane, so cosine similarity between two is cos of the angle gap."""
    radians = math.radians(degrees)
    return [math.cos(radians), math.sin(radians)]


def angle_for(similarity: float) -> float:
    return math.degrees(math.acos(similarity))


def member(name: str, hours: float, vector: list[float], source: str | None = None) -> ClusterInput:
    return ClusterInput(
        article_id=name,
        fetched_at=NOW + timedelta(hours=hours),
        source_id=source or f"source-{name}",
        vector=vector,
    )


def cluster(*members: ClusterInput) -> dict[str, str]:
    return cluster_articles(members, threshold=THRESHOLD, max_gap_hours=GAP)


def test_similar_articles_close_in_time_share_the_earlier_ones_id() -> None:
    result = cluster(member("a", 0, at_angle(0)), member("b", 3, at_angle(angle_for(0.93))))

    assert result == {"a": "a", "b": "a"}


def test_similar_articles_too_far_apart_in_time_stay_separate() -> None:
    result = cluster(member("a", 0, at_angle(0)), member("b", 60, at_angle(angle_for(0.93))))

    assert result == {"a": "a", "b": "b"}


def test_articles_below_the_threshold_stay_separate() -> None:
    result = cluster(member("a", 0, at_angle(0)), member("b", 1, at_angle(angle_for(0.80))))

    assert result == {"a": "a", "b": "b"}


def test_an_article_joins_the_cluster_it_is_most_similar_to() -> None:
    # C is 0.89 similar to A and 0.90 to B, and A and B are far apart.
    c_angle = angle_for(0.89)
    b_angle = c_angle + angle_for(0.90)
    a, b = at_angle(0), at_angle(b_angle)
    c = at_angle(c_angle)
    assert cosine(a, b) < THRESHOLD
    assert cosine(c, a) == pytest.approx(0.89)
    assert cosine(c, b) == pytest.approx(0.90)

    result = cluster(member("a", 0, a), member("b", 1, b), member("c", 2, c))

    assert result == {"a": "a", "b": "b", "c": "b"}


def test_the_result_does_not_depend_on_the_order_of_the_inputs() -> None:
    members = [
        member("a", 0, at_angle(0)),
        member("b", 1, at_angle(5)),
        member("c", 1, at_angle(70)),  # same time as b: the id breaks the tie
        member("d", 4, at_angle(66)),
        member("e", 100, at_angle(2)),
    ]
    expected = cluster(*members)
    assert expected == {"a": "a", "b": "a", "c": "c", "d": "c", "e": "e"}

    shuffler = random.Random(7)
    for _ in range(5):
        shuffled = members[:]
        shuffler.shuffle(shuffled)
        assert cluster(*shuffled) == expected


def test_a_lone_article_is_its_own_cluster_and_nothing_gives_nothing() -> None:
    assert cluster(member("a", 0, at_angle(0))) == {"a": "a"}
    assert cluster() == {}


@pytest.mark.parametrize(
    ("sources", "gamma", "expected"),
    [
        (1, 0.25, 1.0),
        (2, 0.25, 1.17),
        (4, 0.25, 1.35),
        (8, 0.25, 1.52),
        (3, 0.25, 1 + 0.25 * math.log(3)),
        (4, 0.0, 1.0),
    ],
)
def test_coverage_factor_grows_with_the_log_of_the_source_count(
    sources: int, gamma: float, expected: float
) -> None:
    assert coverage_factor(sources, gamma) == pytest.approx(expected, abs=0.005)
