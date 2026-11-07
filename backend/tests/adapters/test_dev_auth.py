"""Tests for ``DevTokenVerifier``, the local-development verifier (#175)."""

from __future__ import annotations

import pytest

from newsfeed.adapters.dev_auth import DevTokenVerifier
from newsfeed.models import VerifiedIdentity
from newsfeed.ports import TokenVerifier

OWNER_EMAIL = "owner@example.com"  # invented


def test_the_dev_verifier_accepts_any_token_as_the_owner() -> None:
    verifier = DevTokenVerifier(OWNER_EMAIL)
    assert isinstance(verifier, TokenVerifier)
    expected = VerifiedIdentity(email=OWNER_EMAIL, email_verified=True)
    assert verifier.verify("") == expected
    assert verifier.verify("anything") == expected


def test_the_dev_verifier_needs_an_owner_email() -> None:
    with pytest.raises(ValueError):
        DevTokenVerifier(" ")
