"""Firestore implementation of the ``Repository`` port, for production.

Each model is one document, in one collection per model: ``articles`` (keyed by
``Article.id``), ``source_state`` (``source_id``), ``feedback`` (``id``) and
``run_stats`` (``run_id``). An optional ``prefix`` is put in front of every
collection name, so tests and environments can share a database without seeing
each other's data (``prefix="test-ab12-"`` gives ``test-ab12-articles``).

Documents hold ``model_dump(mode="json")`` and are validated on read, so they
match what SQLite stores. The one difference is the three timestamps that are
queried or ordered on (``Article.fetched_at``, ``Feedback.created_at``,
``RunStats.started_at``): they are stored as native Firestore timestamps, whose
order is time order. Pydantic's JSON text drops zero microseconds, so as text it
would not sort by instant. Other datetimes stay ISO 8601 text.

Indexes
-------
Firestore serves a query ordered by one field from its automatic single-field
indexes, but an ordering that mixes directions, or an equality filter combined
with an ordering, needs a composite index. The adapter orders ties by the
stored ``id``/``run_id`` field, so it needs these six composite indexes
(collection scope; Native mode):

* ``articles``: ``fetched_at`` DESC, ``id`` ASC
* ``articles``: ``extraction.status`` ASC, ``fetched_at`` DESC, ``id`` ASC
* ``articles_by_source_and_fetched``: ``articles``: ``source_id`` ASC,
  ``fetched_at`` DESC, ``id`` ASC (``source_ids``, with or without ``since``)
* ``articles_by_status_source_and_fetched``: ``articles``:
  ``extraction.status`` ASC, ``source_id`` ASC, ``fetched_at`` DESC, ``id`` ASC
  (``status`` with ``source_ids``)
* ``feedback``: ``created_at`` ASC, ``id`` ASC
* ``run_stats``: ``started_at`` DESC, ``run_id`` ASC

Ties are ordered by the database, not sorted in the adapter, so ``limit`` is
exact at a tie boundary. The Firestore emulator does not enforce indexes, so
the emulator tests can't confirm this list. ``infra/terraform/firestore.tf``
deploys the indexes; keep it in step with this list.

Concurrency: the ``google.cloud.firestore.Client`` is safe to share between
threads, and every port method is a single request, so no locking is needed.

Running the contract tests against the emulator (needs Docker)::

    docker run -d --name firestore-emulator -p 8080:8080 \\
        gcr.io/google.com/cloudsdktool/google-cloud-cli:emulators \\
        gcloud emulators firestore start --host-port=0.0.0.0:8080
    cd backend
    FIRESTORE_EMULATOR_HOST=localhost:8080 uv run pytest -m emulator \\
        --allow-hosts=localhost,127.0.0.1
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from google.api_core.exceptions import NotFound
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.query import Query

from newsfeed.models import (
    Article,
    ExtractionStatus,
    Feedback,
    RunStats,
    SourceState,
)

if TYPE_CHECKING:
    from google.cloud import firestore

    from newsfeed.ports import Repository

__all__ = ["FirestoreRepository", "merge_newest_first"]

# Firestore's ``in`` filter accepts at most this many values.
_IN_LIMIT = 30


def merge_newest_first(groups: Iterable[Sequence[Article]], limit: int | None) -> list[Article]:
    """Merge per-group results into one list, newest ``fetched_at`` first, ties by id.

    Each group is the newest ``limit`` of its own sources, so every row of the
    overall newest ``limit`` is present and cutting the merge to ``limit`` is exact.
    """
    merged = sorted((a for group in groups for a in group), key=lambda a: a.id)
    merged.sort(key=lambda a: a.fetched_at, reverse=True)
    return merged if limit is None else merged[:limit]


def _require_aware(value: datetime | None) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("datetime must be timezone-aware")


def _require_limit(limit: int | None) -> None:
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")


def _doc_id(key: str) -> str:
    """Return ``key`` as a valid document id (no ``/``) without changing plain ids."""
    return quote(key, safe="")


def _with_time(data: dict[str, Any], field: str, value: datetime) -> dict[str, Any]:
    """Replace a JSON-mode timestamp with a native one, normalised to UTC."""
    data[field] = value.astimezone(UTC)
    return data


class FirestoreRepository:
    """A ``Repository`` stored in Firestore (Native mode).

    Args:
        client: a ``google.cloud.firestore.Client``; the caller owns credentials
            and, for the emulator, ``FIRESTORE_EMULATOR_HOST``.
        prefix: optional text put before each collection name.
    """

    def __init__(self, client: firestore.Client, *, prefix: str = "") -> None:
        self._client = client
        self._articles = client.collection(f"{prefix}articles")
        self._source_state = client.collection(f"{prefix}source_state")
        self._feedback = client.collection(f"{prefix}feedback")
        self._run_stats = client.collection(f"{prefix}run_stats")

    # --- Articles ------------------------------------------------------------

    def upsert_article(self, article: Article) -> None:
        """Insert ``article``, or replace the stored article with the same id."""
        data = _with_time(article.model_dump(mode="json"), "fetched_at", article.fetched_at)
        self._articles.document(_doc_id(article.id)).set(data)

    def get_article(self, article_id: str) -> Article | None:
        """Return the article with ``article_id``, or None."""
        snapshot = self._articles.document(_doc_id(article_id)).get()
        return Article.model_validate(snapshot.to_dict()) if snapshot.exists else None

    def list_articles(
        self,
        *,
        status: ExtractionStatus | None = None,
        since: datetime | None = None,
        limit: int | None = None,
        source_ids: Collection[str] | None = None,
    ) -> list[Article]:
        """Return matching articles, newest ``fetched_at`` first, ties by ascending id.

        More than 30 ``source_ids`` (Firestore's ``in`` limit) run as one query per
        group of 30, merged exactly by ``merge_newest_first``.
        """
        _require_aware(since)
        _require_limit(limit)
        if isinstance(source_ids, str):
            raise TypeError("source_ids must be a collection of ids, not a str")
        if limit == 0 or (source_ids is not None and not source_ids):
            return []
        if source_ids is None:
            return self._query_articles(status, since, limit, None)
        distinct = sorted(set(source_ids))
        groups = [
            self._query_articles(status, since, limit, distinct[i : i + _IN_LIMIT])
            for i in range(0, len(distinct), _IN_LIMIT)
        ]
        return merge_newest_first(groups, limit)

    def _query_articles(
        self,
        status: ExtractionStatus | None,
        since: datetime | None,
        limit: int | None,
        source_ids: list[str] | None,
    ) -> list[Article]:
        query: Any = self._articles
        if status is not None:
            query = query.where(
                filter=FieldFilter("extraction.status", "==", ExtractionStatus(status).value)
            )
        if source_ids is not None:
            query = query.where(filter=FieldFilter("source_id", "in", source_ids))
        if since is not None:
            query = query.where(filter=FieldFilter("fetched_at", ">=", since.astimezone(UTC)))
        query = query.order_by("fetched_at", direction=Query.DESCENDING).order_by(
            "id", direction=Query.ASCENDING
        )
        if limit is not None:
            query = query.limit(limit)
        return [Article.model_validate(doc.to_dict()) for doc in query.stream()]

    def delete_article(self, article_id: str) -> bool:
        """Delete the article with ``article_id``; True if it existed."""
        reference = self._articles.document(_doc_id(article_id))
        try:
            # The precondition makes "did it exist" and "delete it" one atomic step.
            reference.delete(option=self._client.write_option(exists=True))
        except NotFound:
            return False
        return True

    # --- Source state --------------------------------------------------------

    def get_source_state(self, source_id: str) -> SourceState | None:
        """Return the saved state for ``source_id``, or None."""
        snapshot = self._source_state.document(_doc_id(source_id)).get()
        return SourceState.model_validate(snapshot.to_dict()) if snapshot.exists else None

    def save_source_state(self, state: SourceState) -> None:
        """Insert or replace the state for ``state.source_id``."""
        self._source_state.document(_doc_id(state.source_id)).set(state.model_dump(mode="json"))

    # --- Feedback ------------------------------------------------------------

    def save_feedback(self, feedback: Feedback) -> None:
        """Insert or replace a feedback event by id."""
        data = _with_time(feedback.model_dump(mode="json"), "created_at", feedback.created_at)
        self._feedback.document(_doc_id(feedback.id)).set(data)

    def list_feedback(self, since: datetime | None = None) -> list[Feedback]:
        """Return feedback created at or after ``since``, oldest first, ties by id."""
        _require_aware(since)
        query: Any = self._feedback
        if since is not None:
            query = query.where(filter=FieldFilter("created_at", ">=", since.astimezone(UTC)))
        query = query.order_by("created_at", direction=Query.ASCENDING).order_by(
            "id", direction=Query.ASCENDING
        )
        return [Feedback.model_validate(doc.to_dict()) for doc in query.stream()]

    # --- Run stats -----------------------------------------------------------

    def save_run_stats(self, stats: RunStats) -> None:
        """Insert or replace run statistics by run id."""
        data = _with_time(stats.model_dump(mode="json"), "started_at", stats.started_at)
        self._run_stats.document(_doc_id(stats.run_id)).set(data)

    def get_run_stats(self, run_id: str) -> RunStats | None:
        """Return the statistics for ``run_id``, or None."""
        snapshot = self._run_stats.document(_doc_id(run_id)).get()
        return RunStats.model_validate(snapshot.to_dict()) if snapshot.exists else None

    def list_run_stats(
        self, *, since: datetime | None = None, limit: int | None = None
    ) -> list[RunStats]:
        """Return matching run statistics, newest ``started_at`` first, ties by run id."""
        _require_aware(since)
        _require_limit(limit)
        if limit == 0:
            return []
        query: Any = self._run_stats
        if since is not None:
            query = query.where(filter=FieldFilter("started_at", ">=", since.astimezone(UTC)))
        query = query.order_by("started_at", direction=Query.DESCENDING).order_by(
            "run_id", direction=Query.ASCENDING
        )
        if limit is not None:
            query = query.limit(limit)
        return [RunStats.model_validate(doc.to_dict()) for doc in query.stream()]


# --- Static conformance check ------------------------------------------------

if TYPE_CHECKING:

    def _check(client: firestore.Client) -> Repository:
        return FirestoreRepository(client)
