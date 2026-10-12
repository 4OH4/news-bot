"""SQLite implementation of the ``Repository`` port, for development and CI.

Uses only the stdlib ``sqlite3`` module. Top-level model fields map to columns
and nested fields (``Article.extraction``, ``score_breakdown``, the ``RunStats``
counters) are stored as JSON text, so the tables read naturally in the
``sqlite3`` shell. Because models use ``extra="forbid"``, only *adding* a nested
field with a default needs no migration (older rows simply lack it); removing
or renaming a nested field, or any top-level change, needs one.

Concurrency: the database runs in WAL mode, so readers don't block the writer.
Each thread gets its own connection (``sqlite3`` connections must not be shared
between threads without locking), every statement runs in autocommit mode, and
a busy timeout makes concurrent writers wait for the write lock instead of
failing. Each port method is a single SQL statement, so no explicit
transactions are needed outside schema migration.

Timestamps are stored as fixed-width ISO 8601 UTC text with microseconds
(``2026-10-11T12:00:00.000000Z``), so string order is time order in ``ORDER BY``
and range filters. Pydantic's own JSON form drops zero microseconds, which would
make ``12:00:00Z`` sort after ``12:00:00.5Z``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, Self

from newsfeed.models import (
    Article,
    ExtractionStatus,
    Feedback,
    RunStats,
    SourceState,
)

if TYPE_CHECKING:
    from newsfeed.ports import Repository

__all__ = ["SCHEMA_VERSION", "SchemaVersionError", "SqliteRepository"]


class SchemaVersionError(RuntimeError):
    """Raised when the database was written by a newer schema than this code knows."""


# Each entry migrates the schema from version ``i`` to ``i + 1``. Append new
# migrations; never edit one that has shipped, because existing databases have
# already applied it.
_MIGRATIONS: tuple[tuple[str, ...], ...] = (
    (
        """
        CREATE TABLE articles (
            id TEXT PRIMARY KEY,
            canonical_url TEXT NOT NULL,
            url TEXT NOT NULL,
            source_id TEXT NOT NULL,
            title TEXT NOT NULL,
            author TEXT,
            published_at TEXT,
            fetched_at TEXT NOT NULL,
            summary TEXT,
            lead_image TEXT,
            extraction TEXT NOT NULL,
            content_ref TEXT,
            score REAL,
            score_breakdown TEXT NOT NULL,
            cluster_id TEXT
        )
        """,
        "CREATE INDEX articles_fetched_at ON articles (fetched_at DESC, id)",
        """
        CREATE INDEX articles_status_fetched_at
            ON articles (json_extract(extraction, '$.status'), fetched_at DESC, id)
        """,
        """
        CREATE TABLE source_state (
            source_id TEXT PRIMARY KEY,
            etag TEXT,
            last_modified TEXT,
            last_fetched_at TEXT,
            last_status INTEGER
        )
        """,
        """
        CREATE TABLE feedback (
            id TEXT PRIMARY KEY,
            article_id TEXT NOT NULL,
            event TEXT NOT NULL,
            value REAL,
            created_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX feedback_created_at ON feedback (created_at, id)",
        """
        CREATE TABLE run_stats (
            run_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            stage_counts TEXT NOT NULL,
            extraction_by_domain TEXT NOT NULL,
            error_count INTEGER NOT NULL
        )
        """,
    ),
)

SCHEMA_VERSION = len(_MIGRATIONS)
"""The schema version this code creates and expects."""

_ARTICLE_COLUMNS = (
    "id",
    "canonical_url",
    "url",
    "source_id",
    "title",
    "author",
    "published_at",
    "fetched_at",
    "summary",
    "lead_image",
    "extraction",
    "content_ref",
    "score",
    "score_breakdown",
    "cluster_id",
)
_ARTICLE_JSON = frozenset({"extraction", "score_breakdown"})

_SOURCE_STATE_COLUMNS = ("source_id", "etag", "last_modified", "last_fetched_at", "last_status")
_FEEDBACK_COLUMNS = ("id", "article_id", "event", "value", "created_at")
_RUN_STATS_COLUMNS = (
    "run_id",
    "started_at",
    "finished_at",
    "stage_counts",
    "extraction_by_domain",
    "error_count",
)
_RUN_STATS_JSON = frozenset({"stage_counts", "extraction_by_domain"})


def _format_time(value: datetime | None) -> str | None:
    """Return ``value`` as fixed-width, sortable ISO 8601 UTC text."""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    naive_utc = value.astimezone(UTC).replace(tzinfo=None)
    return naive_utc.isoformat(timespec="microseconds") + "Z"


def _require_limit(limit: int | None) -> None:
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")


