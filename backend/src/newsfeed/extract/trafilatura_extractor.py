"""The primary extractor, built on Trafilatura (FR-5).

Trafilatura is asked for HTML output with formatting, images and links, plus
page metadata, in a single pass. Its HTML passes through ``sanitize_html`` like
every other extractor's, and the word count comes from the sanitised content's
plain text so it is comparable across extractors.
"""

from __future__ import annotations

from typing import Any, Final

import trafilatura

from newsfeed.extract.common import DEFAULT_MIN_WORDS, build_result, run_safely
from newsfeed.extract.prune import strip_recent_articles
from newsfeed.logging import get_logger
from newsfeed.models import ExtractionResult, ExtractionStatus

__all__ = ["DEFAULT_MIN_WORDS", "TrafilaturaExtractor"]

# htmldate defaults to a date-only string; asking for the time and offset keeps
# them when the page has them. ``original_date`` prefers the publication date
# over the last-modified date.
_DATE_PARAMS: Final[dict[str, Any]] = {
    "original_date": True,
    "outputformat": "%Y-%m-%dT%H:%M:%S%z",
}

log = get_logger(__name__)


class TrafilaturaExtractor:
    """Extract an article's main content and metadata with Trafilatura.

    The status is ``ok`` when the content has at least ``min_words`` words,
    ``partial`` when it has fewer but some, and ``failed`` when it has none.
    Any exception is caught and returned as ``failed`` with the error recorded,
    so one bad page never fails a run. Safe to call from several threads: no
    state is shared between calls.
    """

    def __init__(self, *, min_words: int = DEFAULT_MIN_WORDS) -> None:
        """Create the extractor; ``min_words`` must be at least 1."""
        if min_words < 1:
            raise ValueError(f"min_words must be at least 1, got {min_words}")
        self._min_words = min_words

    @property
    def name(self) -> str:
        """Extractor name recorded as ``ExtractionResult.method``."""
        return "trafilatura"

    @property
    def min_words(self) -> int:
        """Word count at or above which an extraction counts as ``ok``."""
        return self._min_words

    def extract(self, html: str, url: str) -> ExtractionResult:
        """Extract the main content of ``html``, fetched from ``url``. Never raises."""
        return run_safely(self.name, url, lambda: self._extract(html, url), logger=log)

    def _extract(self, html: str, url: str) -> ExtractionResult:
        document = trafilatura.extract_with_metadata(
            strip_recent_articles(html),
            url=url,
            output_format="html",
            include_formatting=True,
            include_images=True,
            include_links=True,
            include_tables=True,
            include_comments=False,
            # Trafilatura's dedup cache is shared between calls; using it would
            # make results depend on what was extracted earlier.
            deduplicate=False,
            date_extraction_params=_DATE_PARAMS,
        )
        if document is None:
            return ExtractionResult(
                status=ExtractionStatus.FAILED,
                method=self.name,
                error="no main content found",
            )

        return build_result(
            method=self.name,
            raw_html=document.text,
            url=url,
            min_words=self._min_words,
            title=document.title,
            author=document.author,
            published_at=document.date,
            lead_image=document.image,
        )
