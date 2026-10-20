"""Failure-reason categories and the per-run metrics behind the ``run finished`` log entry.

Pure functions only: the pipeline records one ``ExtractionRecord`` per article and
one ``FeedRecord`` per source, and ``summarise_run`` folds them into the dict
that is logged (and that ``newsfeed report`` will reuse for its categories).
``RunTally`` stores the failure reasons and per-domain medians in ``RunStats``;
the rest of the summary is logged only.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from newsfeed.models import ExtractionStatus

__all__ = [
    "REASONS",
    "ExtractionRecord",
    "FeedRecord",
    "reason_category",
    "summarise_run",
]

REASONS: Final = (
    "http_403",
    "http_429",
    "http_4xx",
    "http_5xx",
    "robots",
    "timeout",
    "connection",
    "too_short",
    "no_content",
    "other",
)
"""Every value ``reason_category`` can return."""

_HTTP_STATUS = re.compile(r"\bHTTP[ /]?(\d{3})\b", re.I)
_CONNECTION_MARKERS: Final = (
    "connecterror",
    "connection refused",
    "connection reset",
    "connection error",
    "name or service not known",
    "temporary failure in name resolution",
    "getaddrinfo",
    "nodename nor servname",
)
_TOO_SHORT = re.compile(r"\bonly \d+ words\b", re.I)


def reason_category(error: str | None) -> str:
    """Map an error message to one of ``REASONS``.

    Matches on the text because that is all an article keeps (``extraction.error``),
    so the same function works on stored data in a later report. Unrecognised
    or empty errors are ``other``.
    """
    if not error:
        return "other"
    text = error.lower()
    if "robots.txt" in text:
        return "robots"
    match = _HTTP_STATUS.search(error)
    if match:
        code = int(match.group(1))
        if code == 408:  # Request Timeout is transient, unlike the rest of the 4xx
            return "timeout"
        if code == 403:
            return "http_403"
        if code == 429:
            return "http_429"
        if 400 <= code < 500:
            return "http_4xx"
        if 500 <= code < 600:
            return "http_5xx"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if any(m in text for m in _CONNECTION_MARKERS):
        return "connection"
    if _TOO_SHORT.search(error):
        return "too_short"
    if "no usable content" in text or "no text extracted" in text or "no main content" in text:
        return "no_content"
    return "other"


@dataclass(frozen=True)
class ExtractionRecord:
    """The outcome of one article, as the run summary needs it."""

    domain: str
    status: ExtractionStatus
    word_count: int = 0
    error: str | None = None


@dataclass(frozen=True)
class FeedRecord:
    """The outcome of fetching one source's feed."""

    outcome: str  # "ok", "not_modified", "failed" or "skipped"
    http_status: int | None = None
    new: int = 0
    reason: str | None = None


def _word_stats(counts: list[int]) -> dict[str, float | int]:
    return {"median": statistics.median(counts), "min": min(counts), "n": len(counts)}


def summarise_run(
    run_id: str,
    durations: Mapping[str, float],
    extractions: Iterable[ExtractionRecord],
    feeds: Mapping[str, FeedRecord],
) -> dict[str, Any]:
    """Fold per-article and per-feed records into the ``run finished`` metrics.

    ``words_by_domain`` covers ``ok`` results only: a truncated ``partial``
    would hide exactly the regression the median is there to expose.
    """
    statuses: defaultdict[str, Counter[str]] = defaultdict(Counter)
    failures: Counter[str] = Counter()
    words: defaultdict[str, list[int]] = defaultdict(list)
    for record in extractions:
        statuses[record.domain][record.status.value] += 1
        if record.status is ExtractionStatus.FAILED:
            failures[reason_category(record.error)] += 1
        elif record.status is ExtractionStatus.OK:
            words[record.domain].append(record.word_count)
    return {
        "run_id": run_id,
        "duration_s": {stage: round(seconds, 3) for stage, seconds in durations.items()},
        "articles_by_domain": {d: dict(c) for d, c in sorted(statuses.items())},
        "failures_by_reason": dict(sorted(failures.items())),
        "feeds": {
            source_id: {
                "outcome": f.outcome,
                "http_status": f.http_status,
                "new": f.new,
                "reason": f.reason,
            }
            for source_id, f in sorted(feeds.items())
        },
        "words_by_domain": {d: _word_stats(w) for d, w in sorted(words.items())},
    }
