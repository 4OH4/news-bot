"""SQLite-specific behaviour of ``SqliteRepository``.

The port semantics are covered by ``tests/contract/test_repository_contract.py``;
these tests cover the schema, migrations, WAL mode, storage format and
connection lifecycle.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel

from newsfeed.adapters.sqlite_repository import (
    SCHEMA_VERSION,
    SchemaVersionError,
    SqliteRepository,
)
from newsfeed.models import (
    Article,
    ArticleExtraction,
    ExtractionStatus,
    Feedback,
    RunStats,
    SourceState,
)

T0 = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def make_article(n: int, fetched_at: datetime = T0) -> Article:
    url = f"https://example.com/story-{n}"
    return Article(
        canonical_url=url,
        url=url,
        source_id="example",
        title=f"Invented headline {n}",
        fetched_at=fetched_at,
        extraction=ArticleExtraction(status=ExtractionStatus.OK, method="trafilatura"),
        score_breakdown={"topic": 0.5},
    )


def raw(path: Path, sql: str) -> list[tuple[object, ...]]:
    """Run ``sql`` on a separate plain connection and return the rows."""
    connection = sqlite3.connect(path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "newsfeed.db"


@pytest.mark.parametrize(
    ("model", "table"),
    [
        (Article, "articles"),
        (SourceState, "source_state"),
        (Feedback, "feedback"),
        (RunStats, "run_stats"),
    ],
)
def test_every_model_field_has_a_column(db_path: Path, model: type[BaseModel], table: str) -> None:
    # A field added to models.py without a migration would otherwise be
    # silently dropped on write, because unused named parameters are ignored.
    with SqliteRepository(db_path):
        pass
    columns = {str(row[1]) for row in raw(db_path, f"PRAGMA table_info({table})")}
    assert columns == set(model.model_fields)


def test_creates_parent_directory_and_schema(db_path: Path) -> None:
    with SqliteRepository(db_path) as repo:
        assert repo.schema_version() == SCHEMA_VERSION
    assert db_path.is_file()
    tables = {row[0] for row in raw(db_path, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"schema_version", "articles", "source_state", "feedback", "run_stats"} <= tables
    assert raw(db_path, "SELECT version FROM schema_version") == [(SCHEMA_VERSION,)]


def test_uses_wal_mode(db_path: Path) -> None:
    with SqliteRepository(db_path):
        assert raw(db_path, "PRAGMA journal_mode") == [("wal",)]


def test_reopening_keeps_schema_and_data(db_path: Path) -> None:
    article = make_article(1)
    with SqliteRepository(db_path) as repo:
        repo.upsert_article(article)
    with SqliteRepository(db_path) as repo:
        assert repo.schema_version() == SCHEMA_VERSION
        assert repo.get_article(article.id) == article
    assert raw(db_path, "SELECT COUNT(*) FROM schema_version") == [(1,)]


def test_migrates_a_database_at_version_zero(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True)
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE schema_version (version INTEGER)")
    connection.execute("INSERT INTO schema_version (version) VALUES (0)")
    connection.commit()
    connection.close()
    with SqliteRepository(db_path) as repo:
        assert repo.schema_version() == SCHEMA_VERSION
        repo.upsert_article(make_article(1))
    assert raw(db_path, "SELECT version FROM schema_version") == [(SCHEMA_VERSION,)]


def test_refuses_a_newer_schema(db_path: Path) -> None:
    with SqliteRepository(db_path):
        pass
    connection = sqlite3.connect(db_path)
    connection.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 1,))
    connection.commit()
    connection.close()
    with pytest.raises(SchemaVersionError, match="upgrade newsfeed"):
        SqliteRepository(db_path)


@pytest.mark.parametrize("attempt", range(10))
def test_concurrent_first_open_creates_the_schema_once(db_path: Path, attempt: int) -> None:
    # Regression test: switching a new database to WAL used to fail with
    # "database is locked" when several connections opened it at once. The race
    # is timing-dependent, so it is tried on several fresh databases.
    repos: list[SqliteRepository] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def open_repo() -> None:
        try:
            barrier.wait()
            repo = SqliteRepository(db_path)
            with lock:
                repos.append(repo)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=open_repo) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for repo in repos:
        repo.close()
    assert errors == []
    assert raw(db_path, "SELECT version FROM schema_version") == [(SCHEMA_VERSION,)]


def test_timestamps_are_stored_as_fixed_width_utc(db_path: Path) -> None:
    with SqliteRepository(db_path) as repo:
        repo.upsert_article(make_article(1, fetched_at=T0))
        repo.upsert_article(make_article(2, fetched_at=T0 + timedelta(microseconds=5)))
        repo.save_run_stats(RunStats(run_id="r", started_at=T0))
    stored = sorted(str(row[0]) for row in raw(db_path, "SELECT fetched_at FROM articles"))
    assert stored == ["2026-10-11T12:00:00.000000Z", "2026-10-11T12:00:00.000005Z"]
    assert raw(db_path, "SELECT started_at, finished_at FROM run_stats") == [
        ("2026-10-11T12:00:00.000000Z", None)
    ]


def test_nested_fields_are_json_columns(db_path: Path) -> None:
    with SqliteRepository(db_path) as repo:
        repo.upsert_article(make_article(1))
    rows = raw(
        db_path,
        "SELECT json_extract(extraction, '$.status'), json_extract(extraction, '$.method'),"
        " json_extract(score_breakdown, '$.topic') FROM articles",
    )
    assert rows == [("ok", "trafilatura", 0.5)]


def test_two_instances_share_one_database(db_path: Path) -> None:
    with SqliteRepository(db_path) as writer, SqliteRepository(db_path) as reader:
        article = make_article(1)
        writer.upsert_article(article)
        assert reader.get_article(article.id) == article


def test_threads_use_their_own_connections(db_path: Path) -> None:
    with SqliteRepository(db_path) as repo:
        main = repo._connection()
        seen: list[sqlite3.Connection] = []
        thread = threading.Thread(target=lambda: seen.append(repo._connection()))
        thread.start()
        thread.join()
        assert seen[0] is not main
        assert repo._connection() is main


def test_closed_repository_refuses_use(db_path: Path) -> None:
    repo = SqliteRepository(db_path)
    repo.close()
    repo.close()  # idempotent
    with pytest.raises(sqlite3.ProgrammingError):
        repo.get_article("0" * 16)


def test_waits_for_a_lock_while_enabling_wal(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True)
    blocker = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    blocker.execute("BEGIN EXCLUSIVE")
    timer = threading.Timer(0.2, lambda: blocker.execute("COMMIT"))
    timer.start()
    try:
        with SqliteRepository(db_path, timeout=10) as repo:
            assert repo.schema_version() == SCHEMA_VERSION
    finally:
        timer.join()
        blocker.close()


def test_gives_up_on_a_lock_after_the_timeout(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True)
    blocker = sqlite3.connect(db_path, isolation_level=None)
    blocker.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            SqliteRepository(db_path, timeout=0.1)
    finally:
        blocker.close()


def test_in_memory_databases_are_not_supported() -> None:
    # Each thread has its own connection, and every ":memory:" connection is a
    # separate, empty database, so the adapter needs a file.
    with pytest.raises(sqlite3.OperationalError, match="WAL"):
        SqliteRepository(":memory:")
