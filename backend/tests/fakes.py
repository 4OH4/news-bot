"""In-memory fakes for every port in ``newsfeed.ports``.

Use these to test stages without real adapters or the network. They follow the
port contracts (including the shared contract tests in #8), are thread-safe
where the real adapters must be, and record their calls so tests can assert on
them. Stored and returned models are copies, as with a real store.

The ``TYPE_CHECKING`` block at the bottom makes mypy check that each fake
conforms to its Protocol; this module is listed in ``[tool.mypy] files``.
"""

from __future__ import annotations

import mimetypes
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

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
from newsfeed.ports import (
    BlobNotFoundError,
    FetchNotAllowedError,
    validate_blob_key,
)

if TYPE_CHECKING:
    from newsfeed.ports import BlobStore, Extractor, Fetcher, Repository, SourceIngester

__all__ = [
    "FakeExtractor",
    "FakeFetcher",
    "FakeResponse",
    "FakeSourceIngester",
    "FetchCall",
    "InMemoryBlobStore",
    "InMemoryRepository",
]


def _require_aware(value: datetime | None, name: str) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{name} must be timezone-aware")


# --- Repository --------------------------------------------------------------


class InMemoryRepository:
    """A ``Repository`` backed by dicts, guarded by a lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._articles: dict[str, Article] = {}
        self._source_states: dict[str, SourceState] = {}
        self._feedback: dict[str, Feedback] = {}
        self._run_stats: dict[str, RunStats] = {}

    def upsert_article(self, article: Article) -> None:
        """Insert or replace ``article`` by id."""
        with self._lock:
            self._articles[article.id] = article.model_copy(deep=True)

    def get_article(self, article_id: str) -> Article | None:
        """Return a copy of the article with ``article_id``, or None."""
        with self._lock:
            article = self._articles.get(article_id)
            return article.model_copy(deep=True) if article else None

    def list_articles(
        self,
        *,
        status: ExtractionStatus | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[Article]:
        """Return matching articles, newest ``fetched_at`` first."""
        _require_aware(since, "since")
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")
        with self._lock:
            matches = [
                a
                for a in self._articles.values()
                if (status is None or a.extraction.status == status)
                and (since is None or a.fetched_at >= since)
            ]
            # Two stable sorts: newest first, ties broken by ascending id.
            matches.sort(key=lambda a: a.id)
            matches.sort(key=lambda a: a.fetched_at, reverse=True)
            if limit is not None:
                matches = matches[:limit]
            return [a.model_copy(deep=True) for a in matches]

    def delete_article(self, article_id: str) -> bool:
        """Delete the article with ``article_id``; True if it existed."""
        with self._lock:
            return self._articles.pop(article_id, None) is not None

    def get_source_state(self, source_id: str) -> SourceState | None:
        """Return a copy of the saved state for ``source_id``, or None."""
        with self._lock:
            state = self._source_states.get(source_id)
            return state.model_copy(deep=True) if state else None

    def save_source_state(self, state: SourceState) -> None:
        """Insert or replace the state for ``state.source_id``."""
        with self._lock:
            self._source_states[state.source_id] = state.model_copy(deep=True)

    def save_feedback(self, feedback: Feedback) -> None:
        """Insert or replace a feedback event by id."""
        with self._lock:
            self._feedback[feedback.id] = feedback.model_copy(deep=True)

    def list_feedback(self, since: datetime | None = None) -> list[Feedback]:
        """Return feedback created at or after ``since``, oldest first."""
        _require_aware(since, "since")
        with self._lock:
            matches = [f for f in self._feedback.values() if since is None or f.created_at >= since]
            matches.sort(key=lambda f: (f.created_at, f.id))
            return [f.model_copy(deep=True) for f in matches]

    def save_run_stats(self, stats: RunStats) -> None:
        """Insert or replace run statistics by run id."""
        with self._lock:
            self._run_stats[stats.run_id] = stats.model_copy(deep=True)

    def get_run_stats(self, run_id: str) -> RunStats | None:
        """Return a copy of the statistics for ``run_id``, or None."""
        with self._lock:
            stats = self._run_stats.get(run_id)
            return stats.model_copy(deep=True) if stats else None

    def list_run_stats(
        self, *, since: datetime | None = None, limit: int | None = None
    ) -> list[RunStats]:
        """Return matching run statistics, newest ``started_at`` first."""
        _require_aware(since, "since")
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")
        with self._lock:
            matches = [
                s for s in self._run_stats.values() if since is None or s.started_at >= since
            ]
            # Two stable sorts: newest first, ties broken by ascending run_id.
            matches.sort(key=lambda s: s.run_id)
            matches.sort(key=lambda s: s.started_at, reverse=True)
            if limit is not None:
                matches = matches[:limit]
            return [s.model_copy(deep=True) for s in matches]


# --- BlobStore ---------------------------------------------------------------


class InMemoryBlobStore:
    """A ``BlobStore`` backed by a dict.

    Refs are ``memory://<key>``, deliberately different from the key, so a test
    fails if code passes a key where the port expects a ref.
    """

    PREFIX = "memory://"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._blobs: dict[str, tuple[bytes, str]] = {}

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Store ``data`` under ``key`` and return its ref."""
        validate_blob_key(key)
        ref = f"{self.PREFIX}{key}"
        with self._lock:
            self._blobs[ref] = (bytes(data), content_type)
        return ref

    def get(self, ref: str) -> bytes:
        """Return the blob for ``ref``, or raise ``BlobNotFoundError``."""
        with self._lock:
            try:
                return self._blobs[ref][0]
            except KeyError:
                raise BlobNotFoundError(ref) from None

    def exists(self, ref: str) -> bool:
        """Return True if a blob is stored for ``ref``."""
        with self._lock:
            return ref in self._blobs

    def content_type(self, ref: str) -> str:
        """Test helper: the content type the blob for ``ref`` was stored with."""
        with self._lock:
            try:
                return self._blobs[ref][1]
            except KeyError:
                raise BlobNotFoundError(ref) from None

    def keys(self) -> list[str]:
        """Test helper: the keys of every stored blob, sorted."""
        with self._lock:
            return sorted(ref.removeprefix(self.PREFIX) for ref in self._blobs)


