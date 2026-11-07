"""Every route sits behind the IAP JWT check, and the check fails closed (#175)."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.api.support import (
    ARTICLE_ID,
    IAP_HEADER,
    OWNER_TOKEN,
    SHOUTY_TOKEN,
    STRANGER_TOKEN,
    UNVERIFIED_TOKEN,
    RaisingVerifier,
    RecordingBlobStore,
    RecordingVerifier,
    assert_no_content,
    auth,
    client_for,
    make_app,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

_PARAM = re.compile(r"\{(\w+)(?::\w+)?\}")
_KNOWN_PATHS = [
    "/",
    "/index.html",
    "/assets/app-abc123.js",
    "/api/feed",
    f"/api/articles/{ARTICLE_ID}",
]


def _fill(path: str) -> str:
    """A concrete URL for a route path, with a plausible value for each parameter."""

    def value(match: re.Match[str]) -> str:
        return "index.html" if match.group(1) == "path" else ARTICLE_ID

    return _PARAM.sub(value, path)


def _requests(app: FastAPI) -> list[tuple[str, str]]:
    """(method, url) for every route in the app, plus the known paths, so new routes are covered."""
    calls: set[tuple[str, str]] = {("GET", path) for path in _KNOWN_PATHS}
    for route in app.routes:
        path = getattr(route, "path", None)
        if path is None:
            continue
        methods = getattr(route, "methods", None) or {"GET"}
        url = _fill(path) if getattr(route, "methods", None) else path.rstrip("/") + "/index.html"
        calls.update((method, url) for method in methods)
    return sorted(calls)


def test_the_route_walk_sees_the_api_and_client_routes(
    blobs: RecordingBlobStore, client_dir: Path
) -> None:
    app = make_app(blobs, client_dir=client_dir)
    paths = {getattr(route, "path", None) for route in app.routes}
    assert {"/api/feed", "/api/articles/{id}"} <= paths
    assert any(p is not None and p.startswith("/{") for p in paths), paths


def test_every_route_without_the_iap_header_is_401_with_no_content(
    blobs: RecordingBlobStore, client_dir: Path
) -> None:
    app = make_app(blobs, client_dir=client_dir)
    client = client_for(app)

    for method, url in _requests(app):
        response = client.request(method, url)
        assert response.status_code == 401, (method, url, response.status_code)
        assert_no_content(response.content)
    assert blobs.reads == []


def test_every_route_with_a_rejected_token_is_401(
    blobs: RecordingBlobStore, client_dir: Path
) -> None:
    app = make_app(blobs, client_dir=client_dir)
    client = client_for(app)

    for method, url in _requests(app):
        response = client.request(method, url, headers=auth("garbage.not-a.jwt"))
        assert response.status_code == 401, (method, url, response.status_code)
        assert_no_content(response.content)


def test_a_missing_header_is_still_checked_by_the_verifier_as_an_empty_token(
    blobs: RecordingBlobStore,
) -> None:
    verifier = RecordingVerifier()
    client = client_for(make_app(blobs, verifier))

    response = client.get("/api/feed")

    assert response.status_code == 401
    assert verifier.tokens == [""]


def test_the_owners_token_reaches_the_feed(blobs: RecordingBlobStore) -> None:
    verifier = RecordingVerifier()
    client = client_for(make_app(blobs, verifier))

    response = client.get("/api/feed", headers=auth(OWNER_TOKEN))

    assert response.status_code == 200
    assert verifier.tokens == [OWNER_TOKEN]


@pytest.mark.parametrize("token", [STRANGER_TOKEN, UNVERIFIED_TOKEN])
def test_someone_elses_or_an_unverified_account_is_403_with_no_feed(
    blobs: RecordingBlobStore, client_dir: Path, token: str
) -> None:
    app = make_app(blobs, client_dir=client_dir)
    client = client_for(app)

    for method, url in _requests(app):
        response = client.request(method, url, headers=auth(token))
        assert response.status_code == 403, (method, url, response.status_code)
        assert_no_content(response.content)
    assert blobs.reads == []


def test_the_owner_email_is_compared_case_insensitively(blobs: RecordingBlobStore) -> None:
    client = client_for(make_app(blobs))
    assert client.get("/api/feed", headers=auth(SHOUTY_TOKEN)).status_code == 200

    configured_mixed = client_for(make_app(blobs, owner_email="Owner@EXAMPLE.com"))
    assert configured_mixed.get("/api/feed", headers=auth(OWNER_TOKEN)).status_code == 200


@pytest.mark.parametrize(
    "error",
    [ConnectionError("keys unreachable"), RuntimeError("unexpected"), OSError("dns")],
    ids=["connection", "runtime", "oserror"],
)
def test_a_check_that_cant_be_made_is_503_on_every_route_never_401_or_content(
    blobs: RecordingBlobStore, client_dir: Path, error: Exception
) -> None:
    app = make_app(blobs, RaisingVerifier(error), client_dir=client_dir)
    client = client_for(app)

    for method, url in _requests(app):
        response = client.request(method, url, headers=auth(OWNER_TOKEN))
        assert response.status_code == 503, (method, url, response.status_code)
        assert_no_content(response.content)
        if method != "HEAD":
            assert "detail" in response.json()
    assert blobs.reads == []


def test_a_check_that_cant_be_made_is_logged_as_a_warning(
    blobs: RecordingBlobStore, log_records: list[logging.LogRecord]
) -> None:
    client = client_for(make_app(blobs, RaisingVerifier(ConnectionError("keys unreachable"))))

    client.get("/api/feed", headers=auth(OWNER_TOKEN))

    assert any(r.levelno == logging.WARNING for r in log_records), [
        (r.levelname, r.getMessage()) for r in log_records
    ]


def test_the_token_is_never_logged(
    blobs: RecordingBlobStore, log_records: list[logging.LogRecord]
) -> None:
    secret_garbage = "garbage-token-d00d1e"
    client = client_for(make_app(blobs))
    client.get("/api/feed", headers=auth(OWNER_TOKEN))
    client.get("/api/feed", headers=auth(STRANGER_TOKEN))
    client.get("/api/feed", headers={IAP_HEADER: secret_garbage})
    failing = client_for(make_app(blobs, RaisingVerifier(ConnectionError("down"))))
    failing.get("/api/feed", headers=auth(OWNER_TOKEN))

    assert log_records, "the auth outcome should be logged"
    for record in log_records:
        dumped = record.getMessage() + " " + " ".join(str(v) for v in vars(record).values())
        for token in (OWNER_TOKEN, STRANGER_TOKEN, secret_garbage):
            assert token not in dumped, f"token logged in {record.getMessage()!r}"


def test_the_docs_routes_are_off(blobs: RecordingBlobStore) -> None:
    app = make_app(blobs)
    assert (app.docs_url, app.redoc_url, app.openapi_url) == (None, None, None)

    client = client_for(app)
    for url in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(url, headers=auth(OWNER_TOKEN)).status_code == 404
