"""Ports: the interfaces core code uses to reach storage and the outside world.

Stages (``ingest``, ``extract``, ``rank``, ``publish``) and the CLI depend only on
these Protocols and on ``models.py``. Concrete adapters (SQLite, local
filesystem, Firestore, GCS, the ``httpx`` fetcher, ...) are chosen and wired
together at the entry point. Changing anything here is a contract change and
needs its own ``needs-human`` issue (see CLAUDE.md, rule 2).

Conventions (rationale in docs/adr/0001-ports-and-adapters.md):

* Every port is synchronous. Stages that need concurrency use a bounded
  ``ThreadPoolExecutor``, so ``Repository``, ``BlobStore`` and ``Fetcher``
  implementations must be safe to call from several threads at once.
* Ports exchange the Pydantic models from ``models.py``. Implementations return
  copies, so mutating a returned model never changes stored data without a
  further ``upsert``/``save`` call.
* The ``Repository`` is designed to the lowest common denominator of SQLite and
  Firestore: lookups by id, and listing with at most one equality filter plus a
  range on a single timestamp field.
* Article content lives in the ``BlobStore``; the ``Repository`` holds metadata
  and ``Article.content_ref``. Writes to the two are not atomic, so write the
  blob first and the article second.
* The Protocols are ``runtime_checkable`` for convenience in tests, but
  ``isinstance`` only checks that members exist. Conformance proper is checked
  statically by the type checker.

The ``Embedder`` port is deferred to Phase 4 and arrives through its own
contract-change issue.
"""

from __future__ import annotations

import re
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol, runtime_checkable

from newsfeed.models import (
    Article,
    ExtractionResult,
    ExtractionStatus,
    Feedback,
    FetchResult,
    RunStats,
    Source,
    SourceState,
    SourceType,
)

__all__ = [
    "BlobNotFoundError",
    "BlobStore",
    "Extractor",
    "FetchNotAllowedError",
    "Fetcher",
    "Repository",
    "SourceIngester",
    "validate_blob_key",
]


# --- Errors ------------------------------------------------------------------


class BlobNotFoundError(LookupError):
    """Raised by ``BlobStore.get`` when no blob exists for a ref."""


class FetchNotAllowedError(Exception):
    """Raised by ``Fetcher.permit`` when robots.txt forbids fetching a URL."""


# --- Repository --------------------------------------------------------------


