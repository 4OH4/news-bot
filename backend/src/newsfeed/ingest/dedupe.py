"""Exact deduplication of ingested articles by id, i.e. by canonical URL (FR-4).

Near-duplicate story clustering across outlets is a separate, later step
(Phase 4). This module only removes articles that share an id.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from datetime import UTC, datetime

from newsfeed.models import Article

__all__ = ["dedupe"]


def dedupe(candidates: Iterable[Article], existing_ids: Collection[str]) -> list[Article]:
    """Return the candidates that are new, with at most one article per id.

    Candidates whose id is in ``existing_ids`` (e.g. already in the
    ``Repository``) are dropped. Within the batch, the earliest-published
    occurrence of each id is kept; an article without ``published_at`` loses to
    one with a date, and ties keep the first occurrence, so the result is
    deterministic. The output follows the order in which each id first appears.

    Candidates must already carry their canonical URL (``ingest.canonical``),
    because ``Article.id`` is derived from it.
    """
    kept: dict[str, Article] = {}
    for article in candidates:
        if article.id in existing_ids:
            continue
        current = kept.get(article.id)
        if current is None or _sort_key(article) < _sort_key(current):
            kept[article.id] = article
    return list(kept.values())


_UNDATED = datetime.min.replace(tzinfo=UTC)


def _sort_key(article: Article) -> tuple[bool, datetime]:
    """Order by publication time, with undated articles after dated ones."""
    if article.published_at is None:
        return (True, _UNDATED)
    return (False, article.published_at)
