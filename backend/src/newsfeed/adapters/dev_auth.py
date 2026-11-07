"""A ``TokenVerifier`` for local development: every caller is the owner.

Used only with ``NEWSFEED_API_AUTH=none``, which settings refuse with cloud
storage and ``newsfeed serve`` binds to 127.0.0.1.
"""

from __future__ import annotations

from newsfeed.models import VerifiedIdentity

__all__ = ["DevTokenVerifier"]


class DevTokenVerifier:
    """Accepts any token, including an empty one, as ``owner_email``."""

    def __init__(self, owner_email: str) -> None:
        if not owner_email.strip():
            raise ValueError("DevTokenVerifier needs an owner email")
        self._identity = VerifiedIdentity(email=owner_email.strip(), email_verified=True)

    def verify(self, token: str) -> VerifiedIdentity:
        """Return the owner's identity, whatever ``token`` is."""
        return self._identity
