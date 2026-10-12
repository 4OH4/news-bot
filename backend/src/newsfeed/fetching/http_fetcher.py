"""``HttpFetcher``: the ``httpx``-based implementation of the ``Fetcher`` port.

Politeness rules (PROJECT_PLAN.md NFR-3, CLAUDE.md rule 6), applied to every
request including each redirect hop:

* an identifiable User-Agent;
* robots.txt, fetched once per origin per process. A 2xx is parsed, any other
  4xx means "allow all", and a 429, 5xx or network failure means "disallow the
  whole origin" for the rest of the run;
* a minimum interval between requests to the same host (default 2s, overridable
  per host), shared by all threads;
* conditional GETs (``If-None-Match`` / ``If-Modified-Since``);
* bounded retries with exponential backoff and jitter on connection errors,
  timeouts, 429 and 502-504, honouring ``Retry-After`` up to a cap.

``fetch`` never raises for HTTP, network or robots.txt failures; it returns a
``FetchResult`` with ``error`` set. ``clock`` and ``sleep`` are injectable so
tests run instantly, and ``transport`` lets tests use ``httpx.MockTransport``.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Self
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from newsfeed.logging import get_logger
from newsfeed.models import FetchResult
from newsfeed.ports import FetchNotAllowedError

__all__ = [
    "DEFAULT_MAX_BODY_BYTES",
    "DEFAULT_MAX_REDIRECTS",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_MIN_INTERVAL",
    "DEFAULT_TIMEOUT",
    "DEFAULT_USER_AGENT",
    "MAX_RETRY_AFTER",
    "HttpFetcher",
    "parse_retry_after",
]

DEFAULT_USER_AGENT = "newsfeed-personal/0.1 (+https://github.com/4OH4/news-bot)"
DEFAULT_MIN_INTERVAL = 2.0
DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
DEFAULT_MAX_RETRIES = 3
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_MAX_BODY_BYTES = 5 * 1024 * 1024
DEFAULT_BACKOFF_BASE = 1.0
MAX_RETRY_AFTER = 60.0
# RFC 9309 lets crawlers ignore anything past the first 500 KiB of robots.txt.
ROBOTS_MAX_BYTES = 500 * 1024

_RETRY_STATUSES = frozenset({429, 502, 503, 504})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
# Transient failures worth retrying. Other httpx errors (an unsupported scheme,
# a malformed response) would fail the same way again.
_RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)

log = get_logger(__name__)


def parse_retry_after(value: str | None, now: datetime) -> float | None:
    """Return the delay in seconds a ``Retry-After`` header asks for, or None.

    Accepts both forms from RFC 9110: delta-seconds and an HTTP-date, which is
    measured from ``now``. A date in the past gives 0. Unparseable values give None.
    """
    if value is None:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - now).total_seconds())


@dataclass(frozen=True)
class _RobotsPolicy:
    """The robots.txt decision for one origin: a parsed file or a blanket rule."""

    parser: RobotFileParser | None
    allow_all: bool

    def allows(self, url: str, user_agent: str) -> bool:
        if self.parser is None:
            return self.allow_all
        return self.parser.can_fetch(user_agent, url)


@dataclass
class _Response:
    """What one HTTP attempt produced; ``status`` is None if there was no response."""

    status: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes = b""
    error: str | None = None
    retryable: bool = False


class _RateLimiter:
    """Thread-safe minimum interval between requests to the same host.

    Each caller reserves the next free slot for its host under the lock, then
    sleeps outside it, so concurrent callers queue up one interval apart
    instead of all waking at once.
    """

    def __init__(
        self,
        default_interval: float,
        host_intervals: Mapping[str, float],
        clock: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        self._default = default_interval
        self._intervals = {host.lower(): interval for host, interval in host_intervals.items()}
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_slot: dict[str, float] = {}

    def interval(self, host: str) -> float:
        return self._intervals.get(host, self._default)

    def wait(self, host: str) -> float:
        """Block until ``host`` may be contacted again; return the seconds waited."""
        with self._lock:
            now = self._clock()
            start = max(now, self._next_slot.get(host, now))
            self._next_slot[host] = start + self.interval(host)
        delay = start - now
        if delay > 0:
            self._sleep(delay)
        return delay


class HttpFetcher:
    """Polite HTTP client implementing the ``Fetcher`` port.

    Safe to share between threads. Call ``close`` (or use it as a context
    manager) to release the connection pool.
    """

    def __init__(
        self,
        user_agent: str,
        *,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        host_intervals: Mapping[str, float] | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        max_retry_after: float = MAX_RETRY_AFTER,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        """Create a fetcher.

        Args:
            user_agent: Identifiable User-Agent sent with every request and
                matched against robots.txt, e.g. ``DEFAULT_USER_AGENT``.
            min_interval: Default seconds between requests to one host.
            host_intervals: Per-host overrides of ``min_interval``, keyed by hostname.
            max_retries: Retries after the first attempt for transient failures.
            backoff_base: Base of the exponential backoff, in seconds.
            max_retry_after: Cap on the delay honoured from ``Retry-After``.
            timeout: httpx timeouts (default: connect 10s, read 20s).
            max_redirects: Redirect hops followed before giving up.
            max_body_bytes: Larger (decoded) bodies give an error result.
            transport: httpx transport; tests pass ``httpx.MockTransport``.
            clock: Monotonic clock in seconds, for rate limits and timings.
            sleep: Called to wait; tests pass a fake that advances ``clock``.
            jitter: Returns a float in [0, 1) added to backoff delays.
        """
        if not user_agent.strip():
            raise ValueError("user_agent must identify the fetcher")
        if max_retries < 0 or max_redirects < 0 or max_body_bytes <= 0:
            raise ValueError("max_retries and max_redirects must be >= 0, max_body_bytes > 0")
        self.user_agent = user_agent
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._max_retry_after = max_retry_after
        self._max_redirects = max_redirects
        self._max_body_bytes = max_body_bytes
        self._clock = clock
        self._sleep = sleep
        self._jitter = jitter
        self._limiter = _RateLimiter(min_interval, host_intervals or {}, clock, sleep)
        # Redirects are followed by hand so every hop gets the robots check and
        # rate limit of its own host.
        self._client = httpx.Client(
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
            max_redirects=max_redirects,
            headers={"User-Agent": user_agent},
        )
        self._robots: dict[str, _RobotsPolicy] = {}
        self._robots_locks: dict[str, threading.Lock] = {}
        self._robots_guard = threading.Lock()

    # --- Lifecycle -----------------------------------------------------------

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --- Fetcher port --------------------------------------------------------

    def fetch(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> FetchResult:
        """GET ``url`` politely, conditionally if ``etag`` or ``last_modified`` is given.

        Never raises for HTTP, network or robots.txt failures: the result has
        ``error`` set (and ``status_code`` None when there was no response).
        """
        started = self._clock()
        request_headers: dict[str, str] = {}
        if etag:
            request_headers["If-None-Match"] = etag
        if last_modified:
            request_headers["If-Modified-Since"] = last_modified

        current = url
        for _hop in range(self._max_redirects + 1):
            problem = _url_problem(current)
            if problem is not None:
                return self._result(url, current, started, _Response(error=problem))
            if not self.is_allowed(current):
                log.info("fetch disallowed by robots.txt", extra={"url": current})
                return self._result(
                    url, current, started, _Response(error="disallowed by robots.txt")
                )
            response = self._get_with_retries(current, request_headers)
            if response.status in _REDIRECT_STATUSES and response.error is None:
                location = response.headers.get("location")
                if not location:
                    response.error = f"HTTP {response.status} redirect without Location"
                    return self._result(url, current, started, response)
                current = urljoin(current, location.strip())
                continue
            return self._result(url, current, started, response)
        return self._result(
            url,
            current,
            started,
            _Response(error=f"too many redirects (more than {self._max_redirects})"),
        )

    def is_allowed(self, url: str) -> bool:
        """Return True if robots.txt allows fetching ``url`` with our User-Agent.

        Fetches and caches the origin's robots.txt on first use. Malformed or
        non-HTTP URLs are never allowed.
        """
        if _url_problem(url) is not None:
            return False
        return self._robots_policy(_origin(url)).allows(url, self.user_agent)

    @contextmanager
    def permit(self, url: str) -> Iterator[None]:
        """Apply the robots.txt check and rate limit for a request made elsewhere.

        Raises ``FetchNotAllowedError`` if robots.txt forbids ``url`` (or the URL
        isn't a valid http(s) URL); otherwise waits for the host's rate-limit slot.
        """
        if not self.is_allowed(url):
            log.info("permit refused by robots.txt", extra={"url": url})
            raise FetchNotAllowedError(url)
        self._limiter.wait(_host(url))
        yield

    # --- Internals -----------------------------------------------------------

    def _get_with_retries(self, url: str, headers: Mapping[str, str]) -> _Response:
        host = _host(url)
        attempt = 0
        while True:
            attempt += 1
            self._limiter.wait(host)
            attempt_started = self._clock()
            response = self._get_once(url, headers)
            elapsed_ms = _ms(self._clock() - attempt_started)
            will_retry = response.retryable and attempt <= self._max_retries
            context = {
                "url": url,
                "host": host,
                "status": response.status,
                "elapsed_ms": elapsed_ms,
                "attempt": attempt,
            }
            if response.error is None:
                log.info("fetch", extra=context)
            else:
                log.warning(
                    "fetch failed", extra={**context, "error": response.error, "retry": will_retry}
                )
            if not will_retry:
                return response
            self._sleep(self._retry_delay(attempt, response))

    def _get_once(self, url: str, headers: Mapping[str, str]) -> _Response:
        try:
            with self._client.stream("GET", url, headers=headers) as resp:
                return self._read(resp)
        except _RETRYABLE_ERRORS as exc:
            return _Response(error=_describe(exc), retryable=True)
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            return _Response(error=_describe(exc))

    def _read(self, resp: httpx.Response) -> _Response:
        status = resp.status_code
        headers = dict(resp.headers)
        if status == 304 or status in _REDIRECT_STATUSES:
            return _Response(status=status, headers=headers)
        if status in _RETRY_STATUSES:
            return _Response(status=status, headers=headers, error=f"HTTP {status}", retryable=True)
        too_big = _Response(
            status=status,
            headers=headers,
            error=f"response body exceeds {self._max_body_bytes} bytes",
        )
        declared = resp.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > self._max_body_bytes:
            return too_big
        # Count decoded bytes, so a small compressed body can't inflate past the cap.
        chunks: list[bytes] = []
        size = 0
        for chunk in resp.iter_bytes():
            size += len(chunk)
            if size > self._max_body_bytes:
                return too_big
            chunks.append(chunk)
        error = f"HTTP {status}" if status >= 400 else None
        return _Response(status=status, headers=headers, content=b"".join(chunks), error=error)

    def _retry_delay(self, attempt: int, response: _Response) -> float:
        retry_after = parse_retry_after(response.headers.get("retry-after"), datetime.now(UTC))
        if retry_after is not None:
            return min(retry_after, self._max_retry_after)
        return self._backoff_base * (2.0 ** (attempt - 1) + self._jitter())

    def _result(self, url: str, final_url: str, started: float, response: _Response) -> FetchResult:
        not_modified = response.status == 304
        return FetchResult(
            url=url,
            final_url=final_url,
            status_code=response.status,
            headers=response.headers,
            content=b"" if not_modified else response.content,
            not_modified=not_modified,
            error=response.error,
            elapsed_ms=_ms(self._clock() - started),
        )

    def _robots_policy(self, origin: str) -> _RobotsPolicy:
        with self._robots_guard:
            policy = self._robots.get(origin)
            if policy is not None:
                return policy
            origin_lock = self._robots_locks.setdefault(origin, threading.Lock())
        # One fetch per origin: other threads wanting the same origin wait here.
        with origin_lock:
            with self._robots_guard:
                policy = self._robots.get(origin)
            if policy is None:
                policy = self._load_robots(origin)
                with self._robots_guard:
                    self._robots[origin] = policy
        return policy

    def _load_robots(self, origin: str) -> _RobotsPolicy:
        robots_url = f"{origin}/robots.txt"
        self._limiter.wait(_host(robots_url))
        started = self._clock()
        status: int | None = None
        try:
            # robots.txt redirects are followed by httpx (RFC 9309 asks for at
            # least five hops); they are rare and not worth per-hop rate limiting.
            with self._client.stream("GET", robots_url, follow_redirects=True) as resp:
                status = resp.status_code
                body = _read_capped(resp, ROBOTS_MAX_BYTES) if resp.is_success else b""
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            policy = _RobotsPolicy(parser=None, allow_all=False)
            reason = _describe(exc)
        else:
            if 200 <= status < 300:
                parser = RobotFileParser(robots_url)
                parser.parse(body.decode("utf-8", errors="replace").splitlines())
                policy = _RobotsPolicy(parser=parser, allow_all=False)
                reason = "parsed"
            elif 400 <= status < 500 and status != 429:
                # No robots.txt (or not for us to read) means no restrictions.
                policy = _RobotsPolicy(parser=None, allow_all=True)
                reason = f"HTTP {status}: allow all"
            else:
                # Server trouble or rate limiting: stay away from the origin this run.
                policy = _RobotsPolicy(parser=None, allow_all=False)
                reason = f"HTTP {status}: disallow all"
        log_fn = log.info if policy.parser is not None or policy.allow_all else log.warning
        log_fn(
            "robots.txt loaded",
            extra={
                "url": robots_url,
                "status": status,
                "robots": reason,
                "elapsed_ms": _ms(self._clock() - started),
            },
        )
        return policy


def _read_capped(resp: httpx.Response, limit: int) -> bytes:
    """Read at most ``limit`` bytes of the body, discarding the rest."""
    chunks: list[bytes] = []
    size = 0
    for chunk in resp.iter_bytes():
        chunks.append(chunk[: limit - size])
        size += len(chunks[-1])
        if size >= limit:
            break
    return b"".join(chunks)


def _url_problem(url: str) -> str | None:
    """Return why ``url`` can't be fetched, or None if it's a usable http(s) URL."""
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
    except ValueError as exc:
        return f"invalid URL: {exc}"
    if parts.scheme not in ("http", "https"):
        return f"unsupported URL scheme: {parts.scheme or '(none)'}"
    if not hostname:
        return "invalid URL: no host"
    return None


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc.lower()}"


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _ms(seconds: float) -> int:
    return max(0, round(seconds * 1000))


def _describe(exc: Exception) -> str:
    message = str(exc)
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__
