"""RSS 2.0 and Atom 1.0 ingester (FR-1, FR-3).

``RssIngester`` fetches a feed through the ``Fetcher`` port, conditionally on
the source's saved ETag and Last-Modified, and parses the bytes with
feedparser. Each entry becomes a pending ``Article`` whose ``canonical_url`` is
its link as given: canonicalisation and dedupe happen centrally in the
pipeline (#12, #19).

The work is split so other feed-based ingesters (e.g. Google News, #11) can
reuse it: ``ingest_feed`` does the conditional fetch and state update for any
feed URL, and ``parse_feed`` is a pure function from feed bytes to articles.
"""

from __future__ import annotations

import calendar
import io
import logging
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

import feedparser

from newsfeed.ingest.text import collapse_whitespace, html_to_text, truncate
from newsfeed.logging import get_logger
from newsfeed.models import Article, Source, SourceState, SourceType
from newsfeed.ports import Fetcher

__all__ = [
    "SUMMARY_MAX_CHARS",
    "FeedFetchError",
    "RssIngester",
    "ingest_feed",
    "parse_feed",
]

log = get_logger(__name__)

SUMMARY_MAX_CHARS = 500
"""Article summaries are cut to this many characters (including the "…")."""

_STAGE = "ingest"

# feedparser bozo exceptions that only complain about HTTP headers.
_BENIGN_BOZO = (feedparser.NonXMLContentType, feedparser.CharacterEncodingOverride)

# Content types whose values are markup; anything else feedparser has already
# decoded to plain text, and decoding it again would eat text like "<canvas>".
_HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})


class FeedFetchError(RuntimeError):
    """The feed could not be fetched (network error, robots.txt, HTTP 4xx/5xx).

    Raised rather than returning no articles, so the pipeline's per-source
    error handling logs and counts it, and the saved state stays as it was.

    Attributes:
        status_code: the HTTP status (e.g. 404 or 410 for a dead feed), or None
            when there was no response (network error, robots.txt refusal).
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RssIngester:
    """``SourceIngester`` for ``type: rss`` sources (RSS 0.9x/1.0/2.0 and Atom).

    Args:
        clock: returns the current time; used for ``fetched_at`` and
            ``SourceState.last_fetched_at``. Injectable for tests.
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
        """Always ``SourceType.RSS``."""
        return SourceType.RSS

    def ingest(
        self, source: Source, fetcher: Fetcher, state: SourceState
    ) -> tuple[list[Article], SourceState]:
        """Fetch ``source.url`` and return its entries as pending articles.

        See ``ingest_feed`` for the behaviour; raises ``ValueError`` if the
        source has no url (``models.Source`` already rejects that for rss).
        """
        if not source.url:
            raise ValueError(f"source {source.id!r} has no url")
        return ingest_feed(
            source,
            source.url,
            fetcher,
            state,
            clock=self._clock,
            summary_max_chars=self._summary_max_chars,
        )


def ingest_feed(
    source: Source,
    feed_url: str,
    fetcher: Fetcher,
    state: SourceState,
    *,
    clock: Callable[[], datetime] = _utc_now,
    summary_max_chars: int = SUMMARY_MAX_CHARS,
) -> tuple[list[Article], SourceState]:
    """Conditionally fetch the feed at ``feed_url`` and parse it into articles.

    * A 304 Not Modified returns no articles and ``state`` unchanged.
    * A successful fetch returns the parsed articles and a new state carrying
      the response's ETag and Last-Modified (None if the server stopped sending
      them), the fetch time and the HTTP status.
    * A failed fetch raises ``FeedFetchError``.
    * A malformed feed is logged and whatever parsed is returned; entries that
      can't become an ``Article`` are skipped and logged (see ``parse_feed``).
    """
    result = fetcher.fetch(feed_url, etag=state.etag, last_modified=state.last_modified)
    if result.not_modified:
        log.info(
            "feed not modified",
            extra={"stage": _STAGE, "source_id": source.id, "url": feed_url},
        )
        return [], state.model_copy(deep=True)
    if result.error is not None or result.status_code is None or result.status_code >= 400:
        reason = result.error or f"HTTP {result.status_code}"
        raise FeedFetchError(
            f"fetching feed {feed_url} for source {source.id!r} failed: {reason}",
            status_code=result.status_code,
        )

    fetched_at = clock()
    articles = parse_feed(
        result.content,
        source_id=source.id,
        feed_url=result.final_url,
        fetched_at=fetched_at,
        headers=result.headers,
        summary_max_chars=summary_max_chars,
    )
    new_state = SourceState.model_validate(
        {
            **state.model_dump(),
            "etag": result.headers.get("etag"),
            "last_modified": result.headers.get("last-modified"),
            "last_fetched_at": fetched_at,
            "last_status": result.status_code,
        }
    )
    log.info(
        "feed ingested",
        extra={
            "stage": _STAGE,
            "source_id": source.id,
            "url": feed_url,
            "article_count": len(articles),
        },
    )
    return articles, new_state


