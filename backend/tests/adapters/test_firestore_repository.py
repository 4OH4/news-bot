"""Firestore-adapter behaviour that needs no emulator.

The shared contract tests (``tests/contract``) cover the ``Repository``
semantics against the emulator. These check argument validation, which must
fail before any request is made, using a client that records every call.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from newsfeed.adapters.firestore_repository import FirestoreRepository
from newsfeed.models import Article, Feedback, FeedbackEvent, RunStats


def _repo() -> tuple[FirestoreRepository, MagicMock]:
    client = MagicMock()
    return FirestoreRepository(client, prefix="p-"), client


def test_collections_use_the_prefix() -> None:
    _, client = _repo()
    names = [call.args[0] for call in client.collection.call_args_list]
    assert names == ["p-articles", "p-source_state", "p-feedback", "p-run_stats"]


def test_naive_since_is_rejected_before_any_request() -> None:
    repo, client = _repo()
    naive = datetime(2026, 10, 11, 12, 0)
    for call in (
        lambda: repo.list_articles(since=naive),
        lambda: repo.list_feedback(naive),
        lambda: repo.list_run_stats(since=naive),
    ):
        with pytest.raises(ValueError, match="timezone-aware"):
            call()
    client.collection.return_value.where.assert_not_called()


def test_negative_limit_is_rejected_and_zero_returns_nothing() -> None:
    repo, client = _repo()
    with pytest.raises(ValueError, match="non-negative"):
        repo.list_articles(limit=-1)
    with pytest.raises(ValueError, match="non-negative"):
        repo.list_run_stats(limit=-1)
    assert repo.list_articles(limit=0) == []
    assert repo.list_run_stats(limit=0) == []
    client.collection.return_value.order_by.assert_not_called()


def test_stored_documents_validate_back_and_queried_times_are_native() -> None:
    """The document written for each model reads back equal, without a server."""
    repo, client = _repo()
    document = client.collection.return_value.document.return_value
    t = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    article = Article(
        canonical_url="https://example.com/a",
        url="https://example.com/a",
        source_id="s",
        title="T",
        fetched_at=t,
    )
    feedback = Feedback(article_id=article.id, event=FeedbackEvent.READ, created_at=t)
    stats = RunStats(run_id="r1", started_at=t)

    repo.upsert_article(article)
    repo.save_feedback(feedback)
    repo.save_run_stats(stats)
    written = [c.args[0] for c in document.set.call_args_list]
    assert [
        w[f] for w, f in zip(written, ["fetched_at", "created_at", "started_at"], strict=True)
    ] == [t] * 3
    assert Article.model_validate(written[0]) == article
    assert Feedback.model_validate(written[1]) == feedback
    assert RunStats.model_validate(written[2]) == stats


def test_aware_since_is_accepted() -> None:
    repo, _ = _repo()
    assert repo.list_feedback(datetime(2026, 10, 11, tzinfo=UTC)) == []
