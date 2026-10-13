"""Render JavaScript-heavy pages in headless Chromium (FR-6).

Playwright is an optional dependency (the ``playwright`` dependency group and
the ``playwright`` Docker target; PROJECT_PLAN.md §11), so it is imported only
when a browser is first needed. Local setup::

    cd backend && uv sync --group playwright && uv run playwright install chromium

``PlaywrightRenderer`` turns a URL into rendered HTML; ``PlaywrightExtractor``
renders and then delegates to ``TrafilaturaExtractor``. The extractor chain
(#14) uses it as its ``renderer`` for domains flagged ``render: playwright`` in
``config/extraction.yaml``.

Politeness (CLAUDE.md rule 6). The browser sends the project User-Agent, and
images, media, fonts and known ad and tracker hosts are never requested. Main-frame
navigations go through ``Fetcher.permit`` (robots.txt and the per-host rate limit):

- the requested URL is permitted before ``goto``;
- Playwright does not run route handlers for HTTP 3xx hops, so after ``goto`` the
  response's final URL and every URL in its ``redirected_from`` chain are permitted
  too, and a refusal raises ``FetchNotAllowedError`` before any content is returned;
- JavaScript and meta-refresh navigations of the main frame are permitted by the
  route handler before the request is sent (and aborted if refused);
- the HTTP 3xx hops of such a navigation reach neither the route handler nor the
  ``goto`` response, so before the HTML is read the page's final URL
  (``page.url``) is permitted too, and a refusal raises ``FetchNotAllowedError``.

Known limitation: the redirect checks run after Chromium has followed the
redirects, so a robots-disallowed redirect target is requested once (its content
is discarded, never extracted). ``HttpFetcher`` checks before each request. Closing
this gap means fetching every main-frame document with
``route.fetch(max_redirects=0)`` and following each ``Location`` by hand, which
replaces the browser's own navigation and was judged too complex for Phase 1.
Intermediate hops of a JS-initiated redirect chain are not checked, only where it
ends, and the HTTP status of a JS-initiated navigation is not checked.

Not covered: iframe navigations, and same-host subresources (scripts, XHR, CSS),
which don't go through ``permit``. That is acceptable in Phase 1: they load only as
part of rendering a page we were permitted to fetch, as a normal browser would,
and are not extracted as articles; iframe documents are not part of the extracted
page. Revisit if a flagged domain's subresources live under robots.txt rules.

A response of ``None`` or with status 400 or above is a render failure
(``RenderError``), so an error page is never extracted as an article.

Playwright's sync API is bound to one thread, so every browser call runs on a
single dedicated worker thread; the renderer is safe to call from the extract
stage's thread pool, but renders one page at a time.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Final, Protocol
from urllib.parse import urlsplit

from newsfeed.extract.common import run_safely
from newsfeed.extract.trafilatura_extractor import TrafilaturaExtractor
from newsfeed.logging import get_logger
from newsfeed.models import ExtractionResult, ExtractionStatus
from newsfeed.ports import Extractor, Fetcher, FetchNotAllowedError

__all__ = [
    "BLOCKED_HOSTS",
    "BLOCKED_RESOURCE_TYPES",
    "PageRenderer",
    "PlaywrightExtractor",
    "PlaywrightRenderer",
    "RenderError",
    "is_blocked_host",
]

DEFAULT_TIMEOUT_S: Final = 20.0
"""Default limit for navigation to reach ``domcontentloaded``."""

DEFAULT_IDLE_TIMEOUT_S: Final = 3.0
"""How long to wait for the network to go idle after ``domcontentloaded``.

Pages with long-polling or analytics never go idle, so this wait is short and
running out of time is not an error.
"""

BLOCKED_RESOURCE_TYPES: Final = frozenset({"image", "media", "font"})
"""Resource types never loaded: article text doesn't need them."""

