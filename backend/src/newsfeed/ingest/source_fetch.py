"""Conditional fetching and state tracking shared by the ingesters.

Every ingester fetches one URL per source through the ``Fetcher`` port,
conditionally on the source's saved ETag and Last-Modified, and saves the
response's validators for the next run. ``fetch_source`` and ``next_state`` do
that once so the RSS, Google News and Hacker News ingesters behave the same.
"""

from __future__ import annotations

from datetime import datetime

from newsfeed.logging import get_logger
from newsfeed.models import FetchResult, Source, SourceState
from newsfeed.ports import Fetcher

__all__ = ["FeedFetchError", "fetch_source", "next_state"]

log = get_logger(__name__)

_STAGE = "ingest"


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


def fetch_source(
    source: Source, url: str, fetcher: Fetcher, state: SourceState
) -> FetchResult | None:
    """Conditionally fetch ``url`` for ``source``.

    Returns the successful result, or None for a 304 Not Modified. Raises
    ``FeedFetchError`` for a failed fetch, including a robots.txt refusal: the
    ``Fetcher`` applies robots.txt and the refusal is never worked around.
    """
    result = fetcher.fetch(url, etag=state.etag, last_modified=state.last_modified)
    if result.not_modified:
        log.info(
            "feed not modified",
            extra={"stage": _STAGE, "source_id": source.id, "url": url},
        )
        return None
    if result.error is not None or result.status_code is None or result.status_code >= 400:
        reason = result.error or f"HTTP {result.status_code}"
        raise FeedFetchError(
            f"fetching feed {url} for source {source.id!r} failed: {reason}",
            status_code=result.status_code,
        )
    return result


def next_state(state: SourceState, result: FetchResult, fetched_at: datetime) -> SourceState:
    """Return ``state`` updated from a successful fetch.

    Carries the response's ETag and Last-Modified (None if the server stopped
    sending them), the fetch time and the HTTP status.
    """
    return SourceState.model_validate(
        {
            **state.model_dump(),
            "etag": result.headers.get("etag"),
            "last_modified": result.headers.get("last-modified"),
            "last_fetched_at": fetched_at,
            "last_status": result.status_code,
        }
    )
