"""Render JavaScript-heavy pages in headless Chromium (FR-6).

Playwright is an optional dependency (the ``playwright`` dependency group and
the ``playwright`` Docker target; PROJECT_PLAN.md §11), so it is imported only
when a browser is first needed. Local setup::

    cd backend && uv sync --group playwright && uv run playwright install chromium

``PlaywrightRenderer`` turns a URL into rendered HTML; ``PlaywrightExtractor``
renders and then delegates to ``TrafilaturaExtractor``. The extractor chain
(#14) uses it as its ``renderer`` for domains flagged ``render: playwright`` in
``config/extraction.yaml``.

Politeness (CLAUDE.md rule 6): navigation goes through ``Fetcher.permit`` (robots.txt
and the per-host rate limit), including any main-frame redirect to another URL,
and the browser sends the project User-Agent. Images, media, fonts and known ad
and tracker hosts are never requested.

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
            page.route("**/*", lambda route: self._handle_route(route, url))
            with self._fetcher.permit(url):
                page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=self._idle_timeout_ms)
            except self._timeout_error:
                log.debug("network never went idle", extra={"stage": "extract", "url": url})
            html: str = page.content()
            return html
        finally:
            context.close()

    def _handle_route(self, route: Any, original_url: str) -> None:
        """Abort unwanted requests; permit main-frame redirects to other URLs."""
        request = route.request
        if request.resource_type in BLOCKED_RESOURCE_TYPES or is_blocked_host(
            request.url, self._blocked_hosts
        ):
            route.abort()
            return
        if request.is_navigation_request() and request.url != original_url:
            try:
                with self._fetcher.permit(request.url):
                    pass
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
