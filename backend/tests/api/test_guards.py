"""Guards around the API's construction and edge cases of its routes (#175)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from newsfeed.models import VerifiedIdentity
from tests.api.support import (
    RecordingBlobStore,
    auth,
    client_for,
    make_app,
)
from tests.fakes import FakeTokenVerifier


@pytest.mark.parametrize("owner", ["", "   "])
def test_the_app_refuses_an_empty_owner_email(owner: str) -> None:
    with pytest.raises(ValueError):
        make_app(owner_email=owner)


def test_the_app_refuses_a_client_dir_that_is_not_a_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        make_app(client_dir=tmp_path / "missing")


@pytest.mark.parametrize("if_none_match", ["*", "W/{etag}", '"other", {etag}'])
def test_if_none_match_lists_and_weak_tags_get_304(
    blobs: RecordingBlobStore, if_none_match: str
) -> None:
    client = client_for(make_app(blobs))
    etag = client.get("/api/feed", headers=auth()).headers["etag"]

    response = client.get(
        "/api/feed", headers={**auth(), "If-None-Match": if_none_match.format(etag=etag)}
    )

    assert response.status_code == 304
    assert response.headers["etag"] == etag


def test_a_client_without_index_html_is_404(client_dir: Path) -> None:
    (client_dir / "index.html").unlink()

    response = client_for(make_app(client_dir=client_dir)).get("/reconnect", headers=auth())

    assert response.status_code == 404


def test_the_app_starts_and_stops_with_its_lifespan(blobs: RecordingBlobStore) -> None:
    # Lifespan messages pass through the security-headers middleware untouched.
    with TestClient(make_app(blobs)) as client:
        assert client.get("/api/feed", headers=auth()).status_code == 200


# Each path guard is tested on its own, because the others would also stop these paths.


@pytest.mark.parametrize("path", ["../secret", "assets/../../secret", "ok/..", "a\x00b"])
def test_the_client_route_refuses_dot_dot_segments_itself(client_dir: Path, path: str) -> None:
    from newsfeed.api.app import _client_response

    with pytest.raises(HTTPException) as raised:
        _client_response(client_dir.resolve(), path)
    assert raised.value.status_code == 404


@pytest.mark.parametrize("path", ["escape.txt", "assets/escape.js", "/etc/hostname"])
def test_the_client_route_refuses_what_resolves_outside_the_client_dir(
    client_dir: Path, path: str
) -> None:
    from newsfeed.api.app import _client_response

    with pytest.raises(HTTPException) as raised:
        _client_response(client_dir.resolve(), path)
    assert raised.value.status_code == 404


# The Kelvin sign (U+212A) casefolds and lower-cases to an ASCII "k", so a lookalike
# address must not match an owner address containing "k". Addresses are invented.
_K_OWNER = "kim@example.com"


@pytest.mark.parametrize(
    "lookalike", ["\u212aim@example.com", "kim@example.co\u0271", "KIM@EXAMPLE.CO\u1e3e"]
)
def test_a_non_ascii_lookalike_of_the_owner_is_403(
    blobs: RecordingBlobStore, lookalike: str
) -> None:
    verifier = FakeTokenVerifier({"t": VerifiedIdentity(email=lookalike, email_verified=True)})
    client = client_for(make_app(blobs, verifier, owner_email=_K_OWNER))

    response = client.get("/api/feed", headers=auth("t"))

    assert response.status_code == 403
    assert blobs.reads == []


def test_the_owner_in_upper_case_still_matches(blobs: RecordingBlobStore) -> None:
    identity = VerifiedIdentity(email="KIM@Example.COM", email_verified=True)
    client = client_for(make_app(blobs, FakeTokenVerifier({"t": identity}), owner_email=_K_OWNER))

    assert client.get("/api/feed", headers=auth("t")).status_code == 200


@pytest.mark.parametrize("owner", ["\u212aim@example.com", "kim@ex\u00e4mple.com"])
def test_the_app_refuses_a_non_ascii_owner_email(owner: str) -> None:
    with pytest.raises(ValueError):
        make_app(owner_email=owner)


# --- #240: gaps found in review ------------------------------------------------


def test_create_app_itself_refuses_a_client_dir_holding_a_dot_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Called directly, not through `serve`, so create_app's own check is what is covered.
    from newsfeed.api.local_safety import UnsafeClientDirError

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>")
    (dist / ".env").write_text("TOKEN=invented-secret-52cd\n")

    with pytest.raises(UnsafeClientDirError):
        make_app(client_dir=dist)


def test_an_index_html_that_links_outside_the_client_dir_is_not_served(
    client_dir: Path, tmp_path: Path
) -> None:
    from tests.api.support import SECRET_MARKER

    (client_dir / "index.html").unlink()
    (client_dir / "index.html").symlink_to(tmp_path / "secret")

    response = client_for(make_app(client_dir=client_dir)).get("/reconnect", headers=auth())

    assert response.status_code == 404
    assert SECRET_MARKER not in response.text


def test_an_index_html_that_is_a_directory_is_404(client_dir: Path) -> None:
    (client_dir / "index.html").unlink()
    (client_dir / "index.html").mkdir()

    response = client_for(make_app(client_dir=client_dir)).get("/reconnect", headers=auth())

    assert response.status_code == 404


def test_the_manifest_and_script_types_do_not_depend_on_the_system_mime_table(
    client_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mimetypes

    monkeypatch.setattr(mimetypes, "guess_type", lambda *a, **k: ("application/x-wrong", None))
    client = client_for(make_app(client_dir=client_dir))

    manifest = client.get("/manifest.webmanifest", headers=auth())
    script = client.get("/assets/app-abc123.js", headers=auth())

    assert manifest.headers["content-type"].startswith("application/manifest+json")
    assert script.headers["content-type"].startswith("text/javascript")
