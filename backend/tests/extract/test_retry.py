"""Tests for the extraction retry policy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from newsfeed.extract.retry import RETRYABLE_REASONS, RetryPolicy, should_retry
from newsfeed.models import Article, ArticleExtraction, ExtractionStatus

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
POLICY = RetryPolicy()


def _article(
    status: ExtractionStatus = ExtractionStatus.FAILED,
    *,
    error_type: str | None = "timeout",
    error: str | None = "boom",
    attempts: int = 1,
    last_hours_ago: float | None = 2,
    fetched_hours_ago: float = 3,
) -> Article:
    return Article(
        canonical_url="https://news.example.com/a",
        url="https://news.example.com/a",
        source_id="gazette",
        title="A",
        fetched_at=NOW - timedelta(hours=fetched_hours_ago),
        extraction=ArticleExtraction(
            status=status,
            error=error,
            error_type=error_type,
            attempts=attempts,
            last_attempt_at=None
            if last_hours_ago is None
            else NOW - timedelta(hours=last_hours_ago),
        ),
    )


@pytest.mark.parametrize("reason", sorted(RETRYABLE_REASONS))
def test_transient_reasons_are_retried(reason: str) -> None:
    assert should_retry(_article(error_type=reason), NOW, POLICY)


@pytest.mark.parametrize(
    "reason", ["robots", "http_403", "http_4xx", "too_short", "no_content", "other", "paywall"]
)
def test_permanent_reasons_are_never_retried(reason: str) -> None:
    assert not should_retry(_article(error_type=reason), NOW, POLICY)


def test_reason_is_read_from_the_error_text_when_error_type_is_missing() -> None:
    transient = _article(error_type=None, error="PageUnavailableError: HTTP 503")
    permanent = _article(error_type=None, error="PageUnavailableError: HTTP 404")
    assert should_retry(transient, NOW, POLICY)
    assert not should_retry(permanent, NOW, POLICY)


@pytest.mark.parametrize(
    "status",
    [
        ExtractionStatus.OK,
        ExtractionStatus.PENDING,
        ExtractionStatus.SKIPPED,
        ExtractionStatus.PAYWALLED,
    ],
)
def test_only_failed_and_optionally_partial_articles_are_candidates(
    status: ExtractionStatus,
) -> None:
    assert not should_retry(_article(status), NOW, POLICY)


def test_partial_is_retried_only_behind_the_setting() -> None:
    partial = _article(ExtractionStatus.PARTIAL, error_type=None, error=None)
    assert not should_retry(partial, NOW, POLICY)
    assert should_retry(partial, NOW, RetryPolicy(retry_partial=True))


@pytest.mark.parametrize(
    ("attempts", "hours_ago", "due"),
    [
        (1, 0.9, False),
        (1, 1, True),
        (2, 5.9, False),
        (2, 6, True),
        (3, 23, False),
        (3, 24, True),
        (4, 1000, False),  # the cap: three retries after the first attempt
    ],
)
def test_backoff_and_cap(attempts: int, hours_ago: float, due: bool) -> None:
    article = _article(attempts=attempts, last_hours_ago=hours_ago, fetched_hours_ago=60)
    assert should_retry(article, NOW, RetryPolicy(window_days=30)) is due


def test_window_after_fetched_at() -> None:
    old = _article(fetched_hours_ago=73, last_hours_ago=10)
    fresh = _article(fetched_hours_ago=71, last_hours_ago=10)
    assert not should_retry(old, NOW, POLICY)
    assert should_retry(fresh, NOW, POLICY)


def test_article_without_a_recorded_attempt_counts_as_tried_once_at_fetch_time() -> None:
    legacy = _article(attempts=0, last_hours_ago=None, fetched_hours_ago=2)
    assert should_retry(legacy, NOW, POLICY)
    assert not should_retry(
        _article(attempts=0, last_hours_ago=None, fetched_hours_ago=0.5), NOW, POLICY
    )


def test_empty_schedule_disables_retries() -> None:
    assert not should_retry(_article(), NOW, RetryPolicy(backoff_hours=()))
