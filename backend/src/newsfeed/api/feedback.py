"""POST /api/feedback: store one event, keep a liked article's HTML (#176).

Feedback can't be recreated, so an accepted event is saved before the answer
and the client's own event id is the key: a retry overwrites instead of
duplicating. The API never writes an ``Article`` row: the hourly run would
overwrite it. A like copies the article's HTML to ``saved/<id>.html`` instead,
which the 30-day expiry doesn't touch.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from newsfeed.logging import get_logger
from newsfeed.models import Feedback, FeedbackEvent, UtcDatetime
from newsfeed.ports import BlobNotFoundError, BlobStore, Repository

__all__ = ["MAX_BODY_BYTES", "FeedbackRequest", "feedback_router"]

MAX_BODY_BYTES = 4096
_REQUESTED_WITH = "XMLHttpRequest"

log = get_logger(__name__)


class FeedbackRequest(BaseModel):
    """One event as the client posts it (see ``docs/feedback.example.json``)."""

    model_config = ConfigDict(extra="forbid")

    # ``$`` in pydantic's regex engine matches only at the end of the string,
    # so a trailing newline is rejected (the tests pin this).
    id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    article_id: Annotated[str, Field(pattern=r"^[0-9a-f]{16}$")]
    event: FeedbackEvent
    value: Annotated[float | None, Field(ge=0, le=86400, allow_inf_nan=False)] = None
    created_at: UtcDatetime


def feedback_router(blobs: BlobStore, repository: Repository) -> APIRouter:
    """The feedback route. The app-level owner check guards it like every route."""
    router = APIRouter()

    @router.post("/api/feedback", status_code=status.HTTP_204_NO_CONTENT)
    async def post_feedback(request: Request) -> Response:
        # The guard comes first: a cross-site form can send neither header, so it
        # can't post with the owner's cookie. Nothing is parsed before it passes.
        if request.headers.get("x-requested-with") != _REQUESTED_WITH:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed")
        media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if media_type != "application/json":
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Send application/json")

        body = await _read_limited(request)
        try:
            parsed = FeedbackRequest.model_validate_json(body)
        except ValidationError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid event") from None
        feedback = Feedback(
            id=parsed.id,
            article_id=parsed.article_id,
            event=parsed.event,
            value=parsed.value,
            created_at=parsed.created_at,
        )
        try:
            repository.save_feedback(feedback)
        except Exception as exc:
            log.warning(
                "feedback not stored",
                extra={
                    "stage": "api",
                    "article_id": feedback.article_id,
                    "error_type": type(exc).__name__,
                },
            )
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "Could not store the event; try again"
            ) from None
        if feedback.event is FeedbackEvent.LIKE and feedback.value != 0:
            _keep_copy(blobs, feedback.article_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


async def _read_limited(request: Request) -> bytes:
    """The request body, refused with 413 once it passes ``MAX_BODY_BYTES``."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Body too large")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_BODY_BYTES:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Body too large")
    return bytes(data)


def _keep_copy(blobs: BlobStore, article_id: str) -> None:
    """Copy ``articles/<id>.html`` to ``saved/<id>.html`` unless that exists.

    The event is already stored, so no failure here changes the answer.
    """
    context = {"stage": "api", "article_id": article_id}
    try:
        if blobs.exists(blobs.ref_for(f"saved/{article_id}.html")):
            return
        data = blobs.get(blobs.ref_for(f"articles/{article_id}.html"))
        blobs.put(f"saved/{article_id}.html", data, "text/html")
    except BlobNotFoundError:
        # Normal for a link item, which has no stored page.
        log.info("like: no article blob to keep", extra=context)
    except Exception as exc:
        log.warning("like: saved copy failed", extra={**context, "error_type": type(exc).__name__})
