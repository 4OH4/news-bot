"""Verify IAP's signed header (``x-goog-iap-jwt-assertion``) with google-auth.

Google's library does the work: it fetches IAP's public keys and checks the ES256
signature, audience and expiry. This adapter adds only what the library leaves
out (the issuer, and that an email is present) and sorts its failures into
"rejected" and "couldn't check". The project writes no HTTP, key fetching or
signature checks of its own. See Google's "Securing your app with signed headers".
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import google.auth.exceptions
import google.auth.transport.requests
import requests
from google.oauth2 import id_token

from newsfeed.models import VerifiedIdentity
from newsfeed.ports import TokenRejectedError

__all__ = ["IAP_CERTS_URL", "IAP_ISSUER", "IapJwtVerifier"]

IAP_ISSUER = "https://cloud.google.com/iap"
IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"
CLOCK_SKEW_SECONDS = 10


def _google_verifier(audience: str) -> Callable[[str], Mapping[str, Any]]:
    """The default ``verify_token``: google-auth against IAP's published keys."""

    def verify(token: str) -> Mapping[str, Any]:
        # A session per call: requests.Session isn't documented as thread-safe.
        with requests.Session() as session:
            claims: Mapping[str, Any] = id_token.verify_token(
                token,
                google.auth.transport.requests.Request(session=session),
                audience=audience,
                certs_url=IAP_CERTS_URL,
                # The library allows no clock skew by default; an instance clock a
                # second behind IAP's would turn the owner away with a 401.
                clock_skew_in_seconds=CLOCK_SKEW_SECONDS,
            )
        return claims

    return verify


class IapJwtVerifier:
    """A ``TokenVerifier`` for IAP's JWT, delegating the checks to google-auth.

    ``verify_token`` takes the raw token and returns its claims, raising for any
    token it can't accept; tests inject a stand-in so nothing touches the network.
    """

    def __init__(
        self, audience: str, *, verify_token: Callable[[str], Mapping[str, Any]] | None = None
    ) -> None:
        # google-auth skips the audience check when given none: refuse to build that.
        if not audience.strip():
            raise ValueError("IapJwtVerifier needs an audience")
        self._verify_token = verify_token or _google_verifier(audience)

    def verify(self, token: str) -> VerifiedIdentity:
        """Return the identity IAP signed for, or raise.

        Raises ``TokenRejectedError`` for an empty, invalid, expired or wrongly
        issued token, and ``ConnectionError`` when IAP's keys can't be fetched.
        """
        if not token:
            raise TokenRejectedError("no IAP assertion")
        try:
            claims = self._verify_token(token)
        except (google.auth.exceptions.TransportError, requests.RequestException) as exc:
            raise ConnectionError("could not fetch IAP's public keys") from exc
        except Exception as exc:
            raise TokenRejectedError("IAP assertion rejected") from exc
        if not isinstance(claims, Mapping) or claims.get("iss") != IAP_ISSUER:
            raise TokenRejectedError("IAP assertion has the wrong issuer")
        email = claims.get("email")
        if not isinstance(email, str) or not email:
            raise TokenRejectedError("IAP assertion has no email")
        # IAP admits only signed-in Google accounts, and its JWT has no email_verified claim.
        return VerifiedIdentity(email=email, email_verified=True)
