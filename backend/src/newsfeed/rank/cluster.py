"""Near-duplicate story clustering and the coverage factor, as pure functions (#324).

Several outlets covering one story give embeddings that sit very close
together. ``cluster_articles`` groups them greedily, and ``coverage_factor``
turns the number of distinct sources in a group into a small score lift: the
more outlets cover a story, the more likely it matters (ADR-0014).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np

__all__ = ["ClusterInput", "cluster_articles", "coverage_factor"]


@dataclass(frozen=True)
class ClusterInput:
    """One article to cluster: its id, when it was fetched, its source and its embedding."""

    article_id: str
    fetched_at: datetime
    source_id: str
    vector: Sequence[float]


def cluster_articles(
    members: Sequence[ClusterInput], *, threshold: float, max_gap_hours: float
) -> dict[str, str]:
    """Map every article id to its cluster id.

    Greedy and deterministic: articles are taken by ``fetched_at`` then
    ``article_id``. Each joins the cluster of the most similar earlier article
    (cosine similarity at least ``threshold``) fetched within ``max_gap_hours``
    of it, else starts its own. The cluster id is the first member's article id,
    so a lone article is its own cluster.
    """
    ordered = sorted(members, key=lambda m: (m.fetched_at, m.article_id))
    if not ordered:
        return {}
    matrix = np.asarray([m.vector for m in ordered], dtype=np.float64)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.where(norms == 0, 1.0, norms)
    similarity = matrix @ matrix.T
    seconds = np.asarray([m.fetched_at.timestamp() for m in ordered])
    max_gap = max_gap_hours * 3600.0
    result: list[str] = []
    for i, item in enumerate(ordered):
        best = -math.inf
        best_cluster = item.article_id
        for j in range(i):
            if seconds[i] - seconds[j] > max_gap:
                continue
            score = float(similarity[i, j])
            # Earlier rows win ties, since "greater than" never replaces them.
            if score >= threshold and score > best:
                best, best_cluster = score, result[j]
        result.append(best_cluster)
    return {m.article_id: cluster for m, cluster in zip(ordered, result, strict=True)}


def coverage_factor(source_count: int, gamma: float) -> float:
    """``1 + gamma * ln(source_count)`` for two or more sources, else 1.0."""
    if source_count < 2:
        return 1.0
    return 1.0 + gamma * math.log(source_count)
