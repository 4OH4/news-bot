"""Shared pieces for the API tests: invented identities, recording fakes and a built client.

Every email address here is invented. The owner's real address is read only from
``NEWSFEED_OWNER_EMAIL`` at run time and never appears in the repo.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from newsfeed.models import VerifiedIdentity
from newsfeed.ports import BlobStore, TokenVerifier
from tests.fakes import FakeTokenVerifier, InMemoryBlobStore, InMemoryRepository

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

IAP_HEADER = "x-goog-iap-jwt-assertion"

OWNER_EMAIL = "owner@example.com"
OWNER_TOKEN = "owner-token-7f3a9c"
STRANGER_TOKEN = "stranger-token-41be"
UNVERIFIED_TOKEN = "unverified-token-0d2e"
SHOUTY_TOKEN = "shouty-token-88aa"

ARTICLE_ID = "0123456789abcdef"

# Markers that must never appear in a refused response.
FEED_MARKER = "FEED-MARKER-5b1c"
ARTICLE_MARKER = "ARTICLE-MARKER-9e07"
SAVED_MARKER = "SAVED-MARKER-c4d2"
INDEX_MARKER = "INDEX-MARKER-2f6a"
ASSET_MARKER = "ASSET-MARKER-7a90"
SECRET_MARKER = "SECRET-MARKER-e3b8"

ALL_MARKERS = (FEED_MARKER, ARTICLE_MARKER, SAVED_MARKER, INDEX_MARKER, ASSET_MARKER, SECRET_MARKER)

FEED_BYTES = json.dumps({"schema_version": 1, "items": [], "note": FEED_MARKER}).encode()
ARTICLE_HTML = f"<article><p>{ARTICLE_MARKER} invented text</p></article>".encode()

SECURITY_HEADERS_PATH = Path(__file__).parents[3] / "client" / "security-headers.json"


def identities() -> dict[str, VerifiedIdentity]:
    """The tokens the fake verifier accepts."""
    return {
        OWNER_TOKEN: VerifiedIdentity(email=OWNER_EMAIL, email_verified=True),
        STRANGER_TOKEN: VerifiedIdentity(email="stranger@example.org", email_verified=True),
        UNVERIFIED_TOKEN: VerifiedIdentity(email=OWNER_EMAIL, email_verified=False),
        SHOUTY_TOKEN: VerifiedIdentity(email="OWNER@Example.COM", email_verified=True),
    }


class RecordingVerifier:
    """Wraps ``FakeTokenVerifier`` and records every token it is asked about."""

    def __init__(self) -> None:
        self._inner = FakeTokenVerifier(identities())
        self.tokens: list[str] = []

    def verify(self, token: str) -> VerifiedIdentity:
        self.tokens.append(token)
        return self._inner.verify(token)


class RaisingVerifier:
    """A verifier whose check can't be made, such as when IAP's keys can't be fetched."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def verify(self, token: str) -> VerifiedIdentity:
        raise self.error


class RecordingBlobStore:
    """An ``InMemoryBlobStore`` that records which refs were read."""

    def __init__(self) -> None:
        self.inner = InMemoryBlobStore()
        self.reads: list[str] = []

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        return self.inner.put(key, data, content_type)

    def ref_for(self, key: str) -> str:
        return self.inner.ref_for(key)

    def get(self, ref: str) -> bytes:
        self.reads.append(ref)
        return self.inner.get(ref)

    def exists(self, ref: str) -> bool:
        self.reads.append(ref)
        return self.inner.exists(ref)


class UntouchableRepository:
    """A repository that records any use: the read routes need no repository read."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def _call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append(name)
            raise AssertionError(f"the API read the repository: {name}")

        return _call


@pytest.fixture
def blobs() -> RecordingBlobStore:
    store = RecordingBlobStore()
    store.put("feed.json", FEED_BYTES, "application/json")
    store.put(f"articles/{ARTICLE_ID}.html", ARTICLE_HTML, "text/html")
    store.reads.clear()
    return store


@pytest.fixture
def client_dir(tmp_path: Path) -> Path:
    """A built client, with a secret file beside it that must never be served."""
    built = tmp_path / "dist"
    (built / "assets").mkdir(parents=True)
    (built / "index.html").write_text(f"<!doctype html><title>reader</title>{INDEX_MARKER}")
    (built / "sw.js").write_text("self.addEventListener('fetch', () => {});\n")
    (built / "manifest.webmanifest").write_text('{"name": "reader"}\n')
    (built / "assets" / "app-abc123.js").write_text(f"console.log('{ASSET_MARKER}');\n")
    (tmp_path / "secret").write_text(SECRET_MARKER)
    (tmp_path / "pyproject.toml").write_text(f"[project]\nname = '{SECRET_MARKER}'\n")
    (built / "escape.txt").symlink_to(tmp_path / "secret")
    (built / "assets" / "escape.js").symlink_to(tmp_path / "secret")
    return built


def make_app(
    blobs: BlobStore | None = None,
    verifier: TokenVerifier | None = None,
    *,
    owner_email: str = OWNER_EMAIL,
    client_dir: Path | None = None,
    repository: Any = None,
) -> FastAPI:
    """Build the app with test defaults."""
    # Imported here so the fixtures load, and other test packages run, without the API.
    from newsfeed.api.app import create_app

    return create_app(
        blobs if blobs is not None else InMemoryBlobStore(),
        repository if repository is not None else InMemoryRepository(),
        verifier if verifier is not None else FakeTokenVerifier(identities()),
        owner_email=owner_email,
        client_dir=client_dir,
    )


def client_for(app: FastAPI) -> TestClient:
    """A test client that returns 5xx responses instead of raising server errors."""
    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=False)


def auth(token: str = OWNER_TOKEN) -> Mapping[str, str]:
    """Headers carrying ``token`` as the IAP assertion."""
    return {IAP_HEADER: token}


def assert_no_content(body: bytes) -> None:
    """A refused response carries none of the protected content."""
    text = body.decode("utf-8", errors="replace")
    for marker in ALL_MARKERS:
        assert marker not in text, f"refused response leaked {marker}"
