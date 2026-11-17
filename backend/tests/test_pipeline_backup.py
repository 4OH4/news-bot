"""The daily feedback backup at the end of ``run_pipeline`` (#330).

Runs the whole pipeline with the real SQLite repository and local blob store,
and judges it by the files under ``backups/<day>/``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from newsfeed.adapters.localfs_blobstore import LocalBlobStore
from newsfeed.adapters.sqlite_repository import SqliteRepository
from newsfeed.config import UrlParamsConfig
from newsfeed.models import Feedback, FeedbackEvent, RunStats
from newsfeed.pipeline import run_pipeline
from newsfeed.ports import BlobStore
from newsfeed.wiring import Services, build_ingesters
from tests.fakes import FakeExtractor, FakeFetcher

NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
TODAY = Path("backups") / "2026-11-16"


class CountingRepository(SqliteRepository):
    """Counts ``list_feedback`` calls, the read a backup costs."""

    feedback_lists = 0

    def list_feedback(self, since: datetime | None = None) -> list[Feedback]:
        self.feedback_lists += 1
        return super().list_feedback(since)


class FailingFeedbackWrites(LocalBlobStore):
    """A blob store whose writes of ``feedback.jsonl`` fail."""

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        if key.endswith("feedback.jsonl"):
            raise OSError("simulated bucket outage")
        return super().put(key, data, content_type)


@pytest.fixture
def repository(tmp_path: Path) -> Iterator[CountingRepository]:
    with CountingRepository(tmp_path / "newsfeed.db") as repo:
        for n in range(3):
            repo.save_feedback(
                Feedback(id=f"fb-{n}", article_id=f"a{n}", event=FeedbackEvent.OPEN, created_at=NOW)
            )
        yield repo


def _run(tmp_path: Path, repository: SqliteRepository, blobs: BlobStore | None = None) -> RunStats:
    services = Services(
        repository=repository,
        blobs=blobs or LocalBlobStore(tmp_path / "blobs"),
        fetcher=FakeFetcher(),
        ingesters=build_ingesters(),
        extractor=FakeExtractor(),
        url_rules=UrlParamsConfig(),
    )
    return run_pipeline(services, [], clock=lambda: NOW)


def test_run_writes_todays_backup_after_publish(
    tmp_path: Path, repository: CountingRepository
) -> None:
    _run(tmp_path, repository)

    day = tmp_path / "blobs" / TODAY
    assert len((day / "feedback.jsonl").read_text().splitlines()) == 3
    assert json.loads((day / "manifest.json").read_text())["counts"] == {
        "feedback": 3,
        "preferences": 0,
    }
    assert (tmp_path / "blobs" / "feed.json").is_file()


def test_second_run_the_same_day_neither_lists_feedback_nor_rewrites_the_backup(
    tmp_path: Path, repository: CountingRepository
) -> None:
    _run(tmp_path, repository)
    first_lists = repository.feedback_lists
    manifest = tmp_path / "blobs" / TODAY / "manifest.json"
    written = manifest.read_bytes()
    repository.save_feedback(
        Feedback(id="fb-late", article_id="a9", event=FeedbackEvent.LIKE, created_at=NOW)
    )

    _run(tmp_path, repository)

    second_lists = repository.feedback_lists - first_lists
    assert first_lists - second_lists == 1  # the backup's one list_feedback call
    assert manifest.read_bytes() == written
    assert len((tmp_path / "blobs" / TODAY / "feedback.jsonl").read_text().splitlines()) == 3


def test_failed_backup_write_leaves_no_manifest_and_the_next_run_retries(
    tmp_path: Path, repository: CountingRepository, log_records: list[logging.LogRecord]
) -> None:
    stats = _run(tmp_path, repository, FailingFeedbackWrites(tmp_path / "blobs"))

    assert not (tmp_path / "blobs" / TODAY / "manifest.json").exists()
    assert stats.error_count >= 1
    # Rank warns on every run here that embeddings are off; the backup's warning is another.
    assert any(
        r.levelno >= logging.WARNING and getattr(r, "stage", None) != "rank" for r in log_records
    )
    assert (tmp_path / "blobs" / "feed.json").is_file()

    _run(tmp_path, repository)

    day = tmp_path / "blobs" / TODAY
    assert json.loads((day / "manifest.json").read_text())["counts"] == {
        "feedback": 3,
        "preferences": 0,
    }
    assert len((day / "feedback.jsonl").read_text().splitlines()) == 3
