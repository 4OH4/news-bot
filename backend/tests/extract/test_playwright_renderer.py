"""Tests for the Playwright renderer and extractor, with a fake browser (no Chromium)."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from newsfeed.extract.chain import ExtractorChain
from newsfeed.extract.overrides import DomainOverride, ExtractionConfig
from newsfeed.extract.playwright_renderer import (
    PlaywrightExtractor,
    PlaywrightRenderer,
    is_blocked_host,
)
from newsfeed.extract.trafilatura_extractor import TrafilaturaExtractor
from newsfeed.models import ExtractionStatus
from newsfeed.ports import Extractor, FetchNotAllowedError
from tests.fakes import FakeFetcher

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "extract"
URL = "https://spa.example.com/story"
UA = "newsfeed-test/0.1 (+https://example.invalid)"


class FakeTimeout(Exception):
    pass


class FakeRequest:
    def __init__(self, url: str, resource_type: str = "xhr", navigation: bool = False) -> None:
        self.url = url
        self.resource_type = resource_type
        self._navigation = navigation

    def is_navigation_request(self) -> bool:
        return self._navigation


class FakeRoute:
    def __init__(self, request: FakeRequest) -> None:
        self.request = request
        self.outcome = ""

    def abort(self) -> None:
        self.outcome = "abort"

    def continue_(self) -> None:
        self.outcome = "continue"


class FakePage:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser
        self.handler: Any = None
        self.thread_ids: set[int] = set()

    def route(self, pattern: str, handler: Any) -> None:
        self.handler = handler

    def goto(self, url: str, *, wait_until: str, timeout: float) -> None:
        self.thread_ids.add(threading.get_ident())
        self.browser.log.append(f"goto {url} {wait_until} {timeout:g}")
        if self.browser.goto_error:
            raise FakeTimeout("navigation timeout")

    def wait_for_load_state(self, state: str, *, timeout: float) -> None:
        self.browser.log.append(f"wait {state} {timeout:g}")
        if self.browser.idle_times_out:
            raise FakeTimeout("never idle")

    def content(self) -> str:
        return self.browser.html


class FakeContext:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser
        self.closed = False

    def new_page(self) -> FakePage:
        page = FakePage(self.browser)
        self.browser.pages.append(page)
        return page

    def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self, log: list[str], html: str) -> None:
        self.log = log
        self.html = html
        self.idle_times_out = False
        self.goto_error = False
        self.pages: list[FakePage] = []
        self.contexts: list[FakeContext] = []
        self.user_agents: list[str] = []
        self.closed = False

    def new_context(self, *, user_agent: str) -> FakeContext:
        self.user_agents.append(user_agent)
        context = FakeContext(self)
        self.contexts.append(context)
        return context

    def close(self) -> None:
        self.closed = True


class LoggingFetcher(FakeFetcher):
    """A ``FakeFetcher`` that records permits in the same log as the fake browser."""

    log: list[str]

    def permit(self, url: str) -> Any:
        self.log.append(f"permit {url}")
        return super().permit(url)


class Harness:
    """A renderer wired to a fake browser and fetcher, with a shared event log."""

    def __init__(self, disallowed: list[str] | None = None) -> None:
        self.log: list[str] = []
        self.browser = FakeBrowser(self.log, "<html><body>rendered</body></html>")
        self.launches = 0
        self.driver_stopped = False
        self.fetcher = LoggingFetcher(disallowed=disallowed or [])
        self.fetcher.log = self.log
        self.renderer = PlaywrightRenderer(
            self.fetcher,
            user_agent=UA,
            timeout_s=20,
            idle_timeout_s=3,
            launcher=self._launch,
        )

    def _launch(self) -> tuple[Any, type[Exception], Any]:
        self.launches += 1
        return self.browser, FakeTimeout, self._stop

    def _stop(self) -> None:
        self.driver_stopped = True


@pytest.fixture
def make_harness() -> Any:
    created: list[Harness] = []

    def make(disallowed: list[str] | None = None) -> Harness:
        harness = Harness(disallowed)
        created.append(harness)
        return harness

    yield make
    for harness in created:
        harness.renderer.close()


def test_permit_is_called_before_navigation(make_harness: Any) -> None:
    h = make_harness()

    assert h.renderer.render(URL) == "<html><body>rendered</body></html>"

    assert h.log[0] == f"permit {URL}"
    assert h.log[1] == f"goto {URL} domcontentloaded 20000"
    assert h.fetcher.permits == [URL]


def test_robots_refusal_never_navigates(make_harness: Any) -> None:
    h = make_harness(disallowed=["https://spa.example.com/"])
    with pytest.raises(FetchNotAllowedError):
        h.renderer.render(URL)
    assert not any(entry.startswith("goto") for entry in h.log)
    assert h.browser.contexts[0].closed


def test_waits_for_networkidle_and_tolerates_timeout(make_harness: Any) -> None:
    h = make_harness()
    h.browser.idle_times_out = True
    assert "rendered" in h.renderer.render(URL)
    assert "wait networkidle 3000" in h.log


def test_navigation_timeout_propagates_and_closes_context(make_harness: Any) -> None:
    h = make_harness()
    h.browser.goto_error = True
    with pytest.raises(FakeTimeout):
        h.renderer.render(URL)
    assert h.browser.contexts[0].closed


def test_uses_project_user_agent_and_reuses_browser(make_harness: Any) -> None:
    h = make_harness()
    h.renderer.render(URL)
    h.renderer.render(URL + "2")
    assert h.launches == 1
    assert h.browser.user_agents == [UA, UA]


def test_all_browser_calls_run_on_one_thread(make_harness: Any) -> None:
    h = make_harness()
    threads = [threading.Thread(target=h.renderer.render, args=(f"{URL}{i}",)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    ids = {tid for page in h.browser.pages for tid in page.thread_ids}
    assert len(h.browser.pages) == 4
    assert len(ids) == 1
    assert threading.get_ident() not in ids


def test_close_stops_browser_and_driver_and_rejects_renders(make_harness: Any) -> None:
    h = make_harness()
    h.renderer.render(URL)
    h.renderer.close()
    h.renderer.close()  # idempotent
    assert h.browser.closed
    assert h.driver_stopped
    with pytest.raises(RuntimeError):
        h.renderer.render(URL)


def test_close_without_render_launches_nothing(make_harness: Any) -> None:
    h = make_harness()
    h.renderer.close()
    assert h.launches == 0


@pytest.mark.parametrize(
    ("resource_type", "url", "outcome"),
    [
        ("image", "https://spa.example.com/a.png", "abort"),
        ("media", "https://spa.example.com/a.mp4", "abort"),
        ("font", "https://spa.example.com/a.woff2", "abort"),
        ("script", "https://www.googletagmanager.com/gtm.js", "abort"),
        ("xhr", "https://ads.doubleclick.net/x", "abort"),
        ("script", "https://spa.example.com/app.js", "continue"),
        ("xhr", "https://spa.example.com/api/story", "continue"),
    ],
)
def test_route_blocking(make_harness: Any, resource_type: str, url: str, outcome: str) -> None:
    h = make_harness()
    h.renderer.render(URL)
    route = FakeRoute(FakeRequest(url, resource_type))
    h.browser.pages[0].handler(route)
    assert route.outcome == outcome


def test_redirect_navigation_is_permitted_or_aborted(make_harness: Any) -> None:
    h = make_harness(disallowed=["https://private.example.com/"])
    h.renderer.render(URL)
    handler = h.browser.pages[0].handler

    allowed = FakeRoute(FakeRequest("https://other.example.com/x", "document", navigation=True))
    refused = FakeRoute(FakeRequest("https://private.example.com/x", "document", navigation=True))
    original = FakeRoute(FakeRequest(URL, "document", navigation=True))
    for route in (allowed, refused, original):
        handler(route)

    assert allowed.outcome == "continue"
    assert refused.outcome == "abort"
    assert original.outcome == "continue"
    # The original URL was permitted once, by render, not again by the handler.
    assert h.fetcher.permits == [
        URL,
        "https://other.example.com/x",
        "https://private.example.com/x",
    ]


def test_is_blocked_host_matches_subdomains_only() -> None:
    assert is_blocked_host("https://doubleclick.net/x")
    assert is_blocked_host("https://stats.g.doubleclick.net/x")
    assert not is_blocked_host("https://notdoubleclick.net/x")
    assert not is_blocked_host("not a url")


def test_rejects_bad_arguments() -> None:
    with pytest.raises(ValueError, match="user_agent"):
        PlaywrightRenderer(FakeFetcher(), user_agent=" ")
    with pytest.raises(ValueError, match="timeout"):
        PlaywrightRenderer(FakeFetcher(), user_agent=UA, timeout_s=0)


def test_missing_playwright_gives_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    renderer = PlaywrightRenderer(FakeFetcher(), user_agent=UA)
    try:
        with pytest.raises(RuntimeError, match="uv sync --group playwright"):
            renderer.render(URL)
    finally:
        renderer.close()


# --- PlaywrightExtractor -----------------------------------------------------------


class StubRenderer:
    def __init__(self, html: str | Exception) -> None:
        self.html = html
        self.urls: list[str] = []

    def render(self, url: str) -> str:
        self.urls.append(url)
        if isinstance(self.html, Exception):
            raise self.html
        return self.html


def test_extractor_renders_then_extracts_with_trafilatura() -> None:
    rendered = (FIXTURES_DIR / "news_article.html").read_text(encoding="utf-8")
    shell = (FIXTURES_DIR / "js_shell.html").read_text(encoding="utf-8")
    renderer = StubRenderer(rendered)

    result = PlaywrightExtractor(renderer).extract(shell, URL)

    assert renderer.urls == [URL]
    assert result.method == "playwright+trafilatura"
    assert result.status is ExtractionStatus.OK
    expected = TrafilaturaExtractor().extract(rendered, URL)
    assert result.content_html == expected.content_html
    assert result.word_count == expected.word_count


def test_extractor_name_and_protocol() -> None:
    extractor = PlaywrightExtractor(StubRenderer(""))
    assert extractor.name == "playwright+trafilatura"
    assert isinstance(extractor, Extractor)


def test_extractor_reports_robots_refusal_as_failed() -> None:
    result = PlaywrightExtractor(StubRenderer(FetchNotAllowedError(URL))).extract("", URL)
    assert result.status is ExtractionStatus.FAILED
    assert result.method == "playwright+trafilatura"
    assert result.error is not None
    assert "robots" in result.error


def test_extractor_never_raises_on_browser_errors() -> None:
    result = PlaywrightExtractor(StubRenderer(FakeTimeout("navigation timeout"))).extract("", URL)
    assert result.status is ExtractionStatus.FAILED
    assert result.error is not None
    assert "navigation timeout" in result.error


def test_chain_uses_renderer_only_for_flagged_domains() -> None:
    shell = (FIXTURES_DIR / "js_shell.html").read_text(encoding="utf-8")
    rendered = (FIXTURES_DIR / "news_article.html").read_text(encoding="utf-8")
    renderer = StubRenderer(rendered)
    overrides = ExtractionConfig(domains={"spa.example.com": DomainOverride(render="playwright")})
    chain = ExtractorChain(
        [TrafilaturaExtractor()], overrides, renderer=PlaywrightExtractor(renderer)
    )

    flagged = chain.extract(shell, URL)
    other = chain.extract(shell, "https://plain.example.org/story")

    assert flagged.status is ExtractionStatus.OK
    assert flagged.method == "playwright+trafilatura"
    assert [a.method for a in flagged.attempts] == ["trafilatura", "playwright+trafilatura"]
    assert renderer.urls == [URL]
    assert other.method != "playwright+trafilatura"


# --- Live (excluded by default; needs Chromium and network) ------------------------


@pytest.mark.live
def test_live_render_simple_public_page() -> None:
    from newsfeed.fetching import DEFAULT_USER_AGENT, HttpFetcher

    fetcher = HttpFetcher(DEFAULT_USER_AGENT)
    renderer = PlaywrightRenderer(fetcher, user_agent=DEFAULT_USER_AGENT)
    try:
        html = renderer.render("https://example.com/")
    finally:
        renderer.close()
    assert "Example Domain" in html
