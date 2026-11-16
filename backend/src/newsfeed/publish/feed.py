"""The publish stage: build ``feed.json`` from ranked articles and write it.

The format, selection, order and display rule are fixed by ADR-0006 and the
JSON Schema in ``docs/feed.schema.json``. ``build_feed`` is pure; ``publish_feed``
does the I/O through the ``Repository`` and ``BlobStore`` ports.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from newsfeed.logging import get_logger
from newsfeed.models import Article, ExtractionStatus, PipelineStage, Source
from newsfeed.wiring import Services

__all__ = [
    "FEED_KEY",
    "FEED_LIMIT",
    "FEED_WINDOW_HOURS",
    "PARTIAL_MIN_WORDS",
    "SCHEMA_VERSION",
    "BuiltFeed",
    "build_feed",
    "effective_time",
    "one_per_cluster",
    "publish_feed",
]

log = get_logger(__name__)

SCHEMA_VERSION = 1
FEED_KEY = "feed.json"
FEED_LIMIT = 200
FEED_WINDOW_HOURS = 72.0
"""Effective-time window; it must not exceed the rank stage's rescoring window."""
PARTIAL_MIN_WORDS = 150
"""A ``partial`` item is shown as an article from this many words (ADR-0006)."""

_STAGE = PipelineStage.PUBLISH.value
# fullmatch, not match with "$": "$" also accepts a trailing newline, which the JSON Schema rejects.
_HTTP_URL = re.compile(r"https?://\S+")
_SOURCE_ID = re.compile(r"[a-z0-9][a-z0-9_-]*")
_RUN_ID = re.compile(r"[A-Za-z0-9._-]+")


@dataclass(frozen=True)
class BuiltFeed:
    """The feed document plus counts for the run statistics."""

    feed: dict[str, Any]
    candidates: int
    dropped: int
    duplicates: int = 0


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def effective_time(article: Article) -> datetime:
    """``published_at`` (or ``fetched_at``), never later than ``fetched_at``.

    The cap stops a future-dated article from staying in the window for ever.
    """
    return min(article.published_at or article.fetched_at, article.fetched_at)


