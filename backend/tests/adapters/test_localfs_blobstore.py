"""Filesystem-specific behaviour of ``LocalBlobStore``.

The port semantics are covered by ``tests/contract/test_blobstore_contract.py``;
these tests cover the on-disk layout, relative refs, path-traversal safety and
atomic writes.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest

from newsfeed.adapters.localfs_blobstore import LocalBlobStore
from newsfeed.ports import BlobNotFoundError


def all_files(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def test_blobs_are_files_under_the_base_directory(tmp_path: Path) -> None:
    base = tmp_path / "data" / "blobs"
    store = LocalBlobStore(base)
    store.put("articles/abc.html", b"<p>Invented</p>", "text/html")
    assert (base / "articles" / "abc.html").read_bytes() == b"<p>Invented</p>"


def test_refs_are_relative_to_the_base_directory(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path / "blobs")
    ref = store.put("articles/abc.html", b"x")
    assert ref == "articles/abc.html"
    assert not Path(ref).is_absolute()
    assert str(tmp_path) not in ref


def test_refs_survive_moving_the_base_directory(tmp_path: Path) -> None:
    ref = LocalBlobStore(tmp_path / "old").put("articles/abc.html", b"moved")
    shutil.move(tmp_path / "old", tmp_path / "new")
    assert LocalBlobStore(tmp_path / "new").get(ref) == b"moved"


def test_accepts_a_string_base_directory(tmp_path: Path) -> None:
    store = LocalBlobStore(str(tmp_path / "blobs"))
    assert store.get(store.put("feed.json", b"{}")) == b"{}"


def test_reading_an_empty_store_does_not_create_it(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path / "blobs")
    assert not store.exists("feed.json")
    assert not (tmp_path / "blobs").exists()


@pytest.mark.parametrize("key", ["../x", "../../x", "a/../../x", "/tmp/x"])
def test_traversal_keys_write_nothing(tmp_path: Path, key: str) -> None:
    store = LocalBlobStore(tmp_path / "base" / "blobs")
    with pytest.raises(ValueError, match="invalid blob key"):
        store.put(key, b"escaped")
    assert all_files(tmp_path) == []


def test_traversal_refs_read_nothing(tmp_path: Path) -> None:
    (tmp_path / "secret.txt").write_bytes(b"secret")
    store = LocalBlobStore(tmp_path / "blobs")
    store.put("ok.txt", b"ok")
    assert not store.exists("../secret.txt")
    with pytest.raises(BlobNotFoundError):
        store.get("../secret.txt")
    with pytest.raises(BlobNotFoundError):
        store.get(str(tmp_path / "secret.txt"))


def test_a_directory_is_not_a_blob(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path / "blobs")
    store.put("articles/abc.html", b"x")
    assert not store.exists("articles")
    with pytest.raises(BlobNotFoundError):
        store.get("articles")
    with pytest.raises(BlobNotFoundError):
        store.get("articles/abc.html/child")


def test_no_temporary_files_are_left_behind(tmp_path: Path) -> None:
    base = tmp_path / "blobs"
    store = LocalBlobStore(base)
    for n in range(5):
        store.put("feed.json", str(n).encode())
        store.put(f"articles/{n}.html", b"x")
    assert all_files(base) == [*(f"articles/{n}.html" for n in range(5)), "feed.json"]


def test_failed_write_keeps_the_old_blob_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path / "blobs"
    store = LocalBlobStore(base)
    ref = store.put("feed.json", b"old")

    def fail_replace(self: Path, target: Path) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="disk full"):
        store.put("feed.json", b"new")
    monkeypatch.undo()
    assert store.get(ref) == b"old"
    assert all_files(base) == ["feed.json"]


def test_blobs_are_world_readable(tmp_path: Path) -> None:
    store = LocalBlobStore(tmp_path / "blobs")
    store.put("feed.json", b"{}")
    mode = stat.S_IMODE(os.stat(tmp_path / "blobs" / "feed.json").st_mode)
    assert mode == 0o644
