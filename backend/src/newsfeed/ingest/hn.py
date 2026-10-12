"""Hacker News ingester (FR-1), via the Algolia HN Search API.

A ``type: hn`` source fetches the stories currently on the HN front page from
``https://hn.algolia.com/api/v1/search?tags=front_page`` through the
``Fetcher`` (so robots.txt, rate limits and conditional GETs apply as for any
feed), keeps those with at least ``min_points`` points, and returns the
``max_items`` highest-scoring as pending articles.

A story's article URL is the page it links to. Stories without one (Ask HN,
Show HN text posts) link to their HN item page instead. Every summary starts
with the HN discussion link, points and comment count.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import urlencode, urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from newsfeed.ingest.rss import SUMMARY_MAX_CHARS
from newsfeed.ingest.source_fetch import fetch_source, next_state
from newsfeed.ingest.text import collapse_whitespace, html_to_text, truncate
from newsfeed.logging import get_logger
from newsfeed.models import Article, Source, SourceState, SourceType
from newsfeed.ports import Fetcher

__all__ = [
    "ALGOLIA_SEARCH_URL",
    "HN_ITEM_URL",
    "HackerNewsIngester",
    "HackerNewsOptions",
    "front_page_url",
    "item_url",
    "parse_hits",
]

log = get_logger(__name__)

_STAGE = "ingest"

ALGOLIA_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
"""Algolia HN Search API endpoint (documented at https://hn.algolia.com/api)."""

HN_ITEM_URL = "https://news.ycombinator.com/item"
"""HN discussion page; the story id goes in the ``id`` query parameter."""

_PAGE_SIZE = 50
"""Hits requested per fetch. The front page holds 30 stories, so one page covers it."""


class HackerNewsOptions(BaseModel):
    """``options`` of an ``hn`` source. Unknown keys are rejected.

    Attributes:
        min_points: skip stories with fewer points than this (default 0).
        max_items: keep at most this many stories, highest points first
            (default 30, a full front page).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    min_points: Annotated[StrictInt, Field(ge=0)] = 0
    max_items: Annotated[StrictInt, Field(ge=1)] = 30


def front_page_url() -> str:
    """Return the Algolia URL for the current HN front page."""
    return f"{ALGOLIA_SEARCH_URL}?{urlencode({'tags': 'front_page', 'hitsPerPage': _PAGE_SIZE})}"


def item_url(story_id: str) -> str:
    """Return the HN discussion URL for ``story_id``."""
    return f"{HN_ITEM_URL}?{urlencode({'id': story_id})}"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class HackerNewsIngester:
    """``SourceIngester`` for ``type: hn`` sources.

    ``source.url`` overrides the default front-page query with another Algolia
    search URL returning the same JSON shape; ``source.query`` is not used.

    Args:
        clock: returns the current time; injectable for tests.
        summary_max_chars: summaries longer than this are truncated.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = _utc_now,
        summary_max_chars: int = SUMMARY_MAX_CHARS,
    ) -> None:
        self._clock = clock
        self._summary_max_chars = summary_max_chars

    @property
    def source_type(self) -> SourceType:
        """Always ``SourceType.HN``."""
        return SourceType.HN

    def ingest(
        self, source: Source, fetcher: Fetcher, state: SourceState
    ) -> tuple[list[Article], SourceState]:
        """Fetch the HN front page and return its stories as pending articles.

        A 304 yields no articles and ``state`` unchanged; a failed fetch
        (including a robots.txt refusal) raises ``FeedFetchError``; invalid
        ``options`` or a response that isn't Algolia search JSON raise
        ``ValueError``. Individual hits that can't be used are skipped and logged.
        """
        options = HackerNewsOptions.model_validate(source.options)
        url = source.url or front_page_url()
        result = fetch_source(source, url, fetcher, state)
        if result is None:
            return [], state.model_copy(deep=True)

        fetched_at = self._clock()
        articles = parse_hits(
            result.content,
            source_id=source.id,
            fetched_at=fetched_at,
            min_points=options.min_points,
            max_items=options.max_items,
            summary_max_chars=self._summary_max_chars,
        )
        log.info(
            "feed ingested",
            extra={
                "stage": _STAGE,
                "source_id": source.id,
                "url": url,
                "article_count": len(articles),
            },
        )
        return articles, next_state(state, result, fetched_at)


