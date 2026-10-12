"""Contract tests every ``BlobStore`` implementation must pass.

Run against the in-memory fake and the local-filesystem adapter through the
fixtures in ``conftest.py``; Phase 2 adds the GCS adapter there. Refs are
treated as opaque: tests only pass back refs that ``put`` returned, apart from
the checks that invalid refs are reported as missing.
"""

from __future__ import annotations

import threading

import pytest

from newsfeed.ports import BlobNotFoundError, BlobStore
from tests.contract.conftest import BlobStoreOpener, run_threads

INVALID_KEYS = [
    "",
    "../x",
    "../../etc/passwd",
    "/etc/passwd",
    "a/../../b",
    "a/../b",
    "./a",
    "a//b",
    "a/",
    "a\\..\\b",
    "a b",
    "articles/ünï.html",
]


def test_put_get_exists(blob_store: BlobStore) -> None:
    ref = blob_store.put("articles/abc123.html", b"<p>Invented</p>", "text/html")
    assert isinstance(ref, str)
    assert blob_store.exists(ref)
    assert blob_store.get(ref) == b"<p>Invented</p>"


def test_default_content_type(blob_store: BlobStore) -> None:
    ref = blob_store.put("feed.json", b"{}")
    assert blob_store.get(ref) == b"{}"


def test_binary_and_empty_blobs(blob_store: BlobStore) -> None:
    data = bytes(range(256)) * 64
    binary = blob_store.put("images/lead.jpg", data, "image/jpeg")
    empty = blob_store.put("empty.txt", b"")
    assert blob_store.get(binary) == data
    assert blob_store.exists(empty)
    assert blob_store.get(empty) == b""


def test_put_overwrites_with_stable_ref(blob_store: BlobStore) -> None:
    first = blob_store.put("feed.json", b"first, longer version")
    second = blob_store.put("feed.json", b"2")
    assert first == second
    assert blob_store.get(second) == b"2"


def test_distinct_keys_get_distinct_refs(blob_store: BlobStore) -> None:
    refs = {
        blob_store.put(key, key.encode())
        for key in ("a.html", "articles/a.html", "articles/b.html", "x/y/z.html")
    }
    assert len(refs) == 4
    for ref in refs:
        assert blob_store.exists(ref)


def test_missing_blob(blob_store: BlobStore) -> None:
    ref = blob_store.put("articles/present.html", b"x")
    missing = ref.replace("present", "absent")
    assert not blob_store.exists(missing)
    with pytest.raises(BlobNotFoundError):
        blob_store.get(missing)


def test_blob_not_found_is_a_lookup_error() -> None:
    assert issubclass(BlobNotFoundError, LookupError)


@pytest.mark.parametrize("key", INVALID_KEYS)
def test_put_rejects_invalid_keys(blob_store: BlobStore, key: str) -> None:
    with pytest.raises(ValueError, match="invalid blob key"):
        blob_store.put(key, b"x")


@pytest.mark.parametrize("ref", ["", "../x", "/etc/passwd", "a/../../b", "../blobs/x"])
def test_invalid_refs_are_missing(blob_store: BlobStore, ref: str) -> None:
    assert not blob_store.exists(ref)
    with pytest.raises(BlobNotFoundError):
        blob_store.get(ref)


def test_data_persists_across_reopening(open_blob_store: BlobStoreOpener) -> None:
    ref = open_blob_store().put("articles/kept.html", b"<p>kept</p>", "text/html")
    reopened = open_blob_store()
    assert reopened.exists(ref)
    assert reopened.get(ref) == b"<p>kept</p>"


def test_concurrent_puts_from_eight_threads(blob_store: BlobStore) -> None:
    refs: dict[str, str] = {}
    lock = threading.Lock()

    def worker(i: int) -> None:
        for n in range(25):
            key = f"articles/t{i}-{n}.html"
            ref = blob_store.put(key, key.encode(), "text/html")
            # Everyone also rewrites one shared key.
            blob_store.put("feed.json", f"writer {i}".encode() * 1000)
            with lock:
                refs[key] = ref

    run_threads(worker)
    assert len(refs) == 8 * 25
    for key, ref in refs.items():
        assert blob_store.get(ref) == key.encode()
    shared = blob_store.get(blob_store.put("feed.json", b"final"))
    assert shared == b"final"


def test_readers_never_see_partial_writes(blob_store: BlobStore) -> None:
    versions = [bytes([65 + i]) * 200_000 for i in range(4)]
    ref = blob_store.put("feed.json", versions[0])
    stop = threading.Event()
    seen_bad: list[int] = []
    errors: list[BaseException] = []
    reads = [0, 0, 0]

    def reader(i: int) -> None:
        # Catch everything: an exception in a thread would otherwise only kill
        # the thread, and the test would pass without having read anything.
        try:
            while not stop.is_set():
                data = blob_store.get(ref)
                reads[i] += 1
                if data not in versions:
                    seen_bad.append(len(data))
        except BaseException as exc:
            errors.append(exc)

    readers = [threading.Thread(target=reader, args=(i,)) for i in range(len(reads))]
    for thread in readers:
        thread.start()
    try:
        for round_ in range(40):
            blob_store.put("feed.json", versions[round_ % len(versions)])
    finally:
        stop.set()
        for thread in readers:
            thread.join()
    assert errors == []
    assert seen_bad == []
    assert sum(reads) > 0
