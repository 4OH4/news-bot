"""Tests for the reason categories and run summary."""

from __future__ import annotations

import pytest

from newsfeed.metrics import REASONS, ExtractionRecord, FeedRecord, reason_category, summarise_run
from newsfeed.models import ExtractionStatus


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("PageUnavailableError: HTTP 403", "http_403"),
        ("HTTP 404", "http_4xx"),
        ("HTTP 408", "timeout"),
        ("HTTP 429", "http_429"),
        ("HTTP 410", "http_4xx"),
        ("PageUnavailableError: HTTP 503", "http_5xx"),
        ("disallowed by robots.txt", "robots"),
        ("robots.txt unavailable", "robots"),
        ("ReadTimeout: The read operation timed out", "timeout"),
        ("PageUnavailableError: ConnectTimeout", "timeout"),
        ("PageUnavailableError: ConnectError: All connection attempts failed", "connection"),
        ("ConnectError: [Errno -2] Name or service not known", "connection"),
        ("only 40 words extracted (minimum 100)", "too_short"),
        ("no text extracted", "no_content"),
        ("no usable content (trafilatura: no main content found)", "no_content"),
        ("ValueError: boom", "other"),
        ("", "other"),
        (None, "other"),
    ],
)
def test_reason_category(error: str | None, expected: str) -> None:
    assert reason_category(error) == expected
    assert expected in REASONS


def test_summarise_run_medians_use_ok_results_only() -> None:
    ok = ExtractionStatus.OK
    records = [
        ExtractionRecord("a.example", ok, word_count=100),
        ExtractionRecord("a.example", ok, word_count=300),
        ExtractionRecord("a.example", ok, word_count=200),
        ExtractionRecord("a.example", ExtractionStatus.PARTIAL, word_count=10, error="only 10"),
        ExtractionRecord("b.example", ExtractionStatus.FAILED, error="HTTP 403"),
    ]
    summary = summarise_run(
        "r1", {"extract": 1.23456}, records, {"s": FeedRecord("ok", http_status=200, new=2)}
    )

    assert summary["words_by_domain"] == {"a.example": {"median": 200, "min": 100, "n": 3}}
    assert summary["failures_by_reason"] == {"http_403": 1}
    assert summary["articles_by_domain"]["a.example"] == {"ok": 3, "partial": 1}
    assert summary["duration_s"] == {"extract": 1.235}
    assert summary["feeds"]["s"]["new"] == 2
