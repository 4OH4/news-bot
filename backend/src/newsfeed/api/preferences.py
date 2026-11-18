"""GET and PUT /api/preferences, and the reader's preferences applied to served views (#337).

The owner's Hide, Fewer, Show and More choices (ADR-0016) are applied as each view
is served, not when the pipeline ranks, so a change shows on the next refresh.
``PreferencesService`` holds the validated defaults and a short in-memory cache of
the stored choices: one repository read per request at most, and none while the
cache is fresh. A PUT goes straight to the repository for its version check and
replaces the cache with what it saved.

The choices can't be recreated, so the only write path checks the stored
``version`` before saving: a stale copy from another device gets 409, not a save.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from newsfeed.api.ranking import ItemAdjuster, no_adjustment
from newsfeed.logging import get_logger
from newsfeed.models import DEFAULT_USER_ID, PreferenceState, ReaderPreferences, VerifiedIdentity
from newsfeed.ports import Repository
from newsfeed.preferences import PreferencesConfig, effective_states, item_effect, menu

__all__ = [
    "CACHE_SECONDS",
    "MAX_BODY_BYTES",
    "PreferencesRequest",
    "PreferencesService",
    "preferences_router",
    "user_id_for",
]

MAX_BODY_BYTES = 16 * 1024
CACHE_SECONDS = 30.0
_REQUESTED_WITH = "XMLHttpRequest"
_NO_PREFERENCES_VERSION = -1  # the ranked-cache key when nothing can be applied

log = get_logger(__name__)


def user_id_for(identity: VerifiedIdentity) -> str:
    """The user id the preferences of ``identity`` are stored under.

    The owner check has already verified the owner, the only user today, so this is
    always ``DEFAULT_USER_ID``; adding a user changes this one function. The email
    is never stored or logged.
    """
    del identity  # one user for now
    return DEFAULT_USER_ID


class PreferencesRequest(BaseModel):
    """A PUT body: the reader's full set of choices and the version they were made on.

    A source or topic left out is Show. Strict: a string version or an unknown key
    (such as ``regions``, which this version doesn't have) is a client bug.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    version: Annotated[int, Field(ge=0)]
    sources: dict[str, PreferenceState]
    topics: dict[str, PreferenceState]
    setup_done: bool


class PreferencesService:
    """The defaults, plus a 30-second cache of the stored choices for serving views."""

    def __init__(
        self,
        config: PreferencesConfig,
        repository: Repository,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._repository = repository
        self._clock = clock
        self._lock = threading.Lock()  # sync routes run in a thread pool
        self._cached: tuple[float, ReaderPreferences | None] | None = None
        self._generation = 0  # bumped on each save, so a slow read can't refill stale data

    def stored(self) -> ReaderPreferences | None:
        """The owner's stored choices, from the cache when it is under 30 seconds old.

        Raises whatever the repository raises.
        """
        with self._lock:
            if self._cached is not None and self._clock() - self._cached[0] < CACHE_SECONDS:
                return self._cached[1]
            generation = self._generation
        prefs = self._repository.get_preferences(DEFAULT_USER_ID)
        with self._lock:
            if generation == self._generation:
                self._cached = (self._clock(), prefs)
        return prefs

    def remember(self, prefs: ReaderPreferences) -> None:
        """Replace the cache with what was just saved."""
        with self._lock:
            self._generation += 1
            self._cached = (self._clock(), prefs)

    def adjuster(self) -> tuple[int, ItemAdjuster]:
        """(preferences version, adjuster) for one request.

        If the stored choices can't be read, the view is served unadjusted rather
        than not at all: a hidden source showing briefly is cheap, an empty feed isn't.
        """
        try:
            prefs = self.stored()
        except Exception as exc:
            log.warning(
                "preferences not read; serving the view unadjusted",
                extra={"stage": "api", "error_type": type(exc).__name__},
            )
            return _NO_PREFERENCES_VERSION, no_adjustment
        states = effective_states(self.config, prefs)
        config = self.config

        def adjust(item: Mapping[str, Any]) -> tuple[bool, Mapping[str, float]]:
            try:
                return item_effect(item, states, config)
            except (AttributeError, TypeError, ValueError, ZeroDivisionError):
                # A malformed item is left as published; rank_view rejects a bad score.
                return False, {}

        return (prefs.version if prefs is not None else 0), adjust

    def body(self, prefs: ReaderPreferences | None) -> dict[str, Any]:
        """The GET body for ``prefs`` (None: nothing stored yet)."""
        states = effective_states(self.config, prefs)
        return {
            "version": prefs.version if prefs is not None else 0,
            "setup_done": prefs is not None and prefs.setup_done_at is not None,
            "menu": menu(self.config, states).model_dump(mode="json"),
        }


def preferences_router(service: PreferencesService | None, repository: Repository) -> APIRouter:
    """The preferences routes; 503 when no ``preferences.yaml`` was configured.

    The app-level owner check guards them like every route.
    """
    router = APIRouter()
    save_lock = asyncio.Lock()  # one read-check-write at a time in this process

    def configured() -> PreferencesService:
        if service is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Preferences not configured")
        return service

    def unavailable() -> HTTPException:
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Preferences can't be read")

    @router.get("/api/preferences")
    async def get_preferences() -> dict[str, Any]:
        prefs_service = configured()
        try:
            prefs = await run_in_threadpool(repository.get_preferences, DEFAULT_USER_ID)
        except Exception as exc:
            log.warning("preferences not read", extra=_error_context(exc))
            raise unavailable() from None
        return prefs_service.body(prefs)

    @router.put("/api/preferences")
    async def put_preferences(request: Request) -> Response:
        # The guard comes first, as for feedback: a cross-site form can send neither
        # header, so it can't write with the owner's cookie. Nothing is parsed before.
        if request.headers.get("x-requested-with") != _REQUESTED_WITH:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed")
        media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if media_type != "application/json":
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Send application/json")
        prefs_service = configured()
        raw = await _read_limited(request)
        try:
            parsed = PreferencesRequest.model_validate_json(raw)
        except ValidationError:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid preferences"
            ) from None
        _check_ids(parsed, prefs_service.config)

        async with save_lock:
            try:
                current = await run_in_threadpool(repository.get_preferences, DEFAULT_USER_ID)
            except Exception as exc:
                log.warning("preferences not read", extra=_error_context(exc))
                raise unavailable() from None
            stored_version = current.version if current is not None else 0
            if parsed.version != stored_version:
                # A stale copy (another device saved since): send what is stored now.
                return JSONResponse(
                    prefs_service.body(current), status_code=status.HTTP_409_CONFLICT
                )
            saved = _next(parsed, current, prefs_service.config)
            try:
                await run_in_threadpool(repository.save_preferences, saved)
            except Exception as exc:
                log.warning("preferences not stored", extra=_error_context(exc))
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE, "Could not store preferences; try again"
                ) from None
            prefs_service.remember(saved)
        log.info("preferences saved", extra={"stage": "api", "version": saved.version})
        return JSONResponse(prefs_service.body(saved))

    return router


