"""Firestore-adapter behaviour that needs no emulator.

The shared contract tests (``tests/contract``) cover the ``Repository``
semantics against the emulator. These check argument validation, which must
fail before any request is made, using a client that records every call.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from newsfeed.adapters import firestore_repository
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


def _article(n: int, minutes: int) -> Article:
    url = f"https://example.com/story-{n}"
    return Article(
        canonical_url=url,
        url=url,
        source_id="s",
        title=f"Invented headline {n}",
        fetched_at=datetime(2026, 10, 11, 12, 0, tzinfo=UTC) + timedelta(minutes=minutes),
    )


def test_source_ids_string_and_empty_are_handled_before_any_query() -> None:
    repo, client = _repo()
    with pytest.raises(TypeError, match="source_ids"):
        repo.list_articles(source_ids="a")  # type: ignore[arg-type]
    assert repo.list_articles(source_ids=[]) == []
    client.collection.return_value.where.assert_not_called()
    client.collection.return_value.order_by.assert_not_called()


def test_merge_newest_first_across_groups_cuts_to_limit_with_id_tiebreak() -> None:
    merge = firestore_repository.merge_newest_first
    group_1 = [_article(1, 5), _article(2, 3), _article(3, 1)]
    group_2 = [_article(4, 9), _article(5, 5), _article(6, 0)]
    merged = merge([group_1, group_2], limit=3)
    tied = sorted([group_1[0], group_2[1]], key=lambda a: a.id)
    assert [a.id for a in merged] == [group_2[0].id, *(a.id for a in tied)]
    assert len(merge([group_1, group_2], limit=None)) == 6
    assert merge([group_1, group_2], limit=0) == []


def test_firestore_indexes_for_source_filter_are_in_terraform_and_docstring() -> None:
    tf = Path(__file__).parents[3] / "infra" / "terraform" / "firestore.tf"
    for name in ("articles_by_source_and_fetched", "articles_by_status_source_and_fetched"):
        assert name in tf.read_text()
        assert name in (firestore_repository.__doc__ or "")
