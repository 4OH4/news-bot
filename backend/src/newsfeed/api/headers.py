"""Security headers sent on every API response.

The values are the ones the client build declares in ``client/security-headers.json``
(#174); a test compares the two, so neither can drift.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = ["SECURITY_HEADERS", "SecurityHeadersMiddleware"]

SECURITY_HEADERS: Mapping[str, str] = MappingProxyType(
    {
        "Content-Security-Policy": (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src https: data:; "
            "connect-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'none'; "
            "frame-ancestors 'none'"
        ),
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
    }
)

_ENCODED = [
    (name.lower().encode("latin-1"), value.encode("latin-1"))
    for name, value in SECURITY_HEADERS.items()
]


class SecurityHeadersMiddleware:
    """ASGI middleware that sets ``SECURITY_HEADERS`` on every HTTP response, errors included."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Pass the request on, replacing any of our headers in the response start."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                names = {name for name, _ in _ENCODED}
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() not in names]
                message["headers"] = [*headers, *_ENCODED]
            await send(message)

        await self.app(scope, receive, send_with_headers)