def _upsert_sql(table: str, columns: tuple[str, ...]) -> str:
    """Build an insert-or-replace statement keyed on the first column."""
    names = ", ".join(columns)
    placeholders = ", ".join(f":{c}" for c in columns)
    updates = ", ".join(f"{c} = excluded.{c}" for c in columns[1:])
    return (
        f"INSERT INTO {table} ({names}) VALUES ({placeholders}) "
        f"ON CONFLICT ({columns[0]}) DO UPDATE SET {updates}"
    )


def _to_row(data: dict[str, Any], *, json_fields: frozenset[str]) -> dict[str, Any]:
    """Turn a ``model_dump(mode='json')`` dict into column values."""
    row = dict(data)
    for name in json_fields:
        row[name] = json.dumps(row[name], sort_keys=True)
    return row


def _from_row(row: sqlite3.Row, *, json_fields: frozenset[str]) -> dict[str, Any]:
    """Turn a row back into a dict ready for ``Model.model_validate``."""
    data = dict(row)
    for name in json_fields:
        data[name] = json.loads(data[name])
    return data


class SqliteRepository:
    """A ``Repository`` stored in one SQLite database file.

    Opening creates the database and its parent directory if needed, applies
    any pending schema migrations and switches the database to WAL mode. Call
    ``close`` (or use the instance as a context manager) to release the
    connections; an instance can't be used after it is closed.

    Args:
        path: the database file, e.g. ``<data dir>/newsfeed.db``.
        timeout: seconds a statement waits for another writer's lock before
            raising ``sqlite3.OperationalError``.
    """

    def __init__(self, path: Path | str, *, timeout: float = 30.0) -> None:
        self.path = Path(path)
        self._timeout = timeout
        self._local = threading.local()
        self._lock = threading.Lock()
        self._connections: list[sqlite3.Connection] = []
        self._closed = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connection()
        self._enable_wal(connection)
        self._migrate(connection)

    # --- Lifecycle -----------------------------------------------------------

    def close(self) -> None:
        """Close every connection this instance opened. Safe to call twice."""
        with self._lock:
            self._closed = True
            connections, self._connections = self._connections, []
        for connection in connections:
            connection.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def schema_version(self) -> int:
        """Return the schema version recorded in the database."""
        row = self._connection().execute("SELECT version FROM schema_version").fetchone()
        return int(row["version"])

    def _connection(self) -> sqlite3.Connection:
        """Return this thread's connection, opening it on first use."""
        connection: sqlite3.Connection | None = getattr(self._local, "connection", None)
        if connection is not None and not self._closed:
            return connection
        with self._lock:
            if self._closed:
                raise sqlite3.ProgrammingError("SqliteRepository is closed")
            # isolation_level=None is autocommit: each statement commits on its own.
            # check_same_thread=False only so that close() can close connections
            # opened by other threads; each connection is still used by one thread.
            connection = sqlite3.connect(
                self.path, timeout=self._timeout, isolation_level=None, check_same_thread=False
            )
            self._connections.append(connection)
        connection.row_factory = sqlite3.Row
        # NORMAL is durable across application crashes in WAL mode; only a power
        # loss can lose the last few commits, which a re-run of the pipeline redoes.
        connection.execute("PRAGMA synchronous = NORMAL")
        self._local.connection = connection
        return connection

    def _enable_wal(self, connection: sqlite3.Connection) -> None:
        """Switch the database to WAL mode, which then persists in the file.

        Changing the journal mode needs an exclusive lock, and SQLite reports
        "database is locked" at once instead of using the busy timeout when
        another connection holds a lock, e.g. when several processes or
        threads open a new database together. So retry until ``timeout``.
        """
        deadline = time.monotonic() + self._timeout
        delay = 0.005
        while True:
            try:
                row = connection.execute("PRAGMA journal_mode = WAL").fetchone()
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc) or time.monotonic() >= deadline:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 0.1)
                continue
            if str(row[0]).lower() != "wal":
                raise sqlite3.OperationalError(f"could not enable WAL mode on {self.path}")
            return

    def _migrate(self, connection: sqlite3.Connection) -> None:
        """Create the schema, or bring an older one up to ``SCHEMA_VERSION``.

        Runs in one ``BEGIN IMMEDIATE`` transaction, so two processes opening a
        new database at once don't both try to create it.
        """
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER)")
            row = connection.execute("SELECT version FROM schema_version").fetchone()
            current = 0 if row is None else int(row["version"])
            if current > SCHEMA_VERSION:
                raise SchemaVersionError(
                    f"{self.path} has schema version {current}, but this code supports "
                    f"up to {SCHEMA_VERSION}; upgrade newsfeed to open it"
                )
            for statements in _MIGRATIONS[current:]:
                for statement in statements:
                    connection.execute(statement)
            if row is None:
                connection.execute(
                    "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
                )
            elif current != SCHEMA_VERSION:
                connection.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")

    # --- Articles ------------------------------------------------------------

    def upsert_article(self, article: Article) -> None:
        """Insert ``article``, or replace the stored article with the same id."""
        data = article.model_dump(mode="json")
        row = _to_row(data, json_fields=_ARTICLE_JSON)
        row["published_at"] = _format_time(article.published_at)
        row["fetched_at"] = _format_time(article.fetched_at)
        self._connection().execute(_upsert_sql("articles", _ARTICLE_COLUMNS), row)

    def get_article(self, article_id: str) -> Article | None:
        """Return the article with ``article_id``, or None."""
        row = (
            self._connection()
            .execute("SELECT * FROM articles WHERE id = ?", (article_id,))
            .fetchone()
        )
        return None if row is None else self._article(row)

    def list_articles(
        self,
        *,
        status: ExtractionStatus | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[Article]:
        """Return matching articles, newest ``fetched_at`` first, ties by ascending id."""
        since_text = _format_time(since)
        _require_limit(limit)
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("json_extract(extraction, '$.status') = ?")
            params.append(ExtractionStatus(status).value)
        if since_text is not None:
            clauses.append("fetched_at >= ?")
            params.append(since_text)
        sql = "SELECT * FROM articles"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY fetched_at DESC, id ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self._connection().execute(sql, params).fetchall()
        return [self._article(row) for row in rows]

    def delete_article(self, article_id: str) -> bool:
        """Delete the article with ``article_id``; True if it existed."""
        cursor = self._connection().execute("DELETE FROM articles WHERE id = ?", (article_id,))
        return cursor.rowcount > 0

    @staticmethod
    def _article(row: sqlite3.Row) -> Article:
        return Article.model_validate(_from_row(row, json_fields=_ARTICLE_JSON))

    # --- Source state --------------------------------------------------------

    def get_source_state(self, source_id: str) -> SourceState | None:
        """Return the saved state for ``source_id``, or None."""
        row = (
            self._connection()
            .execute("SELECT * FROM source_state WHERE source_id = ?", (source_id,))
            .fetchone()
        )
        return None if row is None else SourceState.model_validate(dict(row))

    def save_source_state(self, state: SourceState) -> None:
        """Insert or replace the state for ``state.source_id``."""
        row = state.model_dump(mode="json")
        row["last_fetched_at"] = _format_time(state.last_fetched_at)
        self._connection().execute(_upsert_sql("source_state", _SOURCE_STATE_COLUMNS), row)

    # --- Feedback ------------------------------------------------------------

    def save_feedback(self, feedback: Feedback) -> None:
        """Insert or replace a feedback event by id."""
        row = feedback.model_dump(mode="json")
        row["created_at"] = _format_time(feedback.created_at)
        self._connection().execute(_upsert_sql("feedback", _FEEDBACK_COLUMNS), row)

    def list_feedback(self, since: datetime | None = None) -> list[Feedback]:
        """Return feedback created at or after ``since``, oldest first, ties by id."""
        since_text = _format_time(since)
        sql = "SELECT * FROM feedback"
        params: list[Any] = []
        if since_text is not None:
            sql += " WHERE created_at >= ?"
            params.append(since_text)
        sql += " ORDER BY created_at ASC, id ASC"
        rows = self._connection().execute(sql, params).fetchall()
        return [Feedback.model_validate(dict(row)) for row in rows]

    # --- Run stats -----------------------------------------------------------

    def save_run_stats(self, stats: RunStats) -> None:
        """Insert or replace run statistics by run id."""
        data = stats.model_dump(mode="json")
        row = _to_row(data, json_fields=_RUN_STATS_JSON)
        row["started_at"] = _format_time(stats.started_at)
        row["finished_at"] = _format_time(stats.finished_at)
        self._connection().execute(_upsert_sql("run_stats", _RUN_STATS_COLUMNS), row)

    def get_run_stats(self, run_id: str) -> RunStats | None:
        """Return the statistics for ``run_id``, or None."""
        row = (
            self._connection()
            .execute("SELECT * FROM run_stats WHERE run_id = ?", (run_id,))
            .fetchone()
        )
        if row is None:
            return None
        return RunStats.model_validate(_from_row(row, json_fields=_RUN_STATS_JSON))


# --- Static conformance check ------------------------------------------------

if TYPE_CHECKING:
    _repository: Repository = SqliteRepository("newsfeed.db")
