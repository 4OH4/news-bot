"""The owner-only API app: the feed, article HTML and the built client.

Every route sits behind the app-level owner check (``api.auth``), so a route
added later can't skip it. Content is read through the ``BlobStore`` port only;
the read routes need no repository read.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
from collections.abc import Sequence
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from newsfeed.api.auth import owner_check
from newsfeed.api.feedback import feedback_router
from newsfeed.api.headers import SecurityHeadersMiddleware
from newsfeed.api.local_safety import check_client_dir
from newsfeed.ports import BlobNotFoundError, BlobStore, Repository, TokenVerifier

__all__ = ["create_app"]

FEED_KEY = "feed.json"
API_CACHE_CONTROL = "private, no-cache"  # clients revalidate with the ETag
CLIENT_CACHE_CONTROL = "no-cache"
ASSET_CACHE_CONTROL = "private, max-age=31536000, immutable"  # hashed file names

# Matched with fullmatch: ``$`` alone would also accept a trailing newline.
_ARTICLE_ID = re.compile(r"[0-9a-f]{16}")
_MEDIA_TYPES = {".webmanifest": "application/manifest+json", ".js": "text/javascript"}


def create_app(
    blobs: BlobStore,
    repository: Repository,
    verifier: TokenVerifier,
    *,
    owner_email: str,
    client_dir: Path | None = None,
    allowed_hosts: Sequence[str] | None = None,
) -> FastAPI:
    """Build the API.

    ``repository`` is used only by the feedback route. ``client_dir`` is the built client;
    when None, every non-API path is 404. It must pass ``check_client_dir``, or
    ``UnsafeClientDirError`` is raised. ``allowed_hosts``, when given, are the only
    ``Host`` names answered (any port); others get 400. Local mode passes
    ``LOOPBACK_HOSTS``; behind IAP it is None.
    """
    if client_dir is not None and not client_dir.is_dir():
        raise ValueError(f"client_dir is not a directory: {client_dir}")
    root = check_client_dir(client_dir) if client_dir is not None else None

    app = FastAPI(
        title="newsfeed",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        dependencies=[Depends(owner_check(verifier, owner_email))],
    )
    if allowed_hosts is not None:
        # Added first so it sits inside the security headers: a 400 carries them too.
        app.add_middleware(
            TrustedHostMiddleware, allowed_hosts=list(allowed_hosts), www_redirect=False
        )
    app.add_middleware(SecurityHeadersMiddleware)
    app.state.repository = repository

    @app.get("/api/feed")
    def get_feed(request: Request) -> Response:
        try:
            data = blobs.get(blobs.ref_for(FEED_KEY))
        except BlobNotFoundError:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "No feed has been published yet"
            ) from None
        return _blob_response(request, data, "application/json")

    @app.get("/api/articles/{id}")
    def get_article(id: str, request: Request) -> Response:
        if not _ARTICLE_ID.fullmatch(id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Article not found")
        # A saved copy (#176) outlives the article blob's 30-day expiry.
        for key in (f"saved/{id}.html", f"articles/{id}.html"):
            try:
                data = blobs.get(blobs.ref_for(key))
            except BlobNotFoundError:
                continue
            return _blob_response(request, data, "text/html; charset=utf-8")
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Article not found")

    app.include_router(feedback_router(blobs, repository))

    # Registered last, so it only catches what the API routes don't.
    @app.get("/{path:path}")
    def get_client(path: str) -> Response:
        if root is None or path == "api" or path.startswith("api/"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
        return _client_response(root, path)

    return app


def _etag(data: bytes) -> str:
    """A strong ETag for ``data``."""
    return f'"{hashlib.sha256(data).hexdigest()}"'


def _matches(if_none_match: str | None, etag: str) -> bool:
    """Whether ``If-None-Match`` names ``etag`` (weak comparison, as RFC 9110 asks)."""
    if not if_none_match:
        return False
    tags = {tag.strip().removeprefix("W/") for tag in if_none_match.split(",")}
    return "*" in tags or etag in tags


def _blob_response(request: Request, data: bytes, media_type: str) -> Response:
    """``data`` with its ETag, or 304 if the client already has it."""
    etag = _etag(data)
    headers = {"ETag": etag, "Cache-Control": API_CACHE_CONTROL}
    if _matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(content=data, media_type=media_type, headers=headers)


def _client_response(root: Path, path: str) -> Response:
    """A file of the built client, or ``index.html`` so client-side routes load the app.

    Nothing outside ``root`` is served: ``..`` segments are refused, and the
    resolved path (symlinks followed) must stay inside ``root``.
    """
    if ".." in path.split("/") or "\x00" in path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if not candidate.is_file():
        candidate = (root / "index.html").resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    relative = candidate.relative_to(root)
    cache = ASSET_CACHE_CONTROL if relative.parts[0] == "assets" else CLIENT_CACHE_CONTROL
    media_type = _MEDIA_TYPES.get(candidate.suffix) or mimetypes.guess_type(candidate.name)[0]
    return FileResponse(
        candidate,
        media_type=media_type or "application/octet-stream",
        headers={"Cache-Control": cache},
    )
