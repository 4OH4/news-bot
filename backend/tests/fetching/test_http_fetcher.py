"""Tests for ``newsfeed.fetching.http_fetcher``.

Everything runs against ``httpx.MockTransport`` with a fake clock, so there is no
network and no real waiting. Hosts are reserved ``example`` domains.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from newsfeed.fetching import DEFAULT_USER_AGENT, HttpFetcher
from newsfeed.fetching.http_fetcher import parse_retry_after
from newsfeed.ports import Fetcher, FetchNotAllowedError

Handler = Callable[[httpx.Request], httpx.Response]

ROBOTS_ALLOW_ALL = "User-agent: *\nDisallow:\n"


class FakeClock:
    """Monotonic clock whose ``sleep`` records the delay and advances time."""

    def __init__(self, *, advance: bool = True) -> None:
        self.now = 1000.0
        self.advance = advance
        self.sleeps: list[float] = []
        self._lock = threading.Lock()

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.sleeps.append(seconds)
            if self.advance:
                self.now += seconds


class Server:
    """A MockTransport handler: routes by URL and records every request."""

    def __init__(self, robots: str | int | Exception = ROBOTS_ALLOW_ALL) -> None:
        self.robots = robots
        self.routes: dict[str, list[httpx.Response | Exception] | Handler] = {}
        self.requests: list[httpx.Request] = []
        self._lock = threading.Lock()

    def route(self, url: str, *responses: httpx.Response | Exception) -> None:
        """Serve ``responses`` in order for ``url``; the last one repeats."""
        self.routes[url] = list(responses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        with self._lock:
            self.requests.append(request)
        url = str(request.url)
        if request.url.path == "/robots.txt" and url not in self.routes:
            if isinstance(self.robots, Exception):
                raise self.robots
            if isinstance(self.robots, int):
                return httpx.Response(self.robots)
            return httpx.Response(200, text=self.robots)
        route = self.routes.get(url)
        if route is None:
            return httpx.Response(404, text="not found")
        if callable(route):
            return route(request)
        with self._lock:
            item = route.pop(0) if len(route) > 1 else route[0]
        if isinstance(item, Exception):
            raise item
        return item

    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]

    def page_urls(self) -> list[str]:
        return [u for u in self.urls() if not u.endswith("/robots.txt")]


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def server() -> Server:
    return Server()


@pytest.fixture
def make_fetcher(server: Server, clock: FakeClock) -> Iterator[Callable[..., HttpFetcher]]:
    created: list[HttpFetcher] = []

    def make(**kwargs: Any) -> HttpFetcher:
        options: dict[str, Any] = {
            "transport": httpx.MockTransport(server),
            "clock": clock,
            "sleep": clock.sleep,
            "jitter": lambda: 0.5,
        }
        options.update(kwargs)
        fetcher = HttpFetcher(DEFAULT_USER_AGENT, **options)
        created.append(fetcher)
        return fetcher

    yield make
    for fetcher in created:
        fetcher.close()


@pytest.fixture
def records() -> Iterator[list[logging.LogRecord]]:
    """Capture records from the ``newsfeed`` logger, which doesn't propagate to root."""
    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = _Capture(level=logging.DEBUG)
    logger = logging.getLogger("newsfeed")
    logger.addHandler(handler)
    try:
        yield captured
    finally:
        logger.removeHandler(handler)


# --- Port conformance and basics ----------------------------------------------


def test_implements_fetcher_port(make_fetcher: Callable[..., HttpFetcher]) -> None:
    fetcher: Fetcher = make_fetcher()
    assert isinstance(fetcher, Fetcher)


def test_rejects_blank_user_agent() -> None:
    with pytest.raises(ValueError, match="user_agent"):
        HttpFetcher("  ")


@pytest.mark.parametrize(
    "options", [{"max_retries": -1}, {"max_redirects": -1}, {"max_body_bytes": 0}]
)
def test_rejects_invalid_limits(options: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="must be"):
        HttpFetcher(DEFAULT_USER_AGENT, **options)


