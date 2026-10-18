"""Tests for storage selection in ``wiring``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from newsfeed.adapters.localfs_blobstore import LocalBlobStore
from newsfeed.adapters.sqlite_repository import SqliteRepository
from newsfeed.settings import Settings
from newsfeed.wiring import open_services, open_storage


def _settings(tmp_path: Path, **kwargs: object) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path, **kwargs)  # type: ignore[call-arg]


def test_local_is_default_and_unchanged(tmp_path: Path) -> None:
    with open_services(_settings(tmp_path)) as services:
        assert isinstance(services.repository, SqliteRepository)
        assert isinstance(services.blobs, LocalBlobStore)


def test_gcp_requires_bucket(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="NEWSFEED_GCS_BUCKET"):
        _settings(tmp_path, storage="gcp")


def test_gcp_builds_firestore_and_gcs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from newsfeed.adapters.firestore_repository import FirestoreRepository
    from newsfeed.adapters.gcs_blobstore import GcsBlobStore

    fs_ctor, gcs_ctor = MagicMock(), MagicMock()
    monkeypatch.setattr("google.cloud.firestore.Client", fs_ctor)
    monkeypatch.setattr("google.cloud.storage.Client", gcs_ctor)

    settings = _settings(
        tmp_path,
        storage="gcp",
        gcs_bucket="b",
        gcs_prefix="p",
        firestore_database="db",
        gcp_project="proj",
    )
    repository, blobs = open_storage(settings)

    assert isinstance(repository, FirestoreRepository)
    assert isinstance(blobs, GcsBlobStore)
    fs_ctor.assert_called_once_with(project="proj", database="db")
    gcs_ctor.assert_called_once_with(project="proj")
    gcs_ctor.return_value.bucket.assert_called_once_with("b")


def test_gcp_services_open_and_close_without_close_method(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("google.cloud.firestore.Client", MagicMock())
    monkeypatch.setattr("google.cloud.storage.Client", MagicMock())
    with open_services(_settings(tmp_path, storage="gcp", gcs_bucket="b")) as services:
        assert type(services.repository).__name__ == "FirestoreRepository"


def test_importing_wiring_does_not_import_google_cloud() -> None:
    # A subprocess: other tests in this process may already have imported it.
    code = (
        "import sys, newsfeed.wiring; "
        "sys.exit(any(m.startswith('google.cloud') for m in sys.modules))"
    )
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0