# --- Fetcher -----------------------------------------------------------------


@dataclass(frozen=True)
class FakeResponse:
    """A canned response served by ``FakeFetcher``.

    Set ``etag`` or ``last_modified`` to have the fetcher answer a matching
    conditional request with 304 Not Modified; ``status=304`` always serves
    Not Modified, with an empty body. Set ``error`` (usually with
    ``status=None``) to simulate a network failure.
    """

    body: bytes = b""
    status: int | None = 200
    headers: Mapping[str, str] = field(default_factory=dict)
    etag: str | None = None
    last_modified: str | None = None
    final_url: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class FetchCall:
    """One call to ``FakeFetcher.fetch``, recorded for assertions."""

    url: str
    etag: str | None
    last_modified: str | None


class FakeFetcher:
    """A ``Fetcher`` that serves canned responses, with no network.

    Responses come from a dict (``responses`` or ``add``) and, for URLs not
    registered, from ``fixtures_dir`` laid out as ``<host>/<path>``: e.g.
    ``https://example.com/feeds/main.xml`` is served from
    ``fixtures_dir/example.com/feeds/main.xml``, and a path ending in ``/`` from
    its ``index.html``. Query strings aren't mapped; register those explicitly.
    Unknown URLs get a 404 result.

    URLs starting with any prefix in ``disallowed`` are treated as forbidden by
    robots.txt. Every ``fetch`` and ``permit`` call is recorded in ``calls`` and
    ``permits``.
    """

    def __init__(
        self,
        responses: Mapping[str, FakeResponse | bytes | str] | None = None,
        *,
        fixtures_dir: Path | None = None,
        disallowed: Iterable[str] = (),
    ) -> None:
        self._lock = threading.Lock()
        self._responses: dict[str, FakeResponse] = {}
        self.fixtures_dir = fixtures_dir
        self.disallowed: list[str] = list(disallowed)
        self.calls: list[FetchCall] = []
        self.permits: list[str] = []
        for url, response in (responses or {}).items():
            self.add(url, response)

    def add(
        self,
        url: str,
        response: FakeResponse | bytes | str = b"",
        *,
        status: int | None = 200,
        headers: Mapping[str, str] | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
        final_url: str | None = None,
        error: str | None = None,
    ) -> None:
        """Register a canned response for ``url``.

        Pass a ``FakeResponse``, or a body (``str`` is UTF-8 encoded) plus the
        keyword arguments, which are ignored when a ``FakeResponse`` is given.
        """
        if not isinstance(response, FakeResponse):
            body = response.encode("utf-8") if isinstance(response, str) else response
            response = FakeResponse(
                body=body,
                status=status,
                headers=dict(headers or {}),
                etag=etag,
                last_modified=last_modified,
                final_url=final_url,
                error=error,
            )
        with self._lock:
            self._responses[url] = response

    def add_file(
        self,
        url: str,
        path: Path | str,
        *,
        status: int | None = 200,
        headers: Mapping[str, str] | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
        final_url: str | None = None,
        error: str | None = None,
    ) -> None:
        """Register the contents of ``path`` as the response for ``url``.

        A relative ``path`` is resolved against ``fixtures_dir``. The
        ``content-type`` header is guessed from the file extension unless given
        in ``headers``. Other keyword arguments are as for ``add``.
        """
        file_path = self._resolve(Path(path))
        merged = {"content-type": _guess_content_type(file_path)}
        merged.update({k.lower(): v for k, v in (headers or {}).items()})
        self.add(
            url,
            file_path.read_bytes(),
            status=status,
            headers=merged,
            etag=etag,
            last_modified=last_modified,
            final_url=final_url,
            error=error,
        )

    def fetch(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> FetchResult:
        """Serve the canned response for ``url``, honouring conditional headers."""
        with self._lock:
            self.calls.append(FetchCall(url=url, etag=etag, last_modified=last_modified))
        if not self.is_allowed(url):
            return FetchResult(url=url, final_url=url, error="disallowed by robots.txt")
        response = self._lookup(url)
        if response is None:
            return FetchResult(
                url=url, final_url=url, status_code=404, error="HTTP 404 (no canned response)"
            )
        final_url = response.final_url or url
        headers = dict(response.headers)
        if response.etag is not None:
            headers["etag"] = response.etag
        if response.last_modified is not None:
            headers["last-modified"] = response.last_modified
        if response.error is not None and response.status is None:
            return FetchResult(url=url, final_url=final_url, error=response.error)
        # A canned 304 is treated like a matched conditional GET, as the port
        # requires: not_modified=True and an empty body.
        if response.status == 304 or (
            response.status == 200 and _is_not_modified(response, etag, last_modified)
        ):
            return FetchResult(
                url=url, final_url=final_url, status_code=304, headers=headers, not_modified=True
            )
        error = response.error
        if error is None and response.status is not None and response.status >= 400:
            error = f"HTTP {response.status}"
        return FetchResult(
            url=url,
            final_url=final_url,
            status_code=response.status,
            headers=headers,
            content=response.body,
            error=error,
        )

    def is_allowed(self, url: str) -> bool:
        """Return False for URLs under a ``disallowed`` prefix."""
        return not any(url.startswith(prefix) for prefix in self.disallowed)

    @contextmanager
    def permit(self, url: str) -> Iterator[None]:
        """Record the permit and raise ``FetchNotAllowedError`` if disallowed."""
        with self._lock:
            self.permits.append(url)
        if not self.is_allowed(url):
            raise FetchNotAllowedError(url)
        yield

    def fetched_urls(self) -> list[str]:
        """Test helper: the URLs passed to ``fetch``, in call order."""
        with self._lock:
            return [call.url for call in self.calls]

    def _lookup(self, url: str) -> FakeResponse | None:
        with self._lock:
            response = self._responses.get(url)
        if response is not None or self.fixtures_dir is None:
            return response
        parts = urlsplit(url)
        if parts.query or not parts.hostname:
            return None
        relative = parts.path.lstrip("/")
        if relative == "" or relative.endswith("/"):
            relative += "index.html"
        # Serve only files inside this host's directory, never via '..'.
        host_dir = (self.fixtures_dir / parts.hostname).resolve()
        candidate = (host_dir / relative).resolve()
        if (
            not host_dir.is_relative_to(self.fixtures_dir.resolve())
            or not candidate.is_relative_to(host_dir)
            or not candidate.is_file()
        ):
            return None
        return FakeResponse(
            body=candidate.read_bytes(), headers={"content-type": _guess_content_type(candidate)}
        )

    def _resolve(self, path: Path) -> Path:
        if path.is_absolute():
            return path
        if self.fixtures_dir is None:
            raise ValueError(f"relative path {path} needs fixtures_dir")
        return self.fixtures_dir / path


def _guess_content_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _is_not_modified(response: FakeResponse, etag: str | None, last_modified: str | None) -> bool:
    """Mirror HTTP: If-None-Match takes precedence over If-Modified-Since."""
    if etag is not None and response.etag is not None:
        return etag == response.etag
    return (
        last_modified is not None
        and response.last_modified is not None
        and last_modified == response.last_modified
    )


# --- Extractor ---------------------------------------------------------------


class FakeExtractor:
    """An ``Extractor`` that returns canned results by URL.

    URLs without a canned result get ``default``, or a ``failed`` result if no
    default is given. ``method`` is filled in with ``name`` when unset. Calls
    are recorded in ``calls`` as ``(url, html)`` pairs.
    """

    def __init__(
        self,
        name: str = "fake",
        results: Mapping[str, ExtractionResult] | None = None,
        *,
        default: ExtractionResult | None = None,
    ) -> None:
        self.name = name
        self._results = dict(results or {})
        self._default = default
        self._lock = threading.Lock()
        self.calls: list[tuple[str, str]] = []

    def extract(self, html: str, url: str) -> ExtractionResult:
        """Return the canned result for ``url``."""
        with self._lock:
            self.calls.append((url, html))
        result = self._results.get(url, self._default)
        if result is None:
            return ExtractionResult(
                status=ExtractionStatus.FAILED, method=self.name, error="no canned result"
            )
        if result.method is None:
            return ExtractionResult.model_validate({**result.model_dump(), "method": self.name})
        return result.model_copy(deep=True)


# --- SourceIngester ----------------------------------------------------------


class FakeSourceIngester:
    """A ``SourceIngester`` that returns canned articles per source id.

    It doesn't fetch. The returned state is the input state with
    ``last_fetched_at`` set from ``clock`` and ``last_status`` set to 200. A
    source id in ``errors`` raises that exception instead, to test per-source
    error handling. Calls are recorded in ``calls`` as source ids.
    """

    def __init__(
        self,
        source_type: SourceType = SourceType.RSS,
        articles: Mapping[str, Sequence[Article]] | None = None,
        *,
        errors: Mapping[str, Exception] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.source_type = source_type
        self._articles = {k: list(v) for k, v in (articles or {}).items()}
        self._errors = dict(errors or {})
        self._clock = clock
        self._lock = threading.Lock()
        self.calls: list[str] = []

    def ingest(
        self, source: Source, fetcher: Fetcher, state: SourceState
    ) -> tuple[list[Article], SourceState]:
        """Return the canned articles for ``source.id`` and an updated state."""
        with self._lock:
            self.calls.append(source.id)
        if source.id in self._errors:
            raise self._errors[source.id]
        articles = [a.model_copy(deep=True) for a in self._articles.get(source.id, [])]
        new_state = SourceState.model_validate(
            {**state.model_dump(), "last_fetched_at": self._clock(), "last_status": 200}
        )
        return articles, new_state


# --- Static conformance checks -------------------------------------------------

if TYPE_CHECKING:
    _repository: Repository = InMemoryRepository()
    _blob_store: BlobStore = InMemoryBlobStore()
    _fetcher: Fetcher = FakeFetcher()
    _extractor: Extractor = FakeExtractor()
    _ingester: SourceIngester = FakeSourceIngester()