def test_successful_fetch_sends_user_agent(
    server: Server, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    server.route(
        "https://example.com/a",
        httpx.Response(200, content=b"<p>hi</p>", headers={"Content-Type": "text/html"}),
    )
    result = make_fetcher().fetch("https://example.com/a")

    assert result.status_code == 200
    assert result.content == b"<p>hi</p>"
    assert result.error is None
    assert result.final_url == "https://example.com/a"
    assert result.headers["content-type"] == "text/html"
    assert all(r.headers["user-agent"] == DEFAULT_USER_AGENT for r in server.requests)
    assert server.urls() == ["https://example.com/robots.txt", "https://example.com/a"]


def test_http_error_status_sets_error_without_raising(
    server: Server, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    server.route("https://example.com/gone", httpx.Response(410, text="gone"))
    result = make_fetcher().fetch("https://example.com/gone")

    assert result.status_code == 410
    assert result.error == "HTTP 410"
    assert server.page_urls() == ["https://example.com/gone"]  # no retry


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("ftp://example.com/file", "unsupported URL scheme: ftp"),
        ("not a url", "unsupported URL scheme: (none)"),
        ("https:///no-host", "invalid URL: no host"),
        ("http://[::1/broken", "invalid URL"),
    ],
)
def test_unusable_urls_give_error_results(
    url: str, message: str, server: Server, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    fetcher = make_fetcher()
    result = fetcher.fetch(url)

    assert result.status_code is None
    assert result.error is not None and result.error.startswith(message)
    assert not fetcher.is_allowed(url)
    assert server.requests == []


# --- robots.txt ----------------------------------------------------------------


def test_robots_disallow_blocks_fetch_without_requesting_page(
    server: Server, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    server.robots = "User-agent: *\nDisallow: /private/\n"
    server.route("https://example.com/public", httpx.Response(200, text="ok"))
    fetcher = make_fetcher()

    blocked = fetcher.fetch("https://example.com/private/page")
    allowed = fetcher.fetch("https://example.com/public")

    assert blocked.status_code is None
    assert blocked.error == "disallowed by robots.txt"
    assert allowed.status_code == 200
    assert server.page_urls() == ["https://example.com/public"]


def test_robots_rules_for_our_user_agent_apply(
    server: Server, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    server.robots = "User-agent: newsfeed-personal\nDisallow: /\n\nUser-agent: *\nDisallow:\n"
    fetcher = make_fetcher()

    assert not fetcher.is_allowed("https://example.com/anything")


def test_robots_fetched_once_per_origin(
    server: Server, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    for path in ("a", "b", "c"):
        server.route(f"https://example.com/{path}", httpx.Response(200, text=path))
    server.route("https://other.example.org/x", httpx.Response(200, text="x"))
    fetcher = make_fetcher()

    for path in ("a", "b", "c"):
        fetcher.fetch(f"https://example.com/{path}")
    fetcher.fetch("https://other.example.org/x")

    robots = [u for u in server.urls() if u.endswith("/robots.txt")]
    assert robots == ["https://example.com/robots.txt", "https://other.example.org/robots.txt"]


def test_robots_fetched_once_under_concurrency(
    server: Server, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    fetcher = make_fetcher(min_interval=0)
    threads = [
        threading.Thread(target=fetcher.is_allowed, args=(f"https://example.com/{i}",))
        for i in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert server.urls() == ["https://example.com/robots.txt"]


@pytest.mark.parametrize("status", [401, 403, 404, 410])
def test_robots_4xx_allows_all(
    status: int, server: Server, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    server.robots = status
    assert make_fetcher().is_allowed("https://example.com/anything")


@pytest.mark.parametrize(
    "robots",
    [
        429,
        500,
        503,
        httpx.ConnectTimeout("timed out"),
        httpx.ReadTimeout("timed out"),
        httpx.ConnectError("refused"),
    ],
    ids=["429", "500", "503", "connect-timeout", "read-timeout", "connect-error"],
)
def test_robots_5xx_or_network_failure_disallows_host(
    robots: int | Exception, server: Server, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    server.robots = robots
    server.route("https://example.com/a", httpx.Response(200, text="ok"))
    fetcher = make_fetcher()

    result = fetcher.fetch("https://example.com/a")

    assert result.error == "disallowed by robots.txt"
    assert not fetcher.is_allowed("https://example.com/b")
    assert server.page_urls() == []
    # The failure is cached for the run: robots.txt isn't retried.
    assert server.urls() == ["https://example.com/robots.txt"]


def test_robots_body_is_capped(server: Server, make_fetcher: Callable[..., HttpFetcher]) -> None:
    # A Disallow rule past the 500 KiB cap is ignored, as RFC 9309 permits.
    padding = "# " + "x" * (600 * 1024) + "\n"
    server.robots = "User-agent: *\nDisallow: /early\n" + padding + "Disallow: /late\n"
    fetcher = make_fetcher()

    assert not fetcher.is_allowed("https://example.com/early")
    assert fetcher.is_allowed("https://example.com/late")


# --- Rate limiting ----------------------------------------------------------------


def test_rate_limit_spaces_requests_to_same_host(
    server: Server, clock: FakeClock, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    server.route("https://example.com/a", httpx.Response(200, text="a"))
    server.route("https://example.com/b", httpx.Response(200, text="b"))
    fetcher = make_fetcher()

    fetcher.fetch("https://example.com/a")  # robots.txt then /a: one wait
    fetcher.fetch("https://example.com/b")  # one more wait

    assert clock.sleeps == [2.0, 2.0]


def test_rate_limit_is_per_host_with_overrides(
    server: Server, clock: FakeClock, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    server.robots = 404
    server.route("https://slow.example.com/a", httpx.Response(200, text="a"))
    server.route("https://fast.example.org/a", httpx.Response(200, text="a"))
    fetcher = make_fetcher(host_intervals={"Slow.Example.com": 5.0})

    fetcher.fetch("https://slow.example.com/a")
    fetcher.fetch("https://fast.example.org/a")

    # slow: robots then page, 5s apart; fast: robots then page, 2s apart.
    # Neither host waits for the other.
    assert clock.sleeps == [5.0, 2.0]


def test_rate_limit_waits_only_for_the_remaining_interval(
    server: Server, clock: FakeClock, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    server.route("https://example.com/a", httpx.Response(200, text="a"))
    fetcher = make_fetcher()
    fetcher.is_allowed("https://example.com/a")  # robots.txt at t=1000
    clock.now += 1.5

    fetcher.fetch("https://example.com/a")

    assert clock.sleeps == [pytest.approx(0.5)]


def test_rate_limit_is_thread_safe(
    server: Server, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    # The clock doesn't advance, so each thread's wait shows the slot it reserved.
    still = FakeClock(advance=False)
    fetcher = make_fetcher(clock=still, sleep=still.sleep)
    fetcher.is_allowed("https://example.com/")  # load robots.txt: slot 0 taken
    barrier = threading.Barrier(4)

    def enter() -> None:
        barrier.wait()
        with fetcher.permit("https://example.com/page"):
            pass

    threads = [threading.Thread(target=enter) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(still.sleeps) == [2.0, 4.0, 6.0, 8.0]


# --- permit() ---------------------------------------------------------------------


def test_permit_raises_when_robots_disallow(
    server: Server, clock: FakeClock, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    server.robots = "User-agent: *\nDisallow: /private\n"
    fetcher = make_fetcher()
    entered = False

    with pytest.raises(FetchNotAllowedError), fetcher.permit("https://example.com/private"):
        entered = True

    assert not entered
    assert clock.sleeps == []  # refused before taking a rate-limit slot
    assert server.page_urls() == []


def test_permit_applies_rate_limit_and_shares_it_with_fetch(
    server: Server, clock: FakeClock, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    server.route("https://example.com/a", httpx.Response(200, text="a"))
    fetcher = make_fetcher()

    with fetcher.permit("https://example.com/rendered"):
        pass  # e.g. Playwright navigates here
    with fetcher.permit("https://example.com/rendered-too"):
        pass
    fetcher.fetch("https://example.com/a")

    # robots.txt, permit, permit, fetch: three waits of 2s each.
    assert clock.sleeps == [2.0, 2.0, 2.0]
    assert server.page_urls() == ["https://example.com/a"]


def test_permit_rejects_unusable_url(make_fetcher: Callable[..., HttpFetcher]) -> None:
    fetcher = make_fetcher()
    with pytest.raises(FetchNotAllowedError), fetcher.permit("file:///etc/passwd"):
        pass


# --- Conditional GET --------------------------------------------------------------


def test_conditional_get_304_returns_not_modified(
    server: Server, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    server.route("https://example.com/feed.xml", httpx.Response(304, headers={"ETag": '"v1"'}))

    result = make_fetcher().fetch(
        "https://example.com/feed.xml", etag='"v1"', last_modified="Mon, 05 Oct 2026 08:00:00 GMT"
    )

    request = server.requests[-1]
    assert request.headers["if-none-match"] == '"v1"'
    assert request.headers["if-modified-since"] == "Mon, 05 Oct 2026 08:00:00 GMT"
    assert result.not_modified is True
    assert result.status_code == 304
    assert result.content == b""
    assert result.error is None
    assert result.headers["etag"] == '"v1"'


def test_unconditional_get_sends_no_validators(
    server: Server, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    server.route("https://example.com/feed.xml", httpx.Response(200, text="<rss/>"))
    result = make_fetcher().fetch("https://example.com/feed.xml")

    request = server.requests[-1]
    assert "if-none-match" not in request.headers
    assert "if-modified-since" not in request.headers
    assert result.not_modified is False


# --- Retries ----------------------------------------------------------------------


def test_retry_on_503_then_success(
    server: Server,
    clock: FakeClock,
    make_fetcher: Callable[..., HttpFetcher],
    records: list[logging.LogRecord],
) -> None:
    server.route(
        "https://example.com/a", httpx.Response(503), httpx.Response(200, text="recovered")
    )
    result = make_fetcher().fetch("https://example.com/a")

    assert result.status_code == 200
    assert result.content == b"recovered"
    assert result.error is None
    assert server.page_urls() == ["https://example.com/a"] * 2
    # Rate limit (2s), backoff 1 * (2**0 + jitter 0.5) = 1.5s, then the rate
    # limit has 0.5s left of its interval.
    assert clock.sleeps == [2.0, 1.5, pytest.approx(0.5)]
    fetch_logs: list[Any] = [r for r in records if r.getMessage().startswith("fetch")]
    attempts = [(r.status, r.attempt) for r in fetch_logs]
    assert attempts == [(503, 1), (200, 2)]


def test_backoff_is_exponential_and_retries_are_bounded(
    server: Server, clock: FakeClock, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    server.route("https://example.com/a", httpx.Response(502))
    result = make_fetcher(min_interval=0).fetch("https://example.com/a")

    assert result.status_code == 502
    assert result.error == "HTTP 502"
    assert len(server.page_urls()) == 4  # first attempt + 3 retries
    assert clock.sleeps == [1.5, 2.5, 4.5]  # base * (2**n + jitter)


@pytest.mark.parametrize(
    ("header", "expected"),
    [("7", 7.0), ("600", 60.0), ("garbage", 1.5)],
    ids=["seconds", "capped-at-60", "unparseable-falls-back-to-backoff"],
)
def test_retry_after_is_honoured_and_capped(
    header: str,
    expected: float,
    server: Server,
    clock: FakeClock,
    make_fetcher: Callable[..., HttpFetcher],
) -> None:
    server.route(
        "https://example.com/a",
        httpx.Response(429, headers={"Retry-After": header}),
        httpx.Response(200, text="ok"),
    )
    result = make_fetcher(min_interval=0).fetch("https://example.com/a")

    assert result.status_code == 200
    assert clock.sleeps == [expected]


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("eof")],
    ids=["connect-error", "read-timeout", "disconnected"],
)
def test_retry_on_network_errors_then_success(
    error: Exception, server: Server, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    server.route("https://example.com/a", error, httpx.Response(200, text="ok"))
    result = make_fetcher(min_interval=0).fetch("https://example.com/a")

    assert result.status_code == 200
    assert len(server.page_urls()) == 2


def test_persistent_network_error_returns_error_result(
    server: Server, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    server.route("https://example.com/a", httpx.ConnectError("connection refused"))
    result = make_fetcher(min_interval=0).fetch("https://example.com/a")

    assert result.status_code is None
    assert result.error == "ConnectError: connection refused"
    assert len(server.page_urls()) == 4


def test_non_transient_errors_are_not_retried(
    server: Server, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    server.route("https://example.com/a", httpx.DecodingError("bad gzip"))
    result = make_fetcher(min_interval=0).fetch("https://example.com/a")

    assert result.error == "DecodingError: bad gzip"
    assert len(server.page_urls()) == 1


@pytest.mark.parametrize("status", [400, 403, 404, 500])
def test_other_statuses_are_not_retried(
    status: int, server: Server, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    server.route("https://example.com/a", httpx.Response(status))
    result = make_fetcher(min_interval=0).fetch("https://example.com/a")

    assert result.status_code == status
    assert result.error == f"HTTP {status}"
    assert len(server.page_urls()) == 1


def test_parse_retry_after_http_date() -> None:
    now = datetime(2026, 10, 12, 12, 0, 0, tzinfo=UTC)
    assert parse_retry_after("Mon, 12 Oct 2026 12:00:30 GMT", now) == 30.0
    assert parse_retry_after("Mon, 12 Oct 2026 11:00:00 GMT", now) == 0.0
    assert parse_retry_after(" 12 ", now) == 12.0
    # "-0000" means "UTC, source zone unknown" and parses as a naive datetime.
    assert parse_retry_after("Mon, 12 Oct 2026 12:01:00 -0000", now) == 60.0
    assert parse_retry_after("soon", now) is None
    assert parse_retry_after(None, now) is None


# --- Body size ----------------------------------------------------------------------


def test_oversize_body_with_content_length_is_an_error(
    server: Server, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    server.route("https://example.com/big", httpx.Response(200, content=b"x" * 101))
    result = make_fetcher(max_body_bytes=100).fetch("https://example.com/big")

    assert result.status_code == 200
    assert result.content == b""
    assert result.error == "response body exceeds 100 bytes"


def test_oversize_streamed_body_without_content_length_is_an_error(
    server: Server, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(20):
            yield b"x" * 10

    server.route("https://example.com/big", httpx.Response(200, content=chunks()))
    result = make_fetcher(max_body_bytes=100).fetch("https://example.com/big")

    assert "content-length" not in result.headers
    assert result.content == b""
    assert result.error == "response body exceeds 100 bytes"


def test_body_at_the_limit_is_accepted(
    server: Server, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    server.route("https://example.com/ok", httpx.Response(200, content=b"x" * 100))
    result = make_fetcher(max_body_bytes=100).fetch("https://example.com/ok")

    assert result.error is None
    assert len(result.content) == 100


def test_default_body_limit_is_5_mb(
    server: Server, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    too_big = b"x" * (5 * 1024 * 1024 + 1)
    server.route("https://example.com/big", httpx.Response(200, content=too_big))
    result = make_fetcher().fetch("https://example.com/big")

    assert result.error == f"response body exceeds {5 * 1024 * 1024} bytes"


# --- Redirects ------------------------------------------------------------------------


def test_redirect_records_final_url(
    server: Server, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    server.route("https://example.com/old", httpx.Response(301, headers={"Location": "/new"}))
    server.route("https://example.com/new", httpx.Response(200, text="moved"))

    result = make_fetcher().fetch("https://example.com/old")

    assert result.url == "https://example.com/old"
    assert result.final_url == "https://example.com/new"
    assert result.status_code == 200
    assert result.content == b"moved"


def test_cross_host_redirect_checks_robots_of_target(
    server: Server, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    server.route(
        "https://news.example.com/r/1",
        httpx.Response(302, headers={"Location": "https://publisher.example.org/story"}),
    )
    server.route(
        "https://publisher.example.org/robots.txt",
        httpx.Response(200, text="User-agent: *\nDisallow: /story\n"),
    )

    result = make_fetcher().fetch("https://news.example.com/r/1")

    assert result.error == "disallowed by robots.txt"
    assert result.final_url == "https://publisher.example.org/story"
    assert "https://publisher.example.org/story" not in server.urls()


def test_too_many_redirects_is_an_error(
    server: Server, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    for i in range(10):
        server.route(
            f"https://example.com/{i}", httpx.Response(302, headers={"Location": f"/{i + 1}"})
        )

    result = make_fetcher(min_interval=0).fetch("https://example.com/0")

    assert result.error == "too many redirects (more than 5)"
    assert result.final_url == "https://example.com/6"
    assert len(server.page_urls()) == 6  # the original request plus 5 redirects


def test_redirect_without_location_is_an_error(
    server: Server, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    server.route("https://example.com/a", httpx.Response(302))
    result = make_fetcher().fetch("https://example.com/a")

    assert result.status_code == 302
    assert result.error == "HTTP 302 redirect without Location"


def test_redirect_to_unsupported_scheme_is_an_error(
    server: Server, make_fetcher: Callable[..., HttpFetcher]
) -> None:
    server.route(
        "https://example.com/a", httpx.Response(302, headers={"Location": "ftp://example.com/f"})
    )
    result = make_fetcher().fetch("https://example.com/a")

    assert result.error == "unsupported URL scheme: ftp"
    assert result.final_url == "ftp://example.com/f"


# --- Logging and timing ------------------------------------------------------------------


def test_each_fetch_is_logged_with_context(
    server: Server,
    clock: FakeClock,
    make_fetcher: Callable[..., HttpFetcher],
    records: list[logging.LogRecord],
) -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        clock.now += 0.25
        return httpx.Response(200, text="ok")

    server.routes["https://example.com/a"] = slow
    result = make_fetcher().fetch("https://example.com/a")

    fetch_logs = [r for r in records if r.getMessage() == "fetch"]
    assert len(fetch_logs) == 1
    entry: Any = fetch_logs[0]
    assert entry.url == "https://example.com/a"
    assert entry.status == 200
    assert entry.elapsed_ms == 250
    assert entry.attempt == 1
    assert entry.levelno == logging.INFO
    # Total elapsed includes the 2s rate-limit wait after robots.txt.
    assert result.elapsed_ms == 2250


def test_failed_fetch_is_logged_as_warning(
    server: Server, make_fetcher: Callable[..., HttpFetcher], records: list[logging.LogRecord]
) -> None:
    server.route("https://example.com/a", httpx.Response(404))
    make_fetcher().fetch("https://example.com/a")

    failures: list[Any] = [r for r in records if r.getMessage() == "fetch failed"]
    assert len(failures) == 1
    assert failures[0].levelno == logging.WARNING
    assert failures[0].error == "HTTP 404"
    assert failures[0].retry is False


def test_context_manager_closes_client(server: Server, clock: FakeClock) -> None:
    with HttpFetcher(
        DEFAULT_USER_AGENT, transport=httpx.MockTransport(server), clock=clock, sleep=clock.sleep
    ) as fetcher:
        assert fetcher.is_allowed("https://example.com/")
    assert fetcher._client.is_closed
