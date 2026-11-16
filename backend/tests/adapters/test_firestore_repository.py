"""Firestore-adapter behaviour that needs no emulator.

The shared contract tests (``tests/contract``) cover the ``Repository``
semantics against the emulator. These check argument validation, which must
fail before any request is made, using a client that records every call.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from newsfeed.adapters import firestore_repository
from newsfeed.adapters.firestore_repository import FirestoreRepository
from newsfeed.models import Article, Feedback, FeedbackEvent, Profile, RunStats


def _repo() -> tuple[FirestoreRepository, MagicMock]:
    client = MagicMock()
    return FirestoreRepository(client, prefix="p-"), client


def test_collections_use_the_prefix() -> None:
    _, client = _repo()
    names = [call.args[0] for call in client.collection.call_args_list]
    # Every collection is prefixed; ``p-profiles`` (#320) may be opened here or
    # on first use, so it is checked by the profile tests below.
    assert {"p-articles", "p-source_state", "p-feedback", "p-run_stats"} <= set(names)
    assert all(name.startswith("p-") for name in names)


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


# --- Personalisation fields (#320) ---------------------------------------------
# Vectors are packed float32 bytes, because Firestore indexes every element of an
# array separately (384 index entries per article).


def _serve_last_write(client: MagicMock) -> dict[str, Any]:
    """Make the next document read return the last document written."""
    document = client.collection.return_value.document.return_value
    written: dict[str, Any] = document.set.call_args.args[0]
    snapshot = document.get.return_value
    snapshot.exists = True
    snapshot.to_dict.return_value = written
    return written


def _vector(length: int, seed: float) -> list[float]:
    return [math.sin(seed + 0.37 * i) for i in range(length)]


def test_embedding_is_stored_as_float32_bytes_and_reads_back() -> None:
    repo, client = _repo()
    values = _vector(384, 0.0)
    article = Article.model_validate(
        {
            **_article(1, 0).model_dump(),
            "embedding": values,
            "embedding_model": "BAAI/bge-small-en-v1.5",
        }
    )
    repo.upsert_article(article)
    written = _serve_last_write(client)
    assert isinstance(written["embedding"], bytes)
    assert len(written["embedding"]) == 4 * 384
    assert written["embedding_model"] == "BAAI/bge-small-en-v1.5"

    stored = repo.get_article(article.id)
    assert stored is not None
    assert stored.embedding == pytest.approx(values, abs=1e-6)
    assert stored.embedding_model == "BAAI/bge-small-en-v1.5"


def test_profile_vectors_are_bytes_in_the_profiles_collection() -> None:
    repo, client = _repo()
    profile = Profile.model_validate(
        {
            "embedding_model": "BAAI/bge-small-en-v1.5",
            "centroids": [
                {"vector": _vector(384, 1.0), "weight": 2.0, "size": 5},
                {"vector": _vector(384, 2.0), "weight": 1.0, "size": 2},
            ],
            "negative": _vector(384, 3.0),
            "events_used": 7,
        }
    )
    repo.save_profile(profile)
    assert "p-profiles" in [call.args[0] for call in client.collection.call_args_list]
    client.collection.return_value.document.assert_any_call("owner")
    written = _serve_last_write(client)
    assert all(isinstance(c["vector"], bytes) for c in written["centroids"])
    assert isinstance(written["negative"], bytes)
    assert len(written["negative"]) == 4 * 384

    stored = repo.get_profile("owner")
    assert stored is not None
    assert stored.events_used == 7
    assert stored.negative == pytest.approx(profile.negative, abs=1e-6)
    for got, want in zip(stored.centroids, profile.centroids, strict=True):
        assert got.vector == pytest.approx(want.vector, abs=1e-6)
        assert (got.weight, got.size) == (want.weight, want.size)


def test_unknown_profile_is_none() -> None:
    repo, client = _repo()
    client.collection.return_value.document.return_value.get.return_value.exists = False
    assert repo.get_profile("owner") is None


def test_feedback_document_from_before_ab_fields_loads_with_defaults() -> None:
    repo, client = _repo()
    query = client.collection.return_value
    query.where.return_value = query
    query.order_by.return_value = query
    legacy = MagicMock()
    legacy.to_dict.return_value = {
        "id": "f1",
        "article_id": "a" * 16,
        "event": "open",
        "value": None,
        "created_at": datetime(2026, 10, 11, 12, 0, tzinfo=UTC),
    }
    query.stream.return_value = [legacy]
    (feedback,) = repo.list_feedback()
    assert (feedback.user_id, feedback.view, feedback.arm) == ("owner", None, None)
