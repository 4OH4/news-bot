"""Google News RSS ingester (FR-1): a discovery layer beyond fixed feeds.

A ``type: google_news`` source names a search ``query``; ``build_search_url``
turns it into a Google News search RSS URL, which is fetched and parsed with the
RSS ingester's ``ingest_feed``. A source may instead give a full Google News
RSS ``url`` (for example a topic or section feed), which is used as is.

Item links are ``news.google.com/rss/articles/...`` redirect URLs, not the
publishers' URLs, and are kept as given: resolving them is an open question
(see the PR for #11 and its follow-up issue). Only the documented RSS feeds are
used; Google's undocumented internal endpoints are never called.

Google formats each item's title as "Headline - Publisher" and its description
as the headline and publisher again, so ``adapt_entry`` strips the publisher
from the title and drops a summary that adds nothing to it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlencode

from pydantic import BaseModel, ConfigDict, StrictStr

from newsfeed.ingest.rss import SUMMARY_MAX_CHARS, ingest_feed
from newsfeed.ingest.text import collapse_whitespace
from newsfeed.models import Article, Source, SourceState, SourceType
from newsfeed.ports import Fetcher

__all__ = [
    "DEFAULT_GL",
    "DEFAULT_HL",
    "GOOGLE_NEWS_SEARCH_URL",
    "GoogleNewsIngester",
    "GoogleNewsOptions",
    "adapt_entry",
    "build_search_url",
]

GOOGLE_NEWS_SEARCH_URL = "https://news.google.com/rss/search"
"""Base URL of Google News search RSS feeds."""

DEFAULT_HL = "en-GB"
"""Default interface language (``hl``)."""

DEFAULT_GL = "GB"
"""Default edition country (``gl``)."""


class GoogleNewsOptions(BaseModel):
    """``options`` of a ``google_news`` source. Unknown keys are rejected.

    Attributes:
        hl: interface language, e.g. ``en-GB`` or ``fr``.
        gl: edition country, e.g. ``GB`` or ``FR``.
        ceid: edition id, ``<country>:<language>``. Defaults to ``gl`` plus the
            language part of ``hl`` (``GB:en`` for the defaults), so changing
            ``hl`` and ``gl`` is enough to switch edition.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    hl: StrictStr = DEFAULT_HL
    gl: StrictStr = DEFAULT_GL
    ceid: StrictStr | None = None


def build_search_url(
    query: str, *, hl: str = DEFAULT_HL, gl: str = DEFAULT_GL, ceid: str | None = None
) -> str:
    """Return the Google News search RSS URL for ``query``.

    Every parameter is percent-encoded (spaces as ``%20``), so queries with
    operators, quotes, ``&``, ``#``, ``+`` or non-ASCII text survive intact.
    ``:`` is left as is: it is valid in a query string, and keeps operators
    such as ``when:1d`` and the ``ceid`` value readable.

    Raises:
        ValueError: if ``query`` is blank.
    """
    query = query.strip()
    if not query:
        raise ValueError("Google News query must not be blank")
    if ceid is None:
        ceid = f"{gl}:{hl.split('-', 1)[0]}"
    params = {"q": query, "hl": hl, "gl": gl, "ceid": ceid}
    # urlencode's default quote_via (quote_plus) writes spaces as "+"; plain
    # percent-encoding is unambiguous for every server.
    return f"{GOOGLE_NEWS_SEARCH_URL}?{urlencode(params, safe=':', quote_via=quote)}"


def adapt_entry(entry: Mapping[str, Any], article: Article) -> Article:
    """Strip the " - Publisher" suffix from the title and drop an empty summary.

    The publisher's name comes from the item's ``<source>`` element. A summary
    that is just the headline and publisher again is removed; one listing
    related coverage (Google's story clusters) is kept.
    """
    source = entry.get("source")
    publisher = collapse_whitespace(source.get("title")) if isinstance(source, Mapping) else ""
    if not publisher:
        return article

    title = article.title
    suffix = f" - {publisher}"
    if title.endswith(suffix) and len(title) > len(suffix):
        title = title[: -len(suffix)].rstrip()

    summary = article.summary
    if summary is not None and summary in {
        title,
        f"{title} {publisher}",
        article.title,
        f"{article.title} {publisher}",
    }:
        summary = None

    return article.model_copy(update={"title": title, "summary": summary})


def _utc_now() -> datetime:
    return datetime.now(UTC)


class GoogleNewsIngester:
    """``SourceIngester`` for ``type: google_news`` sources.

    The source gives either ``query`` (a search, built with
    ``build_search_url`` from ``options`` ``hl``, ``gl`` and ``ceid``) or
    ``url`` (a full Google News RSS URL, used as is), not both.

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
        """Always ``SourceType.GOOGLE_NEWS``."""
        return SourceType.GOOGLE_NEWS

    def feed_url(self, source: Source) -> str:
        """Return the RSS URL for ``source``.

        Raises:
            ValueError: if the source has neither or both of ``query`` and
                ``url``, or its ``options`` are invalid.
        """
        options = GoogleNewsOptions.model_validate(source.options)
        query = (source.query or "").strip()
        if bool(query) == bool(source.url):
            raise ValueError(
                f"google_news source {source.id!r} needs exactly one of 'query' or 'url'"
            )
        if source.url:
            return source.url
        return build_search_url(query, hl=options.hl, gl=options.gl, ceid=options.ceid)

    def ingest(
        self, source: Source, fetcher: Fetcher, state: SourceState
    ) -> tuple[list[Article], SourceState]:
        """Fetch the source's Google News feed and return its items as pending articles.

        Fetching, 304 handling and state are as for ``ingest_feed``; a
        robots.txt refusal by the ``Fetcher`` raises ``FeedFetchError``.
        """
        return ingest_feed(
            source,
            self.feed_url(source),
            fetcher,
            state,
            clock=self._clock,
            summary_max_chars=self._summary_max_chars,
            adapt_entry=adapt_entry,
        )
