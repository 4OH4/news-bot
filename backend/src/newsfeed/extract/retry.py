"""Which failed extractions are worth another try, and when (FR-6, NFR-3).

An article whose extraction failed once because of a passing outage would
otherwise stay a hole in the feed for good. Retrying everything every run would
be impolite and wasteful, so the policy is deliberately narrow:

- **Only transient failures are retried:** timeouts (including HTTP 408),
  connection errors, HTTP 5xx and HTTP 429. Never retried: robots.txt refusals,
  other 4xx (401, 403, 404, 410...), extraction failures such as "no content",
  and ``paywalled`` or ``skipped`` articles (those aren't failures at all).
- **Partial extractions** (usually a short page) are retried only when
  ``retry_partial`` is on; the default is off. A retry that does worse (a
  failure, or a partial no longer than the stored one) keeps the stored text
  and status and only records the attempt and the latest error (also when the retry ends
  skipped or paywalled). ``extract --url --save`` of a stored partial article
  goes through the same path, so it applies this rule too.
- **Backoff and cap:** after attempt *n* the article waits ``backoff_hours[n-1]``
  before the next try, measured from ``extraction.last_attempt_at`` (or
  ``fetched_at`` for articles stored before that field existed). The defaults
  ``(1, 6, 24)`` allow three retries, then give up: the cap is the length of
  the schedule.
- **Window:** nothing is retried more than ``window_days`` after ``fetched_at``,
  since old articles drop out of the feed anyway.

``should_retry`` is pure; the extract stage calls it to pick articles.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from newsfeed.metrics import reason_category
from newsfeed.models import Article, ExtractionStatus

__all__ = ["RETRYABLE_REASONS", "RetryPolicy", "should_retry"]

RETRYABLE_REASONS: Final = frozenset({"timeout", "connection", "http_5xx", "http_429"})
"""``error_type`` categories (see ``newsfeed.metrics.REASONS``) that are transient."""


@dataclass(frozen=True)
class RetryPolicy:
    """How failed extractions are retried. An empty ``backoff_hours`` disables retries."""

    backoff_hours: tuple[float, ...] = (1, 6, 24)
    window_days: float = 3
    retry_partial: bool = False


def should_retry(article: Article, now: datetime, policy: RetryPolicy) -> bool:
    """Whether ``article`` is due another extraction attempt at ``now``."""
    extraction = article.extraction
    if extraction.status is ExtractionStatus.FAILED:
        reason = extraction.error_type or reason_category(extraction.error)
        if reason not in RETRYABLE_REASONS:
            return False
    elif extraction.status is not ExtractionStatus.PARTIAL or not policy.retry_partial:
        return False
    if now - article.fetched_at > timedelta(days=policy.window_days):
        return False
    # An article with a status but no recorded attempt predates the field: it was tried once.
    attempts = max(extraction.attempts, 1)
    if attempts > len(policy.backoff_hours):
        return False
    last = extraction.last_attempt_at or article.fetched_at
    return now - last >= timedelta(hours=policy.backoff_hours[attempts - 1])
