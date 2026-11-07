"""Security headers: the API sends the values the client build declares (#174, #175)."""

from __future__ import annotations

import json
from pathlib import Path

from newsfeed.api.headers import SECURITY_HEADERS
from tests.api.support import (
    ARTICLE_ID,
    OWNER_TOKEN,
    SECURITY_HEADERS_PATH,
    STRANGER_TOKEN,
    RaisingVerifier,
    RecordingBlobStore,
    auth,
    client_for,
    make_app,
)


def _expected() -> dict[str, str]:
    # Never skipped: a missing file is a failure, not a reason to pass.
    assert SECURITY_HEADERS_PATH.is_file(), f"{SECURITY_HEADERS_PATH} is missing"
    expected = json.loads(SECURITY_HEADERS_PATH.read_text())
    assert set(expected) == {
        "Content-Security-Policy",
        "Referrer-Policy",
        "X-Content-Type-Options",
    }
    return expected


def test_security_headers_match_the_client_declaration() -> None:
    assert dict(SECURITY_HEADERS) == _expected()


def test_every_response_carries_the_security_headers(
    blobs: RecordingBlobStore, client_dir: Path
) -> None:
    expected = _expected()
    client = client_for(make_app(blobs, client_dir=client_dir))
    feed = client.get("/api/feed", headers=auth())
    responses = {
        "no header (401)": client.get("/api/feed"),
        "stranger (403)": client.get("/api/feed", headers=auth(STRANGER_TOKEN)),
        "feed (200)": feed,
        "feed (304)": client.get(
            "/api/feed", headers={**auth(), "If-None-Match": feed.headers["etag"]}
        ),
        "article (200)": client.get(f"/api/articles/{ARTICLE_ID}", headers=auth()),
        "bad article id (404)": client.get("/api/articles/NOPE", headers=auth()),
        "unknown api (404)": client.get("/api/unknown", headers=auth()),
        "index (200)": client.get("/", headers=auth()),
        "asset (200)": client.get("/assets/app-abc123.js", headers=auth()),
        "no feed (503)": client_for(make_app(RecordingBlobStore())).get(
            "/api/feed", headers=auth()
        ),
        "check failed (503)": client_for(
            make_app(blobs, RaisingVerifier(ConnectionError("down")))
        ).get("/api/feed", headers=auth(OWNER_TOKEN)),
    }

    for name, response in responses.items():
        for header, value in expected.items():
            assert response.headers.get(header) == value, (name, header)
