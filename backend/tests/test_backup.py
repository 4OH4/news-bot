"""Backups of feedback to the blob store, and restoring them (#330).

Round trips use the real SQLite repository and local blob store; the CLI
commands run against a temporary data directory.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from newsfeed import cli
from newsfeed.adapters.localfs_blobstore import LocalBlobStore
from newsfeed.adapters.sqlite_repository import SqliteRepository
from newsfeed.backup import (
    BackupError,
    backup_due,
    backup_key,
    restore_backup,
    write_backup,
)
from newsfeed.models import Feedback, FeedbackEvent, RankingArm, ReaderPreferences
from tests.fakes import InMemoryBlobStore, InMemoryRepository

DAY = date(2026, 11, 16)
SECRET_ARTICLE_IDS = ("art-private-alpha", "art-private-beta", "art-private-gamma")
SECRET_VALUES = (417.25, 983.5)


def _events() -> list[Feedback]:
    """Three invented events, with every optional field set on at least one."""
    at = datetime(2026, 11, 15, 8, 30, tzinfo=UTC)
    return [
        Feedback(
            id="fb-1",
            article_id=SECRET_ARTICLE_IDS[0],
            event=FeedbackEvent.READ,
            value=SECRET_VALUES[0],
            created_at=at,
            view="feed",
            arm=RankingArm.PERSONAL,
        ),
        Feedback(
            id="fb-2", article_id=SECRET_ARTICLE_IDS[1], event=FeedbackEvent.OPEN, created_at=at
        ),
        Feedback(
            id="fb-3",
            article_id=SECRET_ARTICLE_IDS[2],
            event=FeedbackEvent.READ,
            value=SECRET_VALUES[1],
            created_at=at,
            arm=RankingArm.RULES,
        ),
    ]


def _dumped(repo: SqliteRepository) -> list[dict[str, object]]:
    return sorted((f.model_dump() for f in repo.list_feedback()), key=lambda d: str(d["id"]))


@pytest.fixture
def source(tmp_path: Path) -> Iterator[SqliteRepository]:
    with SqliteRepository(tmp_path / "source.db") as repo:
        for event in _events():
            repo.save_feedback(event)
        yield repo


@pytest.fixture
def empty(tmp_path: Path) -> Iterator[SqliteRepository]:
    with SqliteRepository(tmp_path / "empty.db") as repo:
        yield repo


@pytest.fixture
def blobs(tmp_path: Path) -> LocalBlobStore:
    return LocalBlobStore(tmp_path / "blobs")


def test_backup_writes_one_json_line_per_event_and_a_manifest_with_the_count(
    source: SqliteRepository, blobs: LocalBlobStore
) -> None:
    assert backup_due(blobs, DAY)

    manifest = write_backup(source, blobs, day=DAY, run_id="run-1")

    lines = blobs.get(blobs.ref_for(backup_key(DAY, "feedback.jsonl"))).decode().splitlines()
    assert sorted(json.loads(line)["id"] for line in lines) == ["fb-1", "fb-2", "fb-3"]
    stored = json.loads(blobs.get(blobs.ref_for(backup_key(DAY, "manifest.json"))))
    assert stored["day"] == "2026-11-16"
    assert stored["run_id"] == "run-1"
    assert stored["counts"] == {"feedback": 3, "preferences": 0}
    assert manifest.counts == {"feedback": 3, "preferences": 0}
    assert backup_key(DAY, "manifest.json") == "backups/2026-11-16/manifest.json"
    assert not backup_due(blobs, DAY)


def test_restore_round_trips_every_field_and_is_idempotent(
    source: SqliteRepository, empty: SqliteRepository, blobs: LocalBlobStore
) -> None:
    write_backup(source, blobs, day=DAY, run_id=None)

    assert restore_backup(empty, blobs, day=DAY, dry_run=False) == 3
    assert _dumped(empty) == _dumped(source)

    assert restore_backup(empty, blobs, day=DAY, dry_run=False) == 3
    assert _dumped(empty) == _dumped(source)


def test_dry_run_restore_counts_without_writing(
    source: SqliteRepository, empty: SqliteRepository, blobs: LocalBlobStore
) -> None:
    write_backup(source, blobs, day=DAY, run_id=None)

    assert restore_backup(empty, blobs, day=DAY, dry_run=True) == 3
    assert empty.list_feedback() == []


def test_backup_and_restore_logs_carry_no_article_id_or_feedback_value(
    source: SqliteRepository,
    empty: SqliteRepository,
    blobs: LocalBlobStore,
    log_records: list[logging.LogRecord],
) -> None:
    write_backup(source, blobs, day=DAY, run_id="run-1")
    restore_backup(empty, blobs, day=DAY, dry_run=False)

    assert log_records, "a backup should log its counts and keys"
    for record in log_records:
        text = record.getMessage() + " " + repr(vars(record))
        for secret in (*SECRET_ARTICLE_IDS, *(str(v) for v in SECRET_VALUES)):
            assert secret not in text, f"{secret!r} leaked into log record {record.msg!r}"


# --- Reader preferences (#334) ---------------------------------------------------

SECRET_SOURCE = "private-source-zeta"


def _prefs(hour: int, topic_state: str = "fewer") -> ReaderPreferences:
    return ReaderPreferences.model_validate(
        {
            "sources": {SECRET_SOURCE: "hide"},
            "topics": {"sport": topic_state},
            "updated_at": datetime(2026, 11, 16, hour, 0, tzinfo=UTC),
        }
    )


def test_backup_and_restore_carry_feedback_and_preferences(
    empty: SqliteRepository, blobs: LocalBlobStore, tmp_path: Path
) -> None:
    with SqliteRepository(tmp_path / "with-prefs.db") as source:
        for event in _events()[:2]:
            source.save_feedback(event)
        source.save_preferences(_prefs(9))

        manifest = write_backup(source, blobs, day=DAY, run_id=None)

        assert manifest.counts == {"feedback": 2, "preferences": 1}
        assert restore_backup(empty, blobs, day=DAY, dry_run=False) == 3
        assert _dumped(empty) == _dumped(source)
        assert empty.get_preferences("owner") == _prefs(9)


@pytest.mark.parametrize(
    ("stored_hour", "kept"),
    [(10, True), (9, True), (8, False)],
    ids=["stored-newer-is-kept", "same-time-is-kept", "stored-older-is-replaced"],
)
def test_restore_keeps_stored_preferences_unless_the_backup_is_newer(
    empty: SqliteRepository,
    blobs: LocalBlobStore,
    tmp_path: Path,
    log_records: list[logging.LogRecord],
    stored_hour: int,
    kept: bool,
) -> None:
    backed_up = _prefs(9, topic_state="more")
    with SqliteRepository(tmp_path / "backed-up.db") as source:
        source.save_preferences(backed_up)
        write_backup(source, blobs, day=DAY, run_id=None)
    stored = _prefs(stored_hour, topic_state="fewer")
    empty.save_preferences(stored)
    log_records.clear()

    restored = restore_backup(empty, blobs, day=DAY, dry_run=False)

    assert empty.get_preferences("owner") == (stored if kept else backed_up)
    assert restored == (0 if kept else 1)
    kept_logs = [
        r
        for r in log_records
        if "kept" in r.getMessage().lower() and getattr(r, "collection", None) == "preferences"
    ]
    if kept:
        assert len(kept_logs) == 1
        assert getattr(kept_logs[0], "user_id", None) == "owner"
        assert getattr(kept_logs[0], "stage", None) == "backup"
    else:
        assert kept_logs == []
    for record in log_records:
        text = record.getMessage() + " " + repr(vars(record))
        assert SECRET_SOURCE not in text, f"a setting leaked into log record {record.msg!r}"


def test_dry_run_counts_preferences_by_the_same_rule_and_writes_nothing(
    empty: SqliteRepository, blobs: LocalBlobStore, tmp_path: Path
) -> None:
    with SqliteRepository(tmp_path / "backed-up.db") as source:
        source.save_preferences(_prefs(9, topic_state="more"))
        write_backup(source, blobs, day=DAY, run_id=None)

    assert restore_backup(empty, blobs, day=DAY, dry_run=True) == 1
    assert empty.get_preferences("owner") is None

    empty.save_preferences(_prefs(10))
    assert restore_backup(empty, blobs, day=DAY, dry_run=True) == 0
    assert empty.get_preferences("owner") == _prefs(10)


# --- CLI -----------------------------------------------------------------------

runner = CliRunner()


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = tmp_path / "config"
    config.mkdir()
    (config / "sources.yaml").write_text("sources: []\n")
    (config / "url_params.yaml").write_text("domains: {}\n")
    (config / "extraction.yaml").write_text("domains: {}\n")
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("NEWSFEED_CONFIG_DIR", str(config))
    monkeypatch.setenv("NEWSFEED_DATA_DIR", str(data))
    monkeypatch.setenv("NEWSFEED_STORAGE", "local")
    return data


def test_cli_backup_then_restore_round_trips_through_the_data_dir(data_dir: Path) -> None:
    with SqliteRepository(data_dir / "newsfeed.db") as repo:
        for event in _events():
            repo.save_feedback(event)
        before = _dumped(repo)

    backed_up = runner.invoke(cli.app, ["backup", "--day", "2026-11-10"])
    assert backed_up.exit_code == 0, backed_up.output
    assert (data_dir / "blobs" / "backups" / "2026-11-10" / "manifest.json").is_file()

    (data_dir / "newsfeed.db").unlink()
    dry = runner.invoke(cli.app, ["restore-backup", "2026-11-10", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "3" in dry.output
    with SqliteRepository(data_dir / "newsfeed.db") as repo:
        assert repo.list_feedback() == []

    restored = runner.invoke(cli.app, ["restore-backup", "2026-11-10"])
    assert restored.exit_code == 0, restored.output
    assert "3" in restored.output
    for secret in SECRET_ARTICLE_IDS:
        assert secret not in restored.output + dry.output
    with SqliteRepository(data_dir / "newsfeed.db") as repo:
        assert _dumped(repo) == before


# --- Guards (worker's tests) ----------------------------------------------------


def test_restore_of_a_missing_or_incomplete_backup_raises(
    source: SqliteRepository, empty: SqliteRepository, blobs: LocalBlobStore
) -> None:
    with pytest.raises(BackupError, match="no complete backup"):
        restore_backup(empty, blobs, day=DAY, dry_run=False)

    # feedback.jsonl written but no manifest: an interrupted backup is not restorable.
    lines = b"".join(json.dumps(e.model_dump(mode="json")).encode() + b"\n" for e in _events())
    blobs.put(backup_key(DAY, "feedback.jsonl"), lines)
    with pytest.raises(BackupError, match="no complete backup"):
        restore_backup(empty, blobs, day=DAY, dry_run=False)
    assert empty.list_feedback() == []


def test_a_damaged_backup_writes_nothing_and_its_error_quotes_no_event(
    source: SqliteRepository, empty: SqliteRepository, blobs: LocalBlobStore
) -> None:
    write_backup(source, blobs, day=DAY, run_id=None)
    key = backup_key(DAY, "feedback.jsonl")
    good = blobs.get(blobs.ref_for(key)).decode().splitlines()
    damaged = [good[0], good[1].replace('"open"', '"unknown-event"'), good[2]]
    blobs.put(key, ("\n".join(damaged) + "\n").encode())

    with pytest.raises(BackupError) as raised:
        restore_backup(empty, blobs, day=DAY, dry_run=False)
    assert "line 2" in str(raised.value)
    assert raised.value.__cause__ is None
    assert all(secret not in str(raised.value) for secret in SECRET_ARTICLE_IDS)
    assert empty.list_feedback() == []

    blobs.put(key, ("\n".join(good[:2]) + "\n").encode())  # one line lost
    with pytest.raises(BackupError, match="manifest says 3"):
        restore_backup(empty, blobs, day=DAY, dry_run=False)
    assert empty.list_feedback() == []


def test_cli_backup_refuses_to_replace_a_days_backup_without_force(data_dir: Path) -> None:
    with SqliteRepository(data_dir / "newsfeed.db") as repo:
        for event in _events():
            repo.save_feedback(event)
    assert runner.invoke(cli.app, ["backup", "--day", "2026-11-10"]).exit_code == 0
    jsonl = data_dir / "blobs" / "backups" / "2026-11-10" / "feedback.jsonl"
    with SqliteRepository(data_dir / "newsfeed.db") as repo:
        repo.save_feedback(
            Feedback(
                id="fb-4", article_id="a4", event=FeedbackEvent.LIKE, created_at=datetime.now(UTC)
            )
        )

    refused = runner.invoke(cli.app, ["backup", "--day", "2026-11-10"])
    assert refused.exit_code == 1
    assert "--force" in refused.output
    assert len(jsonl.read_text().splitlines()) == 3

    forced = runner.invoke(cli.app, ["backup", "--day", "2026-11-10", "--force"])
    assert forced.exit_code == 0, forced.output
    assert len(jsonl.read_text().splitlines()) == 4


def test_cli_restore_into_firestore_needs_yes(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NEWSFEED_STORAGE=gcp selects Firestore; the guard stops before the store is written."""
    blobs = LocalBlobStore(data_dir / "blobs")
    with SqliteRepository(tmp_path / "source.db") as repo:
        for event in _events():
            repo.save_feedback(event)
        write_backup(repo, blobs, day=date(2026, 11, 10), run_id=None)
    target = tmp_path / "standin-for-firestore.db"
    monkeypatch.setenv("NEWSFEED_STORAGE", "gcp")
    monkeypatch.setenv("NEWSFEED_GCS_BUCKET", "test-bucket")
    monkeypatch.setattr(cli, "open_storage", lambda _s: (SqliteRepository(target), blobs))

    refused = runner.invoke(cli.app, ["restore-backup", "2026-11-10"])
    assert refused.exit_code == 1
    assert "--yes" in refused.output
    with SqliteRepository(target) as repo:
        assert repo.list_feedback() == []

    dry = runner.invoke(cli.app, ["restore-backup", "2026-11-10", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    with SqliteRepository(target) as repo:
        assert repo.list_feedback() == []

    confirmed = runner.invoke(cli.app, ["restore-backup", "2026-11-10", "--yes"])
    assert confirmed.exit_code == 0, confirmed.output
    with SqliteRepository(target) as repo:
        assert len(repo.list_feedback()) == 3


def test_restore_logs_collections_it_skips(caplog: pytest.LogCaptureFixture) -> None:
    blobs = InMemoryBlobStore()
    manifest = {
        "day": DAY.isoformat(),
        "run_id": None,
        "written_at": "2026-11-16T09:00:00Z",
        "counts": {"future": 2},
    }
    blobs.put(backup_key(DAY, "manifest.json"), json.dumps(manifest).encode(), "application/json")

    with caplog.at_level(logging.INFO):
        restore_backup(InMemoryRepository(), blobs, day=DAY, dry_run=False)

    messages = [r.getMessage() for r in caplog.records]
    assert any("doesn't know" in m for m in messages)  # in the manifest, not in the code
    assert any("backup lacks" in m for m in messages)  # in the code, not in the manifest
