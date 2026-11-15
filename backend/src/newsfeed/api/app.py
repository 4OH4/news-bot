"""The owner-only API app: the feed, its views in pages, article HTML and the built client.

Every route sits behind the app-level owner check (``api.auth``), so a route
added later can't skip it. Content is read through the ``BlobStore`` port only;
the read routes need no repository read.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import mimetypes
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
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

SCHEMA_VERSION = 1
PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 100

# Matched with fullmatch: ``$`` alone would also accept a trailing newline.
_ARTICLE_ID = re.compile(r"[0-9a-f]{16}")
_VIEW = re.compile(r"[a-z0-9-]+")
_CURSOR = re.compile(r"[A-Za-z0-9_-]{1,200}")
_CURSOR_BODY = re.compile(r"([A-Za-z0-9._-]+):([0-9]{1,9})")
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

    def no_feed_yet() -> HTTPException:
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No feed has been published yet")

    @app.get("/api/feed")
    def get_feed(
        request: Request,
        view: str | None = None,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = PAGE_LIMIT,
        cursor: str | None = None,
    ) -> Response:
        if view is None:
            try:
                data = blobs.get(blobs.ref_for(FEED_KEY))
            except BlobNotFoundError:
                raise no_feed_yet() from None
            return _blob_response(request, data, "application/json")
        if not _VIEW.fullmatch(view):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "View not found")
        offset = _cursor_offset(cursor) if cursor is not None else 0
        try:
            stored = blobs.get(blobs.ref_for(f"views/{view}.json"))
        except BlobNotFoundError:
            if not blobs.exists(blobs.ref_for(FEED_KEY)):
                raise no_feed_yet() from None
            raise HTTPException(status.HTTP_404_NOT_FOUND, "View not found") from None
        page = _page(view, stored, offset, limit)
        data = json.dumps(page, ensure_ascii=False, allow_nan=False).encode("utf-8")
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


def _cursor(run_id: str, offset: int) -> str:
    """An opaque cursor: base64url of ``<run_id>:<offset>``, unpadded."""
    return base64.urlsafe_b64encode(f"{run_id}:{offset}".encode()).decode().rstrip("=")


def _cursor_offset(cursor: str) -> int:
    """The offset a cursor names; 400 if it isn't one of ours.

    Its run id isn't checked: after a new run the same offset of the new list is
    served, and the page's ``run_id`` tells the client the list changed (ADR-0012).
    """
    bad = HTTPException(status.HTTP_400_BAD_REQUEST, "Malformed cursor")
    if not _CURSOR.fullmatch(cursor):
        raise bad
    try:
        body = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("ascii")
    except (binascii.Error, ValueError):
        raise bad from None
    match = _CURSOR_BODY.fullmatch(body)
    if match is None:
        raise bad
    return int(match.group(2))


def _page(view: str, stored: bytes, offset: int, limit: int) -> dict[str, Any]:
    """One page of a stored view file, in the ``feed.json`` envelope."""
    try:
        document = json.loads(stored)
        run_id, items, generated_at = (
            document["run_id"],
            document["items"],
            document["generated_at"],
        )
        if not (isinstance(run_id, str) and isinstance(items, list)):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "View can't be read") from None
    end = offset + limit
    next_cursor = _cursor(run_id, end) if end < len(items) else None
    page = {"schema_version": SCHEMA_VERSION, "generated_at": generated_at, "run_id": run_id}
    return page | {"view": view, "next_cursor": next_cursor, "items": items[offset:end]}


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
