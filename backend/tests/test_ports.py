"""Tests for the port definitions in ``newsfeed.ports``."""

from __future__ import annotations

import pytest

from newsfeed.ports import (
    BlobStore,
    Extractor,
    Fetcher,
    Repository,
    SourceIngester,
    validate_blob_key,
)
from tests.fakes import (
    FakeExtractor,
    FakeFetcher,
    FakeSourceIngester,
    InMemoryBlobStore,
    InMemoryRepository,
)


@pytest.mark.parametrize(
    ("fake", "port"),
    [
        (InMemoryRepository(), Repository),
        (InMemoryBlobStore(), BlobStore),
        (FakeFetcher(), Fetcher),
        (FakeExtractor(), Extractor),
        (FakeSourceIngester(), SourceIngester),
    ],
)
def test_fakes_satisfy_runtime_protocols(fake: object, port: type) -> None:
    # Runtime checks only see member names; mypy checks signatures (fakes.py).
    assert isinstance(fake, port)


def test_unrelated_object_does_not_satisfy_protocol() -> None:
    assert not isinstance(object(), Repository)
    assert not isinstance(InMemoryBlobStore(), Fetcher)


@pytest.mark.parametrize(
    "key",
    ["feed.json", "articles/0123456789abcdef.html", "images/a-b_c/d.v2.png", "..hidden/x"],
)
def test_valid_blob_keys(key: str) -> None:
    assert validate_blob_key(key) == key


@pytest.mark.parametrize(
    "key",
    [
        "",
        "../x",
        "a/../b",
        "a/./b",
        "/abs/path",
        "trailing/",
        "a//b",
        "back\\slash",
        "space here",
        "query?x=1",
        "ünïcode",
        "..",
    ],
)
def test_invalid_blob_keys(key: str) -> None:
    with pytest.raises(ValueError, match="invalid blob key"):
        validate_blob_key(key)