BLOCKED_HOSTS: Final = frozenset(
    {
        "doubleclick.net",
        "googlesyndication.com",
        "googleadservices.com",
        "google-analytics.com",
        "googletagmanager.com",
        "googletagservices.com",
        "adnxs.com",
        "taboola.com",
        "outbrain.com",
        "scorecardresearch.com",
        "quantserve.com",
        "facebook.net",
        "hotjar.com",
        "criteo.com",
        "amazon-adsystem.com",
    }
)
"""Ad and tracker domains; subdomains match too."""

log = get_logger(__name__)


class RenderError(Exception):
    """A page could not be rendered: no response, or an HTTP error status."""


class PageRenderer(Protocol):
    """Something that turns a URL into rendered HTML."""

    def render(self, url: str) -> str:
        """Return the page's HTML after JavaScript has run.

        Raises ``FetchNotAllowedError`` if robots.txt forbids ``url``, and any
        other exception on a browser or navigation failure.
        """
        ...


def is_blocked_host(url: str, blocked: frozenset[str] = BLOCKED_HOSTS) -> bool:
    """True if ``url``'s host is, or is a subdomain of, a blocked ad/tracker host."""
    host = (urlsplit(url).hostname or "").lower()
    return any(host == b or host.endswith(f".{b}") for b in blocked)


# Returns (browser, Playwright's TimeoutError class, a callable that stops the
# driver); injectable so tests need no browser.
BrowserLauncher = Callable[[], tuple[Any, type[Exception], Callable[[], None]]]


def _launch_chromium() -> tuple[Any, type[Exception], Callable[[], None]]:
    """Start Playwright and headless Chromium. Must run on the renderer's thread."""
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed; run `uv sync --group playwright` "
            "and `uv run playwright install chromium`"
        ) from exc
    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.launch(headless=True)
    except Exception:
        playwright.stop()
        raise
    return browser, PlaywrightTimeoutError, playwright.stop


