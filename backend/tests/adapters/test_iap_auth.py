"""Tests for ``IapJwtVerifier``: our checks around google-auth, never the library's own (#175).

``verify_token`` is injected, so nothing touches the network. Addresses are invented.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

import google.auth.exceptions
import google.auth.transport.requests
import google.oauth2.id_token
import pytest

from newsfeed.adapters import iap_auth
from newsfeed.adapters.iap_auth import IapJwtVerifier
from newsfeed.models import VerifiedIdentity
from newsfeed.ports import TokenRejectedError, TokenVerifier

AUDIENCE = "/projects/123456789/locations/europe-west2/services/reader-api"
IAP_ISSUER = "https://cloud.google.com/iap"
IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"
TOKEN = "header.payload.signature"


def claims(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "iss": IAP_ISSUER,
        "aud": AUDIENCE,
        "sub": "accounts.google.com:1000000000",
        "email": "reader@example.com",
        "iat": 1_790_000_000,
        "exp": 1_790_000_600,
    }
    base.update(changes)
    return {k: v for k, v in base.items() if v is not None}


class Library:
    """Stands in for ``google.oauth2.id_token.verify_token``: returns claims or raises."""

    def __init__(self, result: Mapping[str, Any] | Exception) -> None:
        self.result = result
        self.tokens: list[str] = []

    def __call__(self, token: str) -> Mapping[str, Any]:
        self.tokens.append(token)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_it_implements_the_token_verifier_port() -> None:
    assert isinstance(IapJwtVerifier(AUDIENCE, verify_token=Library(claims())), TokenVerifier)


def test_an_accepted_iap_token_gives_its_email_as_verified() -> None:
    library = Library(claims(email="reader@example.com"))

    identity = IapJwtVerifier(AUDIENCE, verify_token=library).verify(TOKEN)

    assert identity == VerifiedIdentity(email="reader@example.com", email_verified=True)
    assert library.tokens == [TOKEN]


@pytest.mark.parametrize(
    "error",
    [
        ValueError("Could not verify token signature."),
        ValueError("Token has wrong audience"),
        ValueError("Token expired"),
        ValueError("Wrong number of segments in token"),
        google.auth.exceptions.InvalidValue("Token used too early"),
        google.auth.exceptions.MalformedError("Can't parse segment"),
        google.auth.exceptions.GoogleAuthError("anything else"),
        TypeError("garbage"),
        KeyError("kid"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_any_rejection_by_the_library_is_token_rejected(error: Exception) -> None:
    verifier = IapJwtVerifier(AUDIENCE, verify_token=Library(error))

    with pytest.raises(TokenRejectedError):
        verifier.verify(TOKEN)


@pytest.mark.parametrize(
    "issuer",
    ["https://accounts.google.com", "accounts.google.com", "https://cloud.google.com/iap/", None],
)
def test_a_token_from_another_issuer_is_rejected(issuer: str | None) -> None:
    verifier = IapJwtVerifier(AUDIENCE, verify_token=Library(claims(iss=issuer)))

    with pytest.raises(TokenRejectedError):
        verifier.verify(TOKEN)


def test_a_token_with_no_email_is_rejected() -> None:
    verifier = IapJwtVerifier(AUDIENCE, verify_token=Library(claims(email=None)))

    with pytest.raises(TokenRejectedError):
        verifier.verify(TOKEN)


def test_an_empty_token_is_rejected_without_calling_the_library() -> None:
    library = Library(claims())

    with pytest.raises(TokenRejectedError):
        IapJwtVerifier(AUDIENCE, verify_token=library).verify("")
    assert library.tokens == []


def test_unreachable_keys_raise_connection_error_not_token_rejected() -> None:
    cause = google.auth.exceptions.TransportError("Could not fetch certificates")
    verifier = IapJwtVerifier(AUDIENCE, verify_token=Library(cause))

    with pytest.raises(ConnectionError) as raised:
        verifier.verify(TOKEN)

    assert not isinstance(raised.value, TokenRejectedError)
    assert raised.value.__cause__ is cause


def test_the_default_delegates_to_google_auth_with_the_audience_and_iap_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = google.oauth2.id_token.verify_token
    calls: list[inspect.BoundArguments] = []

    def recorder(*args: Any, **kwargs: Any) -> Mapping[str, Any]:
        calls.append(inspect.signature(real).bind(*args, **kwargs))
        return claims()

    monkeypatch.setattr(google.oauth2.id_token, "verify_token", recorder)
    # In case the adapter bound the name at import time.
    monkeypatch.setattr(iap_auth, "verify_token", recorder, raising=False)

    identity = IapJwtVerifier(AUDIENCE).verify(TOKEN)

    assert identity.email == "reader@example.com"
    assert len(calls) == 1
    bound = calls[0]
    bound.apply_defaults()
    assert bound.arguments["id_token"] == TOKEN
    assert isinstance(bound.arguments["request"], google.auth.transport.requests.Request)
    assert bound.arguments["audience"] == AUDIENCE
    assert bound.arguments["certs_url"] == IAP_CERTS_URL
