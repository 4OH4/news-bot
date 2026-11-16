"""How close each article is to the interest profile, and the factor that follows (#323).

Pure functions. ``similarity_scores`` gives the raw closeness of each article's
embedding to the profile; ``similarity_factors`` turns the raws of one run into
factors of 1 or more by rank, so the factor doesn't depend on the embedding
model's scale and never demotes an article the profile says nothing about.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np

from newsfeed.models import Profile

__all__ = ["similarity_factors", "similarity_scores"]


def similarity_scores(
    vectors: Mapping[str, Sequence[float]], profile: Profile, negative_weight: float
) -> dict[str, float]:
    """Per article id, ``max cos(v, centroid) - negative_weight * cos(v, negative)``.

    Vectors and centroids are unit length, so a dot product is the cosine. The
    negative term applies only when the profile has a ``negative``. With no
    centroids there is nothing to compare against, so the result is empty.
    """
    if not vectors or not profile.centroids:
        return {}
    ids = list(vectors)
    matrix = np.asarray([vectors[i] for i in ids], dtype=np.float64)
    centres = np.asarray([c.vector for c in profile.centroids], dtype=np.float64)
    raw = (matrix @ centres.T).max(axis=1)
    if profile.negative is not None:
        raw = raw - negative_weight * (matrix @ np.asarray(profile.negative, dtype=np.float64))
    return {i: float(r) for i, r in zip(ids, raw, strict=True)}


def similarity_factors(raw: Mapping[str, float], beta: float) -> dict[str, float]:
    """Per article id, ``1 + beta * max(0, 2r - 1)``, where ``r`` is its percentile rank.

    ``r = (rank - 1) / (n - 1)`` among this run's raws, 0 for the lowest and 1
    for the highest; ties share their mean rank, and a single article has
    ``r = 0.5``. Articles at or below the median get 1.0.
    """
    n = len(raw)
    if n == 0:
        return {}
    if n == 1:
        return {next(iter(raw)): 1.0}
    ordered = sorted(raw, key=lambda k: raw[k])
    percentile: dict[str, float] = {}
    start = 0
    while start < n:
        end = start
        while end + 1 < n and raw[ordered[end + 1]] == raw[ordered[start]]:
            end += 1
        mean_rank = (start + end) / 2 + 1  # 1-based ranks start+1 .. end+1
        for key in ordered[start : end + 1]:
            percentile[key] = (mean_rank - 1) / (n - 1)
        start = end + 1
    return {k: 1.0 + beta * max(0.0, 2 * percentile[k] - 1) for k in raw}
