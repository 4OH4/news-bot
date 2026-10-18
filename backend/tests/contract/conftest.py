"""Fixtures that run the storage contract tests against every implementation.

Each fixture is parametrised over the in-memory fakes and the real adapters.
To run the contract suites against a new adapter (Firestore and GCS in
Phase 2), add a parameter here; the tests themselves don't change.

The ``open_*`` fixtures yield an *opener*: a callable that returns a new handle
onto the same underlying store each time it is called, so tests can check that
data survives reopening. The in-memory fakes have nothing to reopen, so their
opener returns the same instance every time.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from newsfeed.adapters.firestore_repository import FirestoreRepository
from newsfeed.adapters.localfs_blobstore import LocalBlobStore
from newsfeed.adapters.sqlite_repository import SqliteRepository
from newsfeed.ports import BlobStore, Repository
from tests.fakes import InMemoryBlobStore, InMemoryRepository

RepositoryOpener = Callable[[], Repository]
BlobStoreOpener = Callable[[], BlobStore]


_NEEDS_FIRESTORE_EMULATOR = pytest.mark.skipif(
    not os.environ.get("FIRESTORE_EMULATOR_HOST"),
    reason="set FIRESTORE_EMULATOR_HOST to run against the Firestore emulator",
)


@pytest.fixture(
    params=[
        "memory",
        "sqlite",
        pytest.param("firestore", marks=[pytest.mark.emulator, _NEEDS_FIRESTORE_EMULATOR]),
    ]
)
def open_repository(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[RepositoryOpener]:
    """Yield an opener for each ``Repository`` implementation."""
    if request.param == "memory":
        fake = InMemoryRepository()
        yield lambda: fake
        return

    if request.param == "firestore":
        from google.auth.credentials import AnonymousCredentials
        from google.cloud import firestore

        # A unique prefix isolates each test; the emulator needs no credentials.
        client = firestore.Client(project="newsfeed-test", credentials=AnonymousCredentials())
        prefix = f"t{uuid.uuid4().hex[:12]}-"
        yield lambda: FirestoreRepository(client, prefix=prefix)
        client.close()
        return

    opened: list[SqliteRepository] = []

    def open_sqlite() -> Repository:
        repo = SqliteRepository(tmp_path / "newsfeed.db")
        opened.append(repo)
        return repo

    yield open_sqlite
    for repo in opened:
        repo.close()


@pytest.fixture
def repo(open_repository: RepositoryOpener) -> Repository:
    """A fresh, empty ``Repository`` of each implementation."""
    return open_repository()


@pytest.fixture(params=["memory", "localfs"])
def open_blob_store(request: pytest.FixtureRequest, tmp_path: Path) -> BlobStoreOpener:
    """Return an opener for each ``BlobStore`` implementation."""
    if request.param == "memory":
        fake = InMemoryBlobStore()
        return lambda: fake
    return lambda: LocalBlobStore(tmp_path / "blobs")


@pytest.fixture
def blob_store(open_blob_store: BlobStoreOpener) -> BlobStore:
    """A fresh, empty ``BlobStore`` of each implementation."""
    return open_blob_store()


def run_threads(worker: Callable[[int], None], count: int = 8) -> None:
    """Run ``worker(i)`` on ``count`` threads at once; fail if any of them raised."""
    errors: list[BaseException] = []
    barrier = threading.Barrier(count)

    def target(i: int) -> None:
        try:
            barrier.wait()
            worker(i)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=target, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