@runtime_checkable
class Repository(Protocol):
    """Persist and query article metadata, source state, feedback and run stats.

    Implementations: SQLite (dev/CI), Firestore (prod), ``InMemoryRepository``
    (tests). All of them must pass the shared contract tests (#8).
    """

    def upsert_article(self, article: Article) -> None:
        """Insert ``article``, or replace the stored article with the same ``id``.

        The whole article is replaced, not merged field by field, and calling
        this twice with the same article is a no-op the second time.
        """
        ...

    def get_article(self, article_id: str) -> Article | None:
        """Return the article with ``article_id``, or None if there isn't one."""
        ...

    def list_articles(
        self,
        *,
        status: ExtractionStatus | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[Article]:
        """Return articles, newest ``fetched_at`` first (ties broken by ``id``).

        Args:
            status: only articles whose ``extraction.status`` equals this.
            since: only articles with ``fetched_at >= since``. Must be
                timezone-aware; a naive value raises ``ValueError``.
            limit: at most this many articles; None means no limit. Must be
                non-negative, otherwise ``ValueError`` is raised.
        """
        ...

    def delete_article(self, article_id: str) -> bool:
        """Delete the article with ``article_id``. Return True if one existed.

        Used when an article's canonical URL is refined after fetching and the
        article is re-created under its new id (#19). The article's blob, if
        any, is left for the caller to manage.
        """
        ...

    def get_source_state(self, source_id: str) -> SourceState | None:
        """Return the fetch state saved for ``source_id``, or None if none yet."""
        ...

    def save_source_state(self, state: SourceState) -> None:
        """Insert or replace the fetch state for ``state.source_id``."""
        ...

    def save_feedback(self, feedback: Feedback) -> None:
        """Insert or replace a feedback event, keyed by ``feedback.id``."""
        ...

    def list_feedback(self, since: datetime | None = None) -> list[Feedback]:
        """Return feedback with ``created_at >= since``, oldest first.

        ``since=None`` returns all feedback. A naive ``since`` raises
        ``ValueError``. Ties on ``created_at`` are broken by ``id``.
        """
        ...

    def save_run_stats(self, stats: RunStats) -> None:
        """Insert or replace run statistics, keyed by ``stats.run_id``.

        A run can be saved when it starts and again when it finishes.
        """
        ...

    def get_run_stats(self, run_id: str) -> RunStats | None:
        """Return the statistics saved for ``run_id``, or None if none."""
        ...


# --- BlobStore ---------------------------------------------------------------

_BLOB_KEY_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


def validate_blob_key(key: str) -> str:
    """Return ``key`` if it is a valid blob key, otherwise raise ``ValueError``.

    A key is a relative, ``/``-separated path such as ``articles/<id>.html``.
    Each segment uses only letters, digits, ``.``, ``_`` and ``-``, and ``.`` and
    ``..`` segments are rejected. That keeps keys portable between a local
    filesystem and object storage, and rules out path traversal. Every
    ``BlobStore`` implementation applies this check in ``put``.
    """
    segments = key.split("/")
    if not key or any(
        seg in {"", ".", ".."} or not _BLOB_KEY_SEGMENT.fullmatch(seg) for seg in segments
    ):
        raise ValueError(
            f"invalid blob key {key!r}: use a relative path of [A-Za-z0-9._-] segments "
            "separated by '/', with no empty, '.' or '..' segments"
        )
    return key


@runtime_checkable
class BlobStore(Protocol):
    """Store and retrieve opaque blobs: clean article HTML, images, ``feed.json``.

    ``put`` takes a key and returns a ref. The ref is an opaque string that is
    stable for the key within one store (``Article.content_ref`` holds it), and
    callers must pass refs, not keys, to ``get`` and ``exists``. Implementations:
    local filesystem (dev/CI), GCS (prod), ``InMemoryBlobStore`` (tests).
    """

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Store ``data`` under ``key``, replacing any existing blob, and return its ref.

        Raises ``ValueError`` if ``key`` fails ``validate_blob_key``. The write
        is atomic: a reader sees either the old blob or the new one in full.
        """
        ...

    def get(self, ref: str) -> bytes:
        """Return the blob for ``ref``. Raises ``BlobNotFoundError`` if missing."""
        ...

    def exists(self, ref: str) -> bool:
        """Return True if a blob exists for ``ref``."""
        ...


# --- Fetcher -----------------------------------------------------------------


@runtime_checkable
class Fetcher(Protocol):
    """All outbound HTTP, done politely (CLAUDE.md rule 6).

    Implementations apply robots.txt, per-host rate limits, an identifiable
    User-Agent, conditional GETs and retries. Implementations: ``HttpFetcher``
    (#9), ``FakeFetcher`` (tests).
    """

    def fetch(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> FetchResult:
        """GET ``url``, conditionally if ``etag`` or ``last_modified`` is given.

        Never raises for HTTP, network or robots.txt failures: the result has
        ``error`` set (and ``status_code`` None when there was no response).
        A 304 response gives ``not_modified=True`` and an empty body.
        """
        ...

    def is_allowed(self, url: str) -> bool:
        """Return True if robots.txt allows fetching ``url`` with our User-Agent."""
        ...

    def permit(self, url: str) -> AbstractContextManager[None]:
        """Apply the robots.txt check and rate limit for a request made elsewhere.

        For clients that don't go through ``fetch``, such as Playwright (#15)::

            with fetcher.permit(url):
                page.goto(url)

        Entering the context waits for the host's rate limit and raises
        ``FetchNotAllowedError`` if robots.txt forbids ``url``.
        """
        ...


# --- Extractor ---------------------------------------------------------------


@runtime_checkable
class Extractor(Protocol):
    """Turn a fetched page into an ``ExtractionResult``.

    Implementations: Trafilatura (#13), readability and newspaper4k fallbacks and
    the extractor chain (#14), Playwright (#15).
    """

    @property
    def name(self) -> str:
        """Extractor name recorded as ``ExtractionResult.method``, e.g. 'trafilatura'."""
        ...

    def extract(self, html: str, url: str) -> ExtractionResult:
        """Extract the main content of ``html``, which was fetched from ``url``.

        ``url`` is used to resolve relative links and for per-domain behaviour.
        Doesn't fetch anything itself (except a renderer going through
        ``Fetcher.permit``). Never raises for a bad page: failures are returned
        as status ``failed`` with ``error`` set.
        """
        ...


# --- SourceIngester ----------------------------------------------------------


@runtime_checkable
class SourceIngester(Protocol):
    """Turn one configured source into normalised articles.

    The pipeline keeps a registry of ingesters keyed by ``source_type``.
    Implementations: RSS/Atom (#10), Google News and Hacker News (#11).
    """

    @property
    def source_type(self) -> SourceType:
        """The kind of source this ingester handles."""
        ...

    def ingest(
        self, source: Source, fetcher: Fetcher, state: SourceState
    ) -> tuple[list[Article], SourceState]:
        """Fetch ``source`` through ``fetcher`` and return its articles and new state.

        ``state`` is the source's saved state, or a fresh
        ``SourceState(source_id=source.id)`` on the first run; the caller saves
        the returned state. Articles have extraction status ``pending`` and
        ``canonical_url`` equal to ``url`` (canonicalisation happens centrally).
        A 304 Not Modified yields no articles and the state unchanged. Entries
        that can't be turned into an ``Article`` are skipped and logged;
        anything else may raise and is caught per source by the pipeline.
        """
        ...
