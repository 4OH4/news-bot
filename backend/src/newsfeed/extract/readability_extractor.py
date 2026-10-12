"""A fallback extractor built on readability-lxml (FR-5).

readability-lxml is a port of Arc90's Readability. It never fetches anything:
it only parses the HTML it is given. It finds the main content well on simple
article pages but extracts little metadata, so results carry the title and the
``<meta name="author">`` value only. Its output passes through ``sanitize_html``
like every other extractor's.
"""

from __future__ import annotations

from typing import Final

from readability import Document

from newsfeed.extract.common import DEFAULT_MIN_WORDS, build_result, run_safely
from newsfeed.logging import get_logger
from newsfeed.models import ExtractionResult

__all__ = ["ReadabilityExtractor"]

# readability-lxml returns this placeholder when the page has no author meta tag.
_NO_AUTHOR: Final = "[no-author]"

log = get_logger(__name__)


class ReadabilityExtractor:
    """Extract an article's main content with readability-lxml.

    Statuses are graded by word count exactly as in ``TrafilaturaExtractor``,
    and any exception (readability raises ``Unparseable`` for empty or broken
    documents) is returned as ``failed`` with the error recorded. Safe to call
    from several threads: each call builds its own ``Document``.
    """

    def __init__(self, *, min_words: int = DEFAULT_MIN_WORDS) -> None:
        """Create the extractor; ``min_words`` must be at least 1."""
        if min_words < 1:
            raise ValueError(f"min_words must be at least 1, got {min_words}")
        self._min_words = min_words

    @property
    def name(self) -> str:
        """Extractor name recorded as ``ExtractionResult.method``."""
        return "readability"

    @property
    def min_words(self) -> int:
        """Word count at or above which an extraction counts as ``ok``."""
        return self._min_words

    def extract(self, html: str, url: str) -> ExtractionResult:
        """Extract the main content of ``html``, fetched from ``url``. Never raises."""
        return run_safely(self.name, url, lambda: self._extract(html, url), logger=log)

    def _extract(self, html: str, url: str) -> ExtractionResult:
        # ``url`` makes readability absolutise links itself; the sanitiser would
        # do it anyway, but readability resolves ``<base href>`` too.
        document = Document(html, url=url)
        content = document.summary(html_partial=True)
        author = document.author()
        return build_result(
            method=self.name,
            raw_html=content,
            url=url,
            min_words=self._min_words,
            title=document.short_title(),
            author=None if author == _NO_AUTHOR else author,
        )
