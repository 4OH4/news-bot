"""The primary extractor, built on Trafilatura (FR-5).

Trafilatura is asked for HTML output with formatting, images and links, plus
page metadata, in a single pass. Its HTML passes through ``sanitize_html`` like
every other extractor's, and the word count comes from the sanitised content's
plain text so it is comparable across extractors.
"""

from __future__ import annotations

from typing import Any, Final
from urllib.parse import urljoin, urlsplit

import trafilatura
from pydantic import TypeAdapter

from newsfeed.extract.sanitize import html_to_text, sanitize_html
from newsfeed.logging import get_logger
from newsfeed.models import ExtractionResult, ExtractionStatus, LenientUtcDatetime

__all__ = ["DEFAULT_MIN_WORDS", "TrafilaturaExtractor"]

DEFAULT_MIN_WORDS: Final = 150
"""Word count at or above which an extraction counts as ``ok``."""

# htmldate defaults to a date-only string; asking for the time and offset keeps
# them when the page has them. ``original_date`` prefers the publication date
# over the last-modified date.
_DATE_PARAMS: Final[dict[str, Any]] = {
    "original_date": True,
    "outputformat": "%Y-%m-%dT%H:%M:%S%z",
}

# Publishers' dates are messy; coerce them the way the model does (ADR-0002).
_PUBLISHED_AT: Final = TypeAdapter(LenientUtcDatetime)

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
        try:
            return self._extract(html, url)
        except Exception as exc:
            # Stage boundary for one page: record the failure and carry on.
            log.warning(
                "extraction failed",
                extra={
                    "stage": "extract",
                    "url": url,
                    "extractor": self.name,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            return ExtractionResult(
                status=ExtractionStatus.FAILED,
                method=self.name,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _extract(self, html: str, url: str) -> ExtractionResult:
        document = trafilatura.extract_with_metadata(
            html,
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

        title = _clean(document.title)
        content_html = sanitize_html(document.text or "", url, title=title)
        text = html_to_text(content_html)
        word_count = len(text.split())
        status, error = self._grade(word_count)
        return ExtractionResult(
            status=status,
            method=self.name,
            title=title,
            author=_clean(document.author),
            published_at=_PUBLISHED_AT.validate_python(document.date),
            content_html=content_html or None,
            text=text or None,
            lead_image=_image_url(document.image, url),
            word_count=word_count,
            error=error,
        )

    def _grade(self, word_count: int) -> tuple[ExtractionStatus, str | None]:
        if word_count >= self._min_words:
            return ExtractionStatus.OK, None
        if word_count > 0:
            return (
                ExtractionStatus.PARTIAL,
                f"only {word_count} words extracted (minimum {self._min_words})",
            )
        return ExtractionStatus.FAILED, "no text extracted"


def _clean(value: str | None) -> str | None:
    """Collapse whitespace; None for missing or blank values."""
    if value is None:
        return None
    return " ".join(value.split()) or None


def _image_url(value: str | None, page_url: str) -> str | None:
    """Make a metadata image URL absolute; None unless it is http(s)."""
    if not value or not value.strip():
        return None
    url = urljoin(page_url, value.strip())
    return url if urlsplit(url).scheme.lower() in {"http", "https"} else None