def parse_hits(
    content: bytes,
    *,
    source_id: str,
    fetched_at: datetime,
    min_points: int = 0,
    max_items: int | None = None,
    summary_max_chars: int = SUMMARY_MAX_CHARS,
) -> list[Article]:
    """Parse an Algolia search response into pending articles.

    Hits with fewer than ``min_points`` points are dropped; the rest are sorted
    by points, highest first (ties keep the API's order), and cut to
    ``max_items``. A hit that can't become an ``Article`` is skipped with a
    structured warning.

    Raises:
        ValueError: if ``content`` is not JSON with a ``hits`` list.
    """
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"HN response is not valid JSON: {exc}") from exc
    hits = payload.get("hits") if isinstance(payload, dict) else None
    if not isinstance(hits, list):
        raise ValueError("HN response has no 'hits' list")

    scored: list[tuple[int, Article]] = []
    for hit in hits:
        try:
            points = _points(hit)
            if points < min_points:
                continue
            scored.append(
                (
                    points,
                    _hit_to_article(
                        hit,
                        points=points,
                        source_id=source_id,
                        fetched_at=fetched_at,
                        summary_max_chars=summary_max_chars,
                    ),
                )
            )
        # API data is untrusted, and one odd hit must not lose the rest.
        except Exception as exc:
            object_id = hit.get("objectID") if isinstance(hit, Mapping) else None
            log.warning(
                "skipped HN hit",
                extra={
                    "stage": _STAGE,
                    "source_id": source_id,
                    "url": item_url(str(object_id)) if object_id else "",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [article for _, article in scored[:max_items]]


def _points(hit: Any) -> int:
    if not isinstance(hit, Mapping):
        raise ValueError("hit is not an object")
    points = hit.get("points")
    if points is None:
        return 0
    if isinstance(points, bool) or not isinstance(points, int):
        raise ValueError(f"hit has non-integer points {points!r}")
    return points


def _hit_to_article(
    hit: Mapping[str, Any],
    *,
    points: int,
    source_id: str,
    fetched_at: datetime,
    summary_max_chars: int,
) -> Article:
    object_id = hit.get("objectID")
    if not isinstance(object_id, str | int) or isinstance(object_id, bool) or not str(object_id):
        raise ValueError("hit has no objectID")
    discussion = item_url(str(object_id))

    title = collapse_whitespace(hit.get("title")) if isinstance(hit.get("title"), str) else ""
    if not title:
        raise ValueError("hit has no title")

    link = _story_link(hit.get("url"))
    comments = hit.get("num_comments")
    comment_count = comments if isinstance(comments, int) and not isinstance(comments, bool) else 0
    header = f"Hacker News discussion ({points} points, {comment_count} comments): {discussion}"
    # Text posts (Ask HN) carry their body as HTML; the header goes first so
    # truncating a long body never cuts the discussion link.
    body = html_to_text(hit.get("story_text")) if isinstance(hit.get("story_text"), str) else ""
    summary = header
    if body and len(header) + 3 < summary_max_chars:
        summary = f"{header} — {truncate(body, summary_max_chars - len(header) - 3)}"

    # For a link the HN submitter isn't the article's author; for a text post
    # they are.
    author = hit.get("author") if link is None else None
    return Article(
        canonical_url=link or discussion,
        url=link or discussion,
        source_id=source_id,
        title=title,
        author=(collapse_whitespace(author) or None) if isinstance(author, str) else None,
        published_at=_submitted_at(hit),
        fetched_at=fetched_at,
        summary=truncate(summary, summary_max_chars),
    )


def _submitted_at(hit: Mapping[str, Any]) -> datetime | None:
    """When the story was submitted to HN, in UTC, or None if unknown."""
    seconds = hit.get("created_at_i")
    if isinstance(seconds, int) and not isinstance(seconds, bool):
        try:
            return datetime.fromtimestamp(seconds, UTC)
        except (OverflowError, OSError, ValueError):
            pass
    created = hit.get("created_at")
    if isinstance(created, str):
        try:
            parsed = datetime.fromisoformat(created.strip())
        except ValueError:
            return None
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    return None


def _story_link(value: Any) -> str | None:
    """The story's external http(s) URL, or None for a text post."""
    if not isinstance(value, str) or not value.strip():
        return None
    url = value.strip()
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None
    return url