def _domain(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host.removeprefix("www.")


def _is_article(article: Article) -> bool:
    """Whether the item shows as an article: its extraction qualifies (ADR-0006) and it
    has stored content. The blob itself isn't checked (ADR-0013): an expired copy is the
    reader's expired state, not a link."""
    if article.content_ref is None:
        return False
    status = article.extraction.status
    if status is ExtractionStatus.OK:
        return True
    return status is ExtractionStatus.PARTIAL and article.extraction.word_count >= PARTIAL_MIN_WORDS


def _item(
    article: Article, story_id: str, *, as_article: bool, coverage: int = 1
) -> dict[str, Any]:
    image = article.lead_image
    item: dict[str, Any] = {
        "id": article.id,
        "story_id": story_id,
        "title": article.title,
        "url": article.canonical_url,
        "source_id": article.source_id,
        "domain": _domain(article.canonical_url),
        "author": article.author,
        "published_at": _timestamp(article.published_at) if article.published_at else None,
        "summary": article.summary,
        "lead_image": image if image and _HTTP_URL.fullmatch(image) else None,
        "score": article.score,
        "score_breakdown": dict(article.score_breakdown),
        "extraction_status": article.extraction.status.value,
        "display": "article" if as_article else "link",
        "word_count": article.extraction.word_count if as_article else None,
        "content_path": f"articles/{article.id}.html" if as_article else None,
    }
    if coverage >= 2:
        item["coverage"] = coverage
    return item


def _problem(article: Article) -> str | None:
    """Why this article can't be a valid feed item, or None. Mirrors the schema."""
    if not article.title.strip():
        return "empty title"
    if not _HTTP_URL.fullmatch(article.canonical_url) or not _domain(article.canonical_url):
        return "invalid url"
    if not _SOURCE_ID.fullmatch(article.source_id):
        return "invalid source id"
    if article.score is None or not math.isfinite(article.score):
        return "invalid score"
    if not all(math.isfinite(v) for v in article.score_breakdown.values()):
        return "invalid score breakdown"
    return None


def _story_end(article: Article, by_address: Mapping[str, Article]) -> str:
    """The id of the row that ``article``'s declared canonicals finally lead to.

    Rows with the same end are one story. A loop has no end, so every row
    leading into it agrees on the smallest id in the loop.
    """
    path = [article.id]
    current = article
    while (declared := current.extraction.declared_canonical) and declared in by_address:
        current = by_address[declared]
        if current.id in path:
            return min(path[path.index(current.id) :])
        path.append(current.id)
    return path[-1]


def _one_per_story(
    ranked: Sequence[Article], articles: Sequence[Article]
) -> list[tuple[Article, str]]:
    """``ranked`` with one row per story, in the same order (#147).

    Each row comes with its story's end id, which is the same whichever row is shown.

    The row kept is the one with readable text; among equals, the canonical
    target, then the highest ranked. Links are followed through all ``articles``,
    so a story stays whole when a row in the middle isn't a candidate, but only
    ``ranked`` rows can be shown: a target the feed can't show never hides its copy.

    A declared address also matches a row by its first-seen ``url``, because a
    target may have been re-keyed since, after its own page declared another
    canonical. That is an exact match, not a normalised one: a miss shows the
    story twice, which loses nothing.
    """
    by_address = {a.url: a for a in articles} | {a.canonical_url: a for a in articles}
    stories: defaultdict[str, list[Article]] = defaultdict(list)
    for article in ranked:
        stories[_story_end(article, by_address)].append(article)

    def shown(end: str, rows: list[Article]) -> Article:
        if len(rows) == 1:
            return rows[0]
        return max(rows, key=lambda a: (_is_article(a), a.id == end))

    kept = {shown(end, rows).id: end for end, rows in stories.items()}
    return [(a, kept[a.id]) for a in ranked if a.id in kept]


def one_per_cluster(rows: Sequence[Article]) -> list[tuple[Article, int]]:
    """``rows`` with one row per ``cluster_id``, in the input order (#325).

    The row shown has the highest score; ties go to a row that shows as an article,
    then the earliest effective time, then the smallest id. Each comes with the
    number of distinct sources among its cluster's rows. Rows without a cluster
    are kept as they are, each its own story.
    """
    clusters: defaultdict[str, list[Article]] = defaultdict(list)
    for row in rows:
        if row.cluster_id is not None:
            clusters[row.cluster_id].append(row)

    def best(group: list[Article]) -> Article:
        return min(
            group,
            key=lambda a: (-(a.score or 0.0), not _is_article(a), effective_time(a), a.id),
        )

    kept = {best(group).id: len({a.source_id for a in group}) for group in clusters.values()}
    return [
        (row, 1 if row.cluster_id is None else kept[row.id])
        for row in rows
        if row.cluster_id is None or row.id in kept
    ]


def build_feed(
    articles: Sequence[Article],
    sources: Collection[Source],
    *,
    now: datetime,
    run_id: str,
    window_hours: float | None = FEED_WINDOW_HOURS,
    limit: int = FEED_LIMIT,
) -> BuiltFeed:
    """Select, order and render the feed (ADR-0006).

    Candidates are scored, unmuted articles from enabled sources with weight
    above zero whose effective time is within ``window_hours`` of ``now``
    (None: no window, as for views, ADR-0013).
    Articles that can't form a valid item are dropped and logged, so one bad
    article never blocks the feed. Rows linked by the canonical their page
    declared are one story and take one place (``_one_per_story``), and rows of one cluster take one
    place too, with a coverage count (``one_per_cluster``).
    """
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError(f"invalid run id {run_id!r}")
    live = {s.id for s in sources if s.enabled and s.weight > 0}
    cutoff = None if window_hours is None else now - timedelta(hours=window_hours)
    candidates = [
        a
        for a in articles
        if a.score is not None
        and "muted" not in a.score_breakdown
        and a.source_id in live
        and (cutoff is None or effective_time(a) >= cutoff)
    ]
    # NaN would make the sort order undefined, so sort valid articles only.
    valid: list[Article] = []
    dropped = 0
    for article in candidates:
        reason = _problem(article)
        if reason is None:
            valid.append(article)
            continue
        dropped += 1
        log.warning(
            "dropped article from feed",
            extra={
                "stage": _STAGE,
                "source_id": article.source_id,
                "url": article.canonical_url,
                "reason": reason,
            },
        )
    valid.sort(key=lambda a: (-(a.score or 0.0), -effective_time(a).timestamp(), a.id))
    stories = _one_per_story(valid, articles)
    story_ids = {a.id: story_id for a, story_id in stories}
    clustered = one_per_cluster([a for a, _ in stories])
    items = [
        _item(a, story_ids[a.id], as_article=_is_article(a), coverage=count)
        for a, count in clustered[:limit]
    ]
    feed = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _timestamp(now),
        "run_id": run_id,
        "items": items,
    }
    return BuiltFeed(feed, len(candidates), dropped, len(valid) - len(clustered))


def publish_feed(
    services: Services,
    sources: Collection[Source],
    *,
    run_id: str,
    now: datetime,
    window_hours: float = FEED_WINDOW_HOURS,
    articles: Sequence[Article] | None = None,
) -> BuiltFeed:
    """Build the feed from stored articles and write it to the ``BlobStore``.

    ``articles``, if given, is the caller's read of the window, so it isn't read twice.
    Writes ``feeds/<run_id>.json`` first and ``feed.json`` second, so a failed
    write leaves the previous ``feed.json`` in place. Errors propagate: the
    caller records the stage as failed.
    """
    since = now - timedelta(hours=window_hours)
    # effective_time <= fetched_at, so anything inside the window was fetched in it.
    if articles is None:
        articles = services.repository.list_articles(since=since)
    built = build_feed(
        articles,
        sources,
        now=now,
        run_id=run_id,
        window_hours=window_hours,
    )
    data = json.dumps(built.feed, ensure_ascii=False, allow_nan=False, indent=1).encode("utf-8")
    services.blobs.put(f"feeds/{run_id}.json", data, "application/json")
    services.blobs.put(FEED_KEY, data, "application/json")
    log.info(
        "feed published",
        extra={
            "stage": _STAGE,
            "run_id": run_id,
            "items": len(built.feed["items"]),
            "dropped": built.dropped,
            "duplicates": built.duplicates,
            "bytes": len(data),
        },
    )
    return built
