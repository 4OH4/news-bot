"""Behaviour shared by every extractor, so their results are comparable.

Each extractor hands its library's raw output to ``build_result``, which runs it
through ``sanitize_html``, counts words from the sanitised plain text and grades
the result with ``grade``. ``run_safely`` is the per-page error boundary: an
exception becomes a ``failed`` result and a structured warning. The chain uses
``grade`` too, to apply a per-domain ``min_words``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Final
from urllib.parse import urljoin, urlsplit

from pydantic import TypeAdapter

from newsfeed.extract.sanitize import html_to_text, sanitize_html
from newsfeed.models import ExtractionResult, ExtractionStatus, LenientUtcDatetime

__all__ = [
    "DEFAULT_MIN_WORDS",
    "build_result",
    "clean_text",
    "grade",
    "http_url",
    "run_safely",
]

DEFAULT_MIN_WORDS: Final = 150
"""Word count at or above which an extraction counts as ``ok``."""

# Publishers' dates are messy; coerce them the way the model does (ADR-0002).
_PUBLISHED_AT: Final = TypeAdapter(LenientUtcDatetime)


def grade(word_count: int, min_words: int) -> tuple[ExtractionStatus, str | None]:
    """Status and error for content of ``word_count`` words.

    ``ok`` at or above ``min_words``, ``partial`` (with the shortfall as the
    error) below it, and ``failed`` when there is no text at all.
    """
    if word_count >= min_words:
        return ExtractionStatus.OK, None
    if word_count > 0:
        return (
            ExtractionStatus.PARTIAL,
            f"only {word_count} words extracted (minimum {min_words})",
        )
    return ExtractionStatus.FAILED, "no text extracted"


def build_result(
    *,
    method: str,
    raw_html: str | None,
    url: str,
    min_words: int,
    title: str | None = None,
    author: str | None = None,
    published_at: str | datetime | None = None,
    lead_image: str | None = None,
) -> ExtractionResult:
    """Sanitise an extractor's raw HTML and grade it into an ``ExtractionResult``.

    ``title`` and ``author`` are whitespace-collapsed, ``published_at`` is
    coerced leniently and ``lead_image`` is made absolute (and dropped unless it
    is http(s)), so every extractor reports metadata the same way.
    """
    title = clean_text(title)
    content_html = sanitize_html(raw_html or "", url, title=title)
    text = html_to_text(content_html)
    word_count = len(text.split())
    status, error = grade(word_count, min_words)
    return ExtractionResult(
        status=status,
        method=method,
        title=title,
        author=clean_text(author),
        published_at=_PUBLISHED_AT.validate_python(published_at),
        content_html=content_html or None,
        text=text or None,
        lead_image=http_url(lead_image, url),
        word_count=word_count,
        error=error,
    )


def run_safely(
    name: str, url: str, extract: Callable[[], ExtractionResult], *, logger: logging.Logger
) -> ExtractionResult:
    """Call ``extract``; turn any exception into a ``failed`` result and a warning.

    This is the error boundary for one page, so one bad page never fails a run.
    The warning goes to ``logger``, normally the calling extractor's module logger.
    """
    try:
        return extract()
    except Exception as exc:
        logger.warning(
            "extraction failed",
            extra={
                "stage": "extract",
                "url": url,
                "extractor": name,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        return ExtractionResult(
            status=ExtractionStatus.FAILED,
            method=name,
            error=f"{type(exc).__name__}: {exc}",
        )


def clean_text(value: str | None) -> str | None:
    """Collapse whitespace; None for missing or blank values."""
    if value is None:
        return None
    return " ".join(value.split()) or None


def http_url(value: str | None, page_url: str) -> str | None:
    """Make a metadata URL absolute against ``page_url``; None unless it is http(s)."""
    if not value or not value.strip():
        return None
    url = urljoin(page_url, value.strip())
    return url if urlsplit(url).scheme.lower() in {"http", "https"} else None
