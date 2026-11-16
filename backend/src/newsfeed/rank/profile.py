"""The owner's interest profile, built from feedback and article embeddings (#323).

Pure functions. ``story_weights`` nets the feedback events on each article into
one weight, decayed by age; ``build_profile`` turns the positive weights into a
few centroids by weighted spherical k-means and the negative ones into a single
``negative`` vector (ADR-0014). The result is the owner's personal history, so
nothing here logs.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime

import numpy as np
from numpy.typing import NDArray

from newsfeed.models import DEFAULT_USER_ID, Feedback, FeedbackEvent, InterestCentroid, Profile
from newsfeed.rank.personalisation import PersonalisationConfig

__all__ = ["KMEANS_ITERATIONS", "KMEANS_SEED", "build_profile", "story_weights"]

KMEANS_SEED = 0
"""Seed for the k-means++ start, so the same inputs give the same profile."""

KMEANS_ITERATIONS = 20
"""At most this many assignment rounds."""

_UNDOABLE = frozenset({FeedbackEvent.LIKE, FeedbackEvent.DISMISS})
_SECONDS_PER_DAY = 86400.0

Matrix = NDArray[np.float64]


def _standing(events: Sequence[Feedback]) -> list[Feedback]:
    """Events still standing: a like or dismiss of value 0 cancels the latest earlier one.

    The same rule as ``newsfeed report``; the undo itself is dropped too.
    """
    stacks: dict[tuple[str, FeedbackEvent], list[Feedback]] = defaultdict(list)
    for event in sorted(events, key=lambda f: (f.created_at, f.id)):
        stack = stacks[(event.article_id, event.event)]
        if event.event in _UNDOABLE and event.value == 0:
            if stack:
                stack.pop()
        else:
            stack.append(event)
    return [f for stack in stacks.values() for f in stack]


def _event_weight(event: Feedback, config: PersonalisationConfig) -> float:
    signals = config.signals
    match event.event:
        case FeedbackEvent.READ:
            seconds = max(event.value or 0.0, 0.0)
            return signals.read * min(seconds / signals.read_full_seconds, 1.0)
        case FeedbackEvent.OPEN:
            return signals.open
        case FeedbackEvent.LIKE:
            return signals.like
        case FeedbackEvent.DISMISS:
            return signals.dismiss
        case _:
            # mute_source becomes a source setting in the preferences epic.
            return 0.0


def story_weights(
    events: Sequence[Feedback], config: PersonalisationConfig, now: datetime
) -> dict[str, float]:
    """The net decayed weight per ``article_id``.

    Each standing event's weight is multiplied by ``0.5 ** (age_days /
    half_life_days)``, with ``age_days`` in whole days, so every event from the
    last 24 hours counts in full; one dated after ``now`` counts as new. A read
    without seconds counts as none. Articles whose events all net to nothing are
    left out.
    """
    half_life = config.profile.half_life_days
    weights: dict[str, float] = defaultdict(float)
    for event in _standing(events):
        weight = _event_weight(event, config)
        if weight == 0.0:
            continue
        age_days = math.floor(max((now - event.created_at).total_seconds(), 0.0) / _SECONDS_PER_DAY)
        weights[event.article_id] += weight * 0.5 ** (age_days / half_life)
    return {k: w for k, w in weights.items() if w != 0.0}


def _normalise_rows(matrix: Matrix) -> Matrix:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return np.divide(matrix, norms, out=np.zeros_like(matrix), where=norms > 0)


def _seed(points: Matrix, weights: Matrix, k: int) -> Matrix:
    """k-means++ on the sphere: each next centre is drawn by weight times squared distance."""
    rng = np.random.default_rng(KMEANS_SEED)
    chosen = [int(rng.choice(len(points), p=weights / weights.sum()))]
    while len(chosen) < k:
        distance = 1.0 - (points @ points[chosen].T).max(axis=1)
        odds = weights * np.clip(distance, 0.0, None) ** 2
        if odds.sum() <= 0:  # every point sits on a chosen centre
            break
        chosen.append(int(rng.choice(len(points), p=odds / odds.sum())))
    return points[chosen].copy()


def _spherical_kmeans(points: Matrix, weights: Matrix, k: int) -> tuple[Matrix, NDArray[np.intp]]:
    centres = _seed(points, weights, k)
    labels = np.full(len(points), -1, dtype=np.intp)
    for _ in range(KMEANS_ITERATIONS):
        new_labels = (points @ centres.T).argmax(axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for j in range(len(centres)):
            members = labels == j
            if not members.any():
                continue  # keep the old centre rather than lose it
            total = (weights[members, None] * points[members]).sum(axis=0)
            norm = np.linalg.norm(total)
            if norm > 0:
                centres[j] = total / norm
    return centres, labels


def _centroids(
    ids: list[str], weights: Mapping[str, float], vectors: Mapping[str, Sequence[float]], k: int
) -> list[InterestCentroid]:
    points = _normalise_rows(np.asarray([vectors[i] for i in ids], dtype=np.float64))
    w = np.asarray([weights[i] for i in ids], dtype=np.float64)
    centres, labels = _spherical_kmeans(points, w, k)
    out = [
        InterestCentroid(
            vector=[float(v) for v in centres[j]],
            weight=float(w[labels == j].sum()),
            size=int((labels == j).sum()),
        )
        for j in range(len(centres))
        if (labels == j).any()
    ]
    # Heaviest first, so the order doesn't depend on which point seeded which centre.
    return sorted(out, key=lambda c: (-c.weight, c.vector))


def build_profile(
    weights: Mapping[str, float],
    vectors: Mapping[str, Sequence[float]],
    *,
    config: PersonalisationConfig,
    now: datetime,
    model: str,
    user_id: str = DEFAULT_USER_ID,
    last_feedback_at: datetime | None,
) -> Profile:
    """The profile for ``weights`` (from ``story_weights``) and the stories' ``vectors``.

    Positive weights form the centroids, ``k = min(max_centroids, ceil(n /
    events_per_centroid))``, by weighted spherical k-means with a seeded
    k-means++ start, so the same inputs give an equal profile. Fewer than
    ``min_events`` positive stories give no centroids. Negative weights form
    ``negative``: their weighted mean, normalised. Stories missing from
    ``vectors`` are skipped (the caller counts them). ``embedding_model`` is
    always ``model``, and ``events_used`` counts the stories used.
    """
    usable = sorted(i for i, w in weights.items() if i in vectors and w != 0.0)
    positive = [i for i in usable if weights[i] > 0]
    negative_ids = [i for i in usable if weights[i] < 0]
    profile = config.profile
    centroids: list[InterestCentroid] = []
    if len(positive) >= profile.min_events:
        k = min(profile.max_centroids, math.ceil(len(positive) / profile.events_per_centroid))
        centroids = _centroids(positive, weights, vectors, k)
    negative: list[float] | None = None
    if negative_ids:
        points = _normalise_rows(np.asarray([vectors[i] for i in negative_ids], dtype=np.float64))
        w = np.asarray([-weights[i] for i in negative_ids], dtype=np.float64)
        mean = (w[:, None] * points).sum(axis=0)
        norm = np.linalg.norm(mean)
        if norm > 0:
            negative = [float(v) for v in mean / norm]
    return Profile(
        user_id=user_id,
        embedding_model=model,
        centroids=centroids,
        negative=negative,
        built_at=now,
        events_used=len(positive) + (len(negative_ids) if negative is not None else 0),
        last_feedback_at=last_feedback_at,
    )
