"""Guards around the API's construction and edge cases of its routes (#175)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import requests
from fastapi.testclient import TestClient

from newsfeed.adapters.dev_auth import DevTokenVerifier
from newsfeed.adapters.iap_auth import IapJwtVerifier
from newsfeed.models import VerifiedIdentity
from newsfeed.ports import TokenVerifier
from tests.api.support import (
    OWNER_EMAIL,
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


def test_the_iap_verifier_refuses_an_empty_audience() -> None:
    # google-auth skips the audience check when given none.
    with pytest.raises(ValueError):
        IapJwtVerifier("  ", verify_token=lambda token: {})


def test_a_requests_error_from_the_library_is_a_connection_error() -> None:
    def failing(token: str) -> dict[str, str]:
        raise requests.ConnectionError("dns")

    with pytest.raises(ConnectionError):
        IapJwtVerifier("/projects/1/x", verify_token=failing).verify("a.b.c")


def test_a_non_mapping_result_from_the_library_is_rejected() -> None:
    from newsfeed.ports import TokenRejectedError

    def odd(token: str) -> Any:
        return ["not", "claims"]

    verifier = IapJwtVerifier("/projects/1/x", verify_token=odd)
    with pytest.raises(TokenRejectedError):
        verifier.verify("a.b.c")


def test_the_dev_verifier_accepts_any_token_as_the_owner() -> None:
    verifier = DevTokenVerifier(OWNER_EMAIL)
    assert isinstance(verifier, TokenVerifier)
    expected = VerifiedIdentity(email=OWNER_EMAIL, email_verified=True)
    assert verifier.verify("") == expected
    assert verifier.verify("anything") == expected


def test_the_dev_verifier_needs_an_owner_email() -> None:
    with pytest.raises(ValueError):
        DevTokenVerifier(" ")


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
