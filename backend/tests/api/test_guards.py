"""Guards around the API's construction and edge cases of its routes (#175)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tests.api.support import (
    RecordingBlobStore,
    auth,
    client_for,
    make_app,
)


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