def _error_context(exc: Exception) -> dict[str, str]:
    return {"stage": "api", "error_type": type(exc).__name__}


def _check_ids(parsed: PreferencesRequest, config: PreferencesConfig) -> None:
    """422 unless every source and topic named is one the menu offers."""
    unknown_sources = set(parsed.sources) - set(config.source_order)
    unknown_topics = set(parsed.topics) - set(config.topics)
    if unknown_sources or unknown_topics:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown source or topic")


def _overrides(
    sent: Mapping[str, PreferenceState],
    stored: Mapping[str, PreferenceState],
    offered: set[str],
) -> dict[str, PreferenceState]:
    """The overrides to store: what was sent, minus Show, plus stored ones not offered.

    A source dropped from the config (disabled for a while, say) isn't in the menu,
    so the client can't send it; its stored choice is kept rather than lost.
    """
    kept = {key: state for key, state in stored.items() if key not in offered}
    return kept | {key: state for key, state in sent.items() if state is not PreferenceState.SHOW}


def _next(
    parsed: PreferencesRequest, current: ReaderPreferences | None, config: PreferencesConfig
) -> ReaderPreferences:
    """The preferences to save for an accepted PUT on top of ``current``."""
    now = datetime.now(UTC)
    setup_done_at = current.setup_done_at if current is not None else None
    if setup_done_at is None and parsed.setup_done:
        setup_done_at = now
    return ReaderPreferences(
        user_id=DEFAULT_USER_ID,
        sources=_overrides(
            parsed.sources, current.sources if current else {}, set(config.source_order)
        ),
        topics=_overrides(parsed.topics, current.topics if current else {}, set(config.topics)),
        setup_done_at=setup_done_at,
        updated_at=now,
        version=(current.version if current is not None else 0) + 1,
    )


async def _read_limited(request: Request) -> bytes:
    """The request body, refused with 413 once it passes ``MAX_BODY_BYTES``.

    The same as feedback's reader with a larger limit; kept here so that route,
    which is outside this change, isn't touched.
    """
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Body too large")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_BODY_BYTES:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Body too large")
    return bytes(data)
