"""The publish stage: build ``feed.json`` from ranked articles and write it.

The format, selection, order and display rule are fixed by ADR-0006 and the
JSON Schema in ``docs/feed.schema.json``. ``build_feed`` is pure; ``publish_feed``
does the I/O through the ``Repository`` and ``BlobStore`` ports.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Collection, Sequence
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
_HTTP_URL = re.compile(r"^https?://\S+$")
_SOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_RUN_ID = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class BuiltFeed:
    """The feed document plus counts for the run statistics."""

    feed: dict[str, Any]
    candidates: int
    dropped: int
    links_for_missing_content: int


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
    status = article.extraction.status
    if status is ExtractionStatus.OK:
        return True
    return status is ExtractionStatus.PARTIAL and article.extraction.word_count >= PARTIAL_MIN_WORDS


def _item(article: Article, *, as_article: bool) -> dict[str, Any]:
    image = article.lead_image
    return {
        "id": article.id,
        "title": article.title,
        "url": article.canonical_url,
        "source_id": article.source_id,
        "domain": _domain(article.canonical_url),
        "author": article.author,
        "published_at": _timestamp(article.published_at) if article.published_at else None,
        "summary": article.summary,
        "lead_image": image if image and _HTTP_URL.match(image) else None,
        "score": article.score,
        "score_breakdown": dict(article.score_breakdown),
        "extraction_status": article.extraction.status.value,
        "display": "article" if as_article else "link",
        "word_count": article.extraction.word_count if as_article else None,
        "content_path": f"articles/{article.id}.html" if as_article else None,
    }


def _problem(article: Article) -> str | None:
    """Why this article can't be a valid feed item, or None. Mirrors the schema."""
    if not article.title.strip():
        return "empty title"
    if not _HTTP_URL.match(article.canonical_url) or not _domain(article.canonical_url):
        return "invalid url"
    if not _SOURCE_ID.match(article.source_id):
        return "invalid source id"
    if article.score is None or not math.isfinite(article.score):
        return "invalid score"
    if not all(math.isfinite(v) for v in article.score_breakdown.values()):
        return "invalid score breakdown"
    return None


def build_feed(
    articles: Sequence[Article],
    sources: Collection[Source],
    *,
    now: datetime,
    run_id: str,
    has_content: Callable[[Article], bool] = lambda _article: True,
    window_hours: float = FEED_WINDOW_HOURS,
    limit: int = FEED_LIMIT,
) -> BuiltFeed:
    """Select, order and render the feed (ADR-0006).

    Candidates are scored, unmuted articles from enabled sources with weight
    above zero whose effective time is within ``window_hours`` of ``now``.
    ``has_content`` says whether an article's cleaned HTML is still stored; it
    is only asked about articles that would otherwise be shown as articles.
    Articles that can't form a valid item are dropped and logged, so one bad
    article never blocks the feed.
    """
    if not _RUN_ID.match(run_id):
        raise ValueError(f"invalid run id {run_id!r}")
    live = {s.id for s in sources if s.enabled and s.weight > 0}
    cutoff = now - timedelta(hours=window_hours)
    candidates = [
        a
        for a in articles
        if a.score is not None
        and "muted" not in a.score_breakdown
        and a.source_id in live
        and effective_time(a) >= cutoff
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
    items: list[dict[str, Any]] = []
    missing = 0
    for article in valid[:limit]:
        as_article = _is_article(article)
        if as_article and not has_content(article):
            as_article = False
            missing += 1
        items.append(_item(article, as_article=as_article))
    feed = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _timestamp(now),
        "run_id": run_id,
        "items": items,
    }
    return BuiltFeed(feed, len(candidates), dropped, missing)


def _content_exists(services: Services, article: Article) -> bool:
    if not article.content_ref:
        return False
    try:
        return services.blobs.exists(article.content_ref)
    except Exception as exc:
        log.warning(
            "couldn't check article content; showing as a link",
            extra={
                "stage": _STAGE,
                "source_id": article.source_id,
                "url": article.canonical_url,
                "error": str(exc),
            },
        )
        return False


def publish_feed(
    services: Services,
    sources: Collection[Source],
    *,
    run_id: str,
    now: datetime,
    window_hours: float = FEED_WINDOW_HOURS,
) -> BuiltFeed:
    """Build the feed from stored articles and write it to the ``BlobStore``.

    Writes ``feeds/<run_id>.json`` first and ``feed.json`` second, so a failed
    write leaves the previous ``feed.json`` in place. Errors propagate: the
    caller records the stage as failed.
    """
    since = now - timedelta(hours=window_hours)
    # effective_time <= fetched_at, so anything inside the window was fetched in it.
    articles = services.repository.list_articles(since=since)
    built = build_feed(
        articles,
        sources,
        now=now,
        run_id=run_id,
        has_content=lambda a: _content_exists(services, a),
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
            "bytes": len(data),
        },
    )
    return built