class PlaywrightRenderer:
    """Render pages in headless Chromium, politely.

    The browser starts on first use and is reused (one fresh context per page, so
    no cookies carry over). Call ``close`` when done.
    """

    def __init__(
        self,
        fetcher: Fetcher,
        *,
        user_agent: str,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_S,
        blocked_hosts: frozenset[str] = BLOCKED_HOSTS,
        launcher: BrowserLauncher = _launch_chromium,
    ) -> None:
        """Create a renderer.

        Args:
            fetcher: supplies ``permit`` for robots.txt and rate limiting.
            user_agent: the project's identifiable User-Agent.
            timeout_s: navigation timeout (to ``domcontentloaded``).
            idle_timeout_s: extra wait for ``networkidle``; timing out is fine.
            blocked_hosts: ad/tracker domains to refuse.
            launcher: starts the browser; replaced in tests.
        """
        if not user_agent.strip():
            raise ValueError("user_agent must identify the fetcher")
        if timeout_s <= 0 or idle_timeout_s < 0:
            raise ValueError("timeout_s must be positive and idle_timeout_s not negative")
        self._fetcher = fetcher
        self._user_agent = user_agent
        self._timeout_ms = timeout_s * 1000
        self._idle_timeout_ms = idle_timeout_s * 1000
        self._blocked_hosts = blocked_hosts
        self._launcher = launcher
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="playwright")
        self._lock = threading.Lock()
        self._closed = False
        # Only touched on the pool thread.
        self._browser: Any = None
        self._stop_driver: Callable[[], None] | None = None
        self._timeout_error: type[Exception] = TimeoutError

    def render(self, url: str) -> str:
        """Render ``url`` and return its HTML. Raises on failure (see ``PageRenderer``)."""
        with self._lock:
            if self._closed:
                raise RuntimeError("renderer is closed")
            future = self._pool.submit(self._render, url)
        return future.result()

    def close(self) -> None:
        """Close the browser and stop the worker thread. Safe to call twice."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._pool.submit(self._shutdown).result()
        self._pool.shutdown()

    # --- Runs on the worker thread -------------------------------------------

    def _shutdown(self) -> None:
        browser, self._browser = self._browser, None
        stop, self._stop_driver = self._stop_driver, None
        try:
            if browser is not None:
                browser.close()
            if stop is not None:
                stop()
        except Exception as exc:
            log.warning(
                "closing browser failed",
                extra={"stage": "extract", "error_type": type(exc).__name__, "error": str(exc)},
            )

    def _render(self, url: str) -> str:
        if self._browser is None:
            self._browser, self._timeout_error, self._stop_driver = self._launcher()
        context = self._browser.new_context(user_agent=self._user_agent)
        try:
            page = context.new_page()
            permitted: set[str] = {url}
            page.route("**/*", lambda route: self._handle_route(route, page, permitted))
            with self._fetcher.permit(url):
                response = page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
            self._check_response(response, url, permitted)
            try:
                page.wait_for_load_state("networkidle", timeout=self._idle_timeout_ms)
            except self._timeout_error:
                log.debug("network never went idle", extra={"stage": "extract", "url": url})
            # A JS navigation's own HTTP 3xx hops reach neither the route handler nor
            # _check_response, so permit wherever the page ended up before reading it.
            self._permit_once(page.url, permitted)
            html: str = page.content()
            return html
        finally:
            context.close()

    def _check_response(self, response: Any, url: str, permitted: set[str]) -> None:
        """Fail on a missing or error response; permit the redirect chain it came through.

        Route handlers don't see HTTP redirect hops, so they are checked here.
        """
        if response is None:
            raise RenderError(f"no response for {url}")
        request = response.request
        chain = [response.url]
        while request is not None:
            chain.append(request.url)
            request = request.redirected_from
        for hop in chain:
            self._permit_once(hop, permitted)
        if response.status >= 400:
            raise RenderError(f"HTTP {response.status} for {url}")

    def _permit_once(self, url: str, permitted: set[str]) -> None:
        """Permit ``url`` unless already permitted; raises ``FetchNotAllowedError``."""
        if url not in permitted:
            with self._fetcher.permit(url):
                permitted.add(url)

    def _handle_route(self, route: Any, page: Any, permitted: set[str]) -> None:
        """Abort unwanted requests; permit main-frame navigations to new URLs."""
        request = route.request
        if request.resource_type in BLOCKED_RESOURCE_TYPES or is_blocked_host(
            request.url, self._blocked_hosts
        ):
            route.abort()
            return
        is_main_frame_navigation = (
            request.is_navigation_request() and request.frame == page.main_frame
        )
        if is_main_frame_navigation and request.url not in permitted:
            try:
                with self._fetcher.permit(request.url):
                    permitted.add(request.url)
            except FetchNotAllowedError:
                log.info(
                    "navigation refused by robots.txt",
                    extra={"stage": "extract", "url": request.url},
                )
                route.abort()
                return
        route.continue_()


class PlaywrightExtractor:
    """Render the page in a browser, then extract with Trafilatura.

    The ``html`` passed to ``extract`` is ignored (it is the un-rendered page);
    the URL is rendered afresh. Failures, including a robots.txt refusal, come
    back as ``failed`` results, never exceptions.
    """

    def __init__(self, renderer: PageRenderer, *, inner: Extractor | None = None) -> None:
        """Create the extractor; ``inner`` defaults to ``TrafilaturaExtractor``."""
        self._renderer = renderer
        self._inner: Extractor = inner or TrafilaturaExtractor()

    @property
    def name(self) -> str:
        """Extractor name recorded as ``ExtractionResult.method``."""
        return "playwright+trafilatura"

    def extract(self, html: str, url: str) -> ExtractionResult:
        """Render ``url`` and extract its main content. Never raises."""
        return run_safely(self.name, url, lambda: self._extract(url), logger=log)

    def _extract(self, url: str) -> ExtractionResult:
        try:
            rendered = self._renderer.render(url)
        except FetchNotAllowedError:
            return ExtractionResult(
                status=ExtractionStatus.FAILED,
                method=self.name,
                error="rendering refused by robots.txt",
            )
        result = self._inner.extract(rendered, url)
        return result.model_copy(update={"method": self.name})
