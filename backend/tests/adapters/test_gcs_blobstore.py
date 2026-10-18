"""GCS-specific behaviour of ``GcsBlobStore``: prefixes and content types.

The shared contract tests (``tests/contract``) cover the ``BlobStore`` behaviour.
These use the in-process fake client, so they need no network or credentials.
"""

from __future__ import annotations

import pytest

from newsfeed.adapters.gcs_blobstore import GcsBlobStore
from tests.contract.fake_gcs import FakeGcsClient


def _store(client: FakeGcsClient, prefix: str = "") -> GcsBlobStore:
    return GcsBlobStore(client, "bkt", prefix=prefix)  # type: ignore[arg-type]


def test_prefix_is_applied_to_every_key_and_not_to_refs() -> None:
    client = FakeGcsClient()
    store = _store(client, "prod")
    refs = [
        store.put("articles/a1.html", b"x", "text/html"),
        store.put("feed.json", b"{}", "application/json"),
        store.put("feeds/run-1.json", b"{}", "application/json"),
    ]
    assert refs == ["articles/a1.html", "feed.json", "feeds/run-1.json"]
    assert sorted(name for _, name in client.state.objects) == [
        "prod/articles/a1.html",
        "prod/feed.json",
        "prod/feeds/run-1.json",
    ]
    assert all(store.exists(ref) for ref in refs)


def test_no_prefix_uses_the_bucket_root() -> None:
    client = FakeGcsClient()
    _store(client).put("feed.json", b"{}")
    assert [name for _, name in client.state.objects] == ["feed.json"]


def test_prefix_slashes_are_normalised() -> None:
    client = FakeGcsClient()
    _store(client, "/prod/").put("feed.json", b"{}")
    assert [name for _, name in client.state.objects] == ["prod/feed.json"]


def test_prefixes_are_isolated_from_each_other() -> None:
    client = FakeGcsClient()
    a, b = _store(client, "a"), _store(client, "b")
    a.put("feed.json", b"A")
    assert not b.exists("feed.json")
    assert b.put("feed.json", b"B") == a.put("feed.json", b"A")
    assert a.get("feed.json") == b"A"
    assert b.get("feed.json") == b"B"


def test_invalid_prefix_is_rejected() -> None:
    with pytest.raises(ValueError, match="invalid blob key"):
        _store(FakeGcsClient(), "../other")


def test_content_type_comes_from_the_extension() -> None:
    client = FakeGcsClient()
    store = _store(client)
    store.put("feed.json", b"{}")
    store.put("articles/a.html", b"<p>x</p>", "application/octet-stream")
    store.put("images/lead.jpg", b"\xff", "image/jpeg")
    store.put("blob.bin", b"\x00")
    types = {name: ctype for (_, name), (_, ctype) in client.state.objects.items()}
    assert types == {
        "feed.json": "application/json",
        "articles/a.html": "text/html; charset=utf-8",
        "images/lead.jpg": "image/jpeg",
        "blob.bin": "application/octet-stream",
    }
