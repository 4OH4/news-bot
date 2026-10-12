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

import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from newsfeed.adapters.localfs_blobstore import LocalBlobStore
from newsfeed.adapters.sqlite_repository import SqliteRepository
from newsfeed.ports import BlobStore, Repository
from tests.fakes import InMemoryBlobStore, InMemoryRepository

RepositoryOpener = Callable[[], Repository]
BlobStoreOpener = Callable[[], BlobStore]


@pytest.fixture(params=["memory", "sqlite"])
def open_repository(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[RepositoryOpener]:
    """Yield an opener for each ``Repository`` implementation."""
    if request.param == "memory":
        fake = InMemoryRepository()
        yield lambda: fake
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
