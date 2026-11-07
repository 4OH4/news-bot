"""The owner check that guards every route.

IAP (Identity-Aware Proxy) turns strangers away before the container, and signs
a JWT naming the caller in ``x-goog-iap-jwt-assertion``. This check verifies that
JWT again and admits only the owner, so the content stays private even if IAP is
switched off or opened to the wrong account. Every outcome but the owner fails
closed: 401 for a missing or rejected token, 403 for anyone else, and 503 when
the check can't be made at all (the client treats 401 as "Reconnect", which can't
fix an outage).
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException, Request, status

from newsfeed.logging import get_logger
from newsfeed.models import VerifiedIdentity
from newsfeed.ports import TokenRejectedError, TokenVerifier

__all__ = ["IAP_JWT_HEADER", "owner_check"]

IAP_JWT_HEADER = "x-goog-iap-jwt-assertion"

log = get_logger(__name__)


def owner_check(verifier: TokenVerifier, owner_email: str) -> Callable[[Request], VerifiedIdentity]:
    """A FastAPI dependency that returns the owner's identity or raises an HTTP error.

    Never logs the token: only the outcome and the path.
    """
    # ASCII lower-casing, not casefold: casefold maps some non-ASCII letters onto ASCII
    # ones (the Kelvin sign U+212A becomes "k"), so a lookalike address could match.
    # Check before lowering: the Kelvin sign lower-cases to an ASCII "k" too.
    if not owner_email.strip() or not owner_email.isascii():
        raise ValueError("owner_email must be a non-empty ASCII address")
    owner = owner_email.strip().lower()

    def require_owner(request: Request) -> VerifiedIdentity:
        # A missing header is checked like any other token, so the verifier decides.
        token = request.headers.get(IAP_JWT_HEADER, "")
        context = {"stage": "api", "path": request.url.path}
        try:
            identity = verifier.verify(token)
        except TokenRejectedError:
            log.info("api auth: token rejected", extra={**context, "outcome": "rejected"})
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in") from None
        except Exception as exc:
            # The type only: a library's message could quote what it was given.
            log.warning(
                "api auth: check could not be made",
                extra={**context, "outcome": "unavailable", "error_type": type(exc).__name__},
            )
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-in check unavailable; try again later"
            ) from None
        email = identity.email
        if not identity.email_verified or not email.isascii() or email.lower() != owner:
            log.info("api auth: not the owner", extra={**context, "outcome": "forbidden"})
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed")
        log.debug("api auth: owner", extra={**context, "outcome": "owner"})
        return identity

    return require_owner