def parse_feed(
    content: bytes,
    *,
    source_id: str,
    feed_url: str,
    fetched_at: datetime,
    headers: Mapping[str, str] | None = None,
    summary_max_chars: int = SUMMARY_MAX_CHARS,
) -> list[Article]:
    """Parse RSS/Atom ``content`` into pending articles, in feed order.

    ``feed_url`` is the URL the feed was served from (after redirects) and is
    the base for relative links. ``headers`` are the response headers; the
    content type's charset helps feedparser decode the bytes.

    Malformed feeds (feedparser's ``bozo`` flag) are logged as a warning and
    whatever parsed is returned; a feed whose only fault is a non-XML
    Content-Type is logged at debug level. An entry that can't become an ``Article`` is
    skipped with a structured warning; the rest of the feed is still returned.
    """
    response_headers = {"content-location": feed_url}
    content_type = (headers or {}).get("content-type")
    if content_type:
        response_headers["content-type"] = content_type
    # BytesIO, never a str: feedparser treats a str as a URL or file path to open.
    parsed = feedparser.parse(io.BytesIO(content), response_headers=response_headers)

    if parsed.get("bozo"):
        exc = parsed.get("bozo_exception")
        # Many servers send feeds as text/plain, text/html, text/xml without a
        # charset, or with no Content-Type at all. feedparser flags that as
        # bozo (NonXMLContentType, or CharacterEncodingOverride once the body
        # has any non-ASCII text), but if the body still parsed as a feed
        # nothing is wrong with it, and a warning on every run would be noise.
        benign = isinstance(exc, _BENIGN_BOZO) and bool(parsed.get("version"))
        log.log(
            logging.DEBUG if benign else logging.WARNING,
            "malformed feed; using the entries that parsed",
            extra={
                "stage": _STAGE,
                "source_id": source_id,
                "url": feed_url,
                "error_type": type(exc).__name__ if exc is not None else "unknown",
                "error": str(exc) if exc is not None else "",
                "entry_count": len(parsed.get("entries", [])),
            },
        )

    articles: list[Article] = []
    for entry in parsed.get("entries", []):
        try:
            articles.append(
                _entry_to_article(
                    entry,
                    source_id=source_id,
                    base_url=feed_url,
                    fetched_at=fetched_at,
                    summary_max_chars=summary_max_chars,
                )
            )
        # Feed content is untrusted third-party data, and one odd entry must
        # not lose the rest of the feed, so any per-entry failure is a skip.
        except Exception as exc:
            log.warning(
                "skipped feed entry",
                extra={
                    "stage": _STAGE,
                    "source_id": source_id,
                    "url": _raw_link(entry) or feed_url,
                    "feed_url": feed_url,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
    return articles


def _entry_to_article(
    entry: Mapping[str, Any],
    *,
    source_id: str,
    base_url: str,
    fetched_at: datetime,
    summary_max_chars: int,
) -> Article:
    url = _entry_url(entry, base_url)
    summary = truncate(_entry_summary(entry), summary_max_chars)
    return Article(
        canonical_url=url,
        url=url,
        source_id=source_id,
        title=_as_text(entry.get("title"), entry.get("title_detail")),
        # feedparser decodes author names to plain text and gives them no type.
        author=collapse_whitespace(entry.get("author")) or None,
        published_at=_entry_published(entry),
        fetched_at=fetched_at,
        summary=summary or None,
    )


def _raw_link(entry: Mapping[str, Any]) -> str | None:
    link = entry.get("link")
    return (link.strip() or None) if isinstance(link, str) else None


def _entry_url(entry: Mapping[str, Any], base_url: str) -> str:
    """Return the entry's absolute http(s) link, resolving relative links."""
    link = _raw_link(entry)
    if link is None:
        raise ValueError("entry has no link")
    url = urljoin(base_url, link)
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"entry link {link!r} is not an http(s) URL")
    return url


def _as_text(value: Any, detail: Any) -> str:
    """Plain text of a feedparser text construct, given its ``*_detail`` mapping.

    Only HTML/XHTML values are stripped of markup. feedparser has already
    decoded entities in plain-text values, so a title such as "Using <canvas>
    elements" is literal text and must not be parsed as HTML again.
    """
    if not isinstance(value, str):
        return ""
    content_type = detail.get("type") if isinstance(detail, Mapping) else None
    if content_type in _HTML_TYPES:
        return html_to_text(value)
    return collapse_whitespace(value)


def _entry_summary(entry: Mapping[str, Any]) -> str:
    """Plain-text summary, or the start of the content if there is none (Atom)."""
    summary = entry.get("summary")
    if isinstance(summary, str) and summary.strip():
        detail = entry.get("summary_detail")
        if detail is None:
            # feedparser copies Atom <content> into ``summary`` without a
            # detail; the content item carries the type.
            for content in entry.get("content") or []:
                if isinstance(content, Mapping) and content.get("value") == summary:
                    detail = content
                    break
        return _as_text(summary, detail)
    for content in entry.get("content") or []:
        if isinstance(content, Mapping):
            text = _as_text(content.get("value"), content)
            if text:
                return text
    return ""


def _entry_published(entry: Mapping[str, Any]) -> datetime | None:
    """Publication time in UTC, falling back to the updated time, else None.

    feedparser parses the date formats seen in the wild (RFC 822, W3CDTF, ...)
    and normalises them to UTC ``struct_time``s; a date it can't parse is
    treated as absent.
    """
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if isinstance(parsed, time.struct_time):
            try:
                return datetime.fromtimestamp(calendar.timegm(parsed), UTC)
            except (OverflowError, OSError, ValueError):
                continue
    return None
