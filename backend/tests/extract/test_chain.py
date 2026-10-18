"""Tests for ``ExtractorChain``, using stub extractors and the synthetic fixtures."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from newsfeed.extract.chain import DEFAULT_MIN_PARTIAL_WORDS, ExtractorChain
from newsfeed.extract.newspaper_extractor import Newspaper4kExtractor
from newsfeed.extract.overrides import DomainOverride, ExtractionConfig
from newsfeed.extract.readability_extractor import ReadabilityExtractor
from newsfeed.extract.trafilatura_extractor import TrafilaturaExtractor
from newsfeed.models import ExtractionAttempt, ExtractionResult, ExtractionStatus
from newsfeed.ports import Extractor
from tests.fakes import FakeExtractor

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "extract"
URL = "https://news.example.com/local/2025/03/story"
HTML = "<html><body><p>Invented page.</p></body></html>"

OK = ExtractionStatus.OK
PARTIAL = ExtractionStatus.PARTIAL
FAILED = ExtractionStatus.FAILED


def result(status: ExtractionStatus, words: int = 0, **fields: object) -> ExtractionResult:
    """A canned result whose content has ``words`` words."""
    text = " ".join(["word"] * words) or None
    error = None if status is OK else f"canned {status}"
    return ExtractionResult.model_validate(
        {
            "status": status,
            "content_html": f"<p>{text}</p>" if text else None,
            "text": text,
            "word_count": words,
            "error": error,
            **fields,
        }
    )


class Recorder:
    """A stub extractor that logs its calls to a list shared with other stubs."""

    def __init__(self, name: str, canned: ExtractionResult, log: list[str]) -> None:
        self.name = name
        self._canned = canned
        self._log = log

    def extract(self, html: str, url: str) -> ExtractionResult:
        self._log.append(self.name)
        return self._canned.model_copy(update={"method": self.name})


class Raiser:
    """A misbehaving extractor that raises instead of returning ``failed``."""

    name = "raiser"

    def extract(self, html: str, url: str) -> ExtractionResult:
        raise RuntimeError("extractor bug")


def stubs(
    *canned: tuple[str, ExtractionResult],
) -> tuple[list[Extractor], list[str]]:
    calls: list[str] = []
    return [Recorder(name, res, calls) for name, res in canned], calls


def overrides(**domains: dict[str, object]) -> ExtractionConfig:
    return ExtractionConfig.model_validate({"domains": domains})


def methods(res: ExtractionResult) -> list[str]:
    return [a.method for a in res.attempts]


# --- Construction ------------------------------------------------------------


def test_is_an_extractor() -> None:
    chain = ExtractorChain([FakeExtractor("trafilatura")])
    assert isinstance(chain, Extractor)
    assert chain.name == "chain"
    assert [e.name for e in chain.extractors] == ["trafilatura"]
    assert chain.override_for(URL) is None


def test_rejects_no_extractors() -> None:
    with pytest.raises(ValueError, match="at least one extractor"):
        ExtractorChain([])


def test_rejects_duplicate_names() -> None:
    with pytest.raises(ValueError, match="duplicate extractor names: readability"):
        ExtractorChain([FakeExtractor("readability"), FakeExtractor("readability")])
    with pytest.raises(ValueError, match="duplicate extractor names: trafilatura"):
        ExtractorChain([FakeExtractor("trafilatura")], renderer=FakeExtractor("trafilatura"))


def test_rejects_override_naming_a_missing_extractor() -> None:
    config = overrides(**{"example.com": {"order": ["newspaper4k", "trafilatura"]}})
    with pytest.raises(ValueError, match=r"'example.com' .* not in the chain: newspaper4k"):
        ExtractorChain([FakeExtractor("trafilatura")], config)


@pytest.mark.parametrize("value", [0, -1])
def test_rejects_min_partial_words_below_one(value: int) -> None:
    with pytest.raises(ValueError, match="min_partial_words"):
        ExtractorChain([FakeExtractor("trafilatura")], min_partial_words=value)


def test_warns_when_render_is_configured_without_a_renderer(
    log_records: list[logging.LogRecord],
) -> None:
    ExtractorChain(
        [FakeExtractor("trafilatura")],
        overrides(**{"app.example.com": {"render": "playwright"}}),
    )
    [record] = [r for r in log_records if r.levelno == logging.WARNING]
    assert vars(record)["domain"] == "app.example.com"
    assert vars(record)["stage"] == "extract"


# --- Order and fallback ------------------------------------------------------


def test_stops_at_the_first_ok_result() -> None:
    extractors, calls = stubs(("trafilatura", result(OK, 200)), ("readability", result(OK, 300)))
    res = ExtractorChain(extractors).extract(HTML, URL)
    assert calls == ["trafilatura"]
    assert res.status is OK
    assert res.method == "trafilatura"
    assert res.word_count == 200
    assert res.attempts == [ExtractionAttempt(method="trafilatura", status=OK, word_count=200)]


def test_falls_back_in_order_when_the_primary_fails() -> None:
    extractors, calls = stubs(
        ("trafilatura", result(FAILED)),
        ("readability", result(PARTIAL, 60)),
        ("newspaper4k", result(OK, 180, title="Invented headline")),
    )
    res = ExtractorChain(extractors).extract(HTML, URL)
    assert calls == ["trafilatura", "readability", "newspaper4k"]
    assert res.status is OK
    assert res.method == "newspaper4k"
    assert res.title == "Invented headline"
    assert res.error is None
    assert res.attempts == [
        ExtractionAttempt(method="trafilatura", status=FAILED, error="canned failed"),
        ExtractionAttempt(
            method="readability", status=PARTIAL, error="canned partial", word_count=60
        ),
        ExtractionAttempt(method="newspaper4k", status=OK, word_count=180),
    ]


def test_passes_the_page_to_every_extractor() -> None:
    first = FakeExtractor("trafilatura", default=result(FAILED))
    second = FakeExtractor("readability", default=result(OK, 200))
    ExtractorChain([first, second]).extract(HTML, URL)
    assert first.calls == second.calls == [(URL, HTML)]


def test_returns_the_partial_result_with_most_words() -> None:
    extractors, calls = stubs(
        ("trafilatura", result(PARTIAL, 70)),
        ("readability", result(PARTIAL, 120, title="Longest")),
        ("newspaper4k", result(FAILED)),
    )
    res = ExtractorChain(extractors).extract(HTML, URL)
    assert calls == ["trafilatura", "readability", "newspaper4k"]
    assert res.status is PARTIAL
    assert res.method == "readability"
    assert res.title == "Longest"
    assert res.word_count == 120
    assert res.error == "canned partial"
    assert methods(res) == ["trafilatura", "readability", "newspaper4k"]


def test_ties_between_partial_results_go_to_the_earlier_extractor() -> None:
    extractors, _ = stubs(
        ("trafilatura", result(PARTIAL, 80)), ("readability", result(PARTIAL, 80))
    )
    assert ExtractorChain(extractors).extract(HTML, URL).method == "trafilatura"


def test_short_partial_results_are_treated_as_boilerplate() -> None:
    # A JavaScript shell's "enable JavaScript" notice must not reach the reader.
    extractors, _ = stubs(("trafilatura", result(PARTIAL, 9)), ("readability", result(FAILED)))
    res = ExtractorChain(extractors).extract(HTML, URL)
    assert res.status is FAILED
    assert res.method is None
    assert res.content_html is None
    assert res.word_count == 0
    assert res.error is not None
    assert res.error.startswith(
        "no usable content: best result (trafilatura, 9 words) is below 50 words"
    )
    assert "readability: canned failed" in res.error
    assert [a.word_count for a in res.attempts] == [9, 0]


def test_min_partial_words_boundary() -> None:
    at = ExtractorChain([FakeExtractor("trafilatura", default=result(PARTIAL, 20))])
    assert DEFAULT_MIN_PARTIAL_WORDS == 50
    assert at.extract(HTML, URL).status is FAILED
    lowered = ExtractorChain(
        [FakeExtractor("trafilatura", default=result(PARTIAL, 20))], min_partial_words=20
    )
    assert lowered.extract(HTML, URL).status is PARTIAL


def test_all_failed() -> None:
    extractors, _ = stubs(("trafilatura", result(FAILED)), ("readability", result(FAILED)))
    res = ExtractorChain(extractors).extract(HTML, URL)
    assert res.status is FAILED
    assert res.method is None
    assert res.error == (
        "no usable content (trafilatura: canned failed; readability: canned failed)"
    )
    assert len(res.attempts) == 2


def test_paywalled_is_preferred_over_failed_but_not_over_usable_partial() -> None:
    paywalled = result(ExtractionStatus.PAYWALLED, 30)
    extractors, _ = stubs(("trafilatura", result(FAILED)), ("readability", paywalled))
    assert ExtractorChain(extractors).extract(HTML, URL).status is ExtractionStatus.PAYWALLED
    extractors, _ = stubs(("trafilatura", paywalled), ("readability", result(PARTIAL, 90)))
    assert ExtractorChain(extractors).extract(HTML, URL).status is PARTIAL


def test_an_extractor_that_raises_is_recorded_and_the_chain_continues(
    log_records: list[logging.LogRecord],
) -> None:
    extractors, _ = stubs(("readability", result(OK, 200)))
    res = ExtractorChain([Raiser(), *extractors]).extract(HTML, URL)
    assert res.status is OK
    assert res.method == "readability"
    assert res.attempts[0] == ExtractionAttempt(
        method="raiser", status=FAILED, error="RuntimeError: extractor bug"
    )
    [warning] = [r for r in log_records if r.levelno == logging.WARNING]
    assert vars(warning)["extractor"] == "raiser"
    assert vars(warning)["url"] == URL


def test_method_is_always_the_extractor_name() -> None:
    # An extractor reporting a different method (e.g. a delegating renderer that
    # forgot to overwrite it) is reported under its own name.
    mislabelled = FakeExtractor("readability", default=result(OK, 200, method="trafilatura"))
    res = ExtractorChain([mislabelled]).extract(HTML, URL)
    assert res.method == "readability"
    assert methods(res) == ["readability"]


# --- Domain overrides --------------------------------------------------------


def test_domain_order_is_respected() -> None:
    extractors, calls = stubs(
        ("trafilatura", result(FAILED)),
        ("readability", result(FAILED)),
        ("newspaper4k", result(OK, 200)),
    )
    config = overrides(**{"example.com": {"order": ["newspaper4k", "readability"]}})
    res = ExtractorChain(extractors, config).extract(HTML, "https://example.com/a")
    assert calls == ["newspaper4k"]
    assert res.method == "newspaper4k"


def test_domain_order_can_leave_extractors_out() -> None:
    extractors, calls = stubs(
        ("trafilatura", result(OK, 200)),
        ("readability", result(FAILED)),
        ("newspaper4k", result(FAILED)),
    )
    config = overrides(**{"example.com": {"order": ["readability", "newspaper4k"]}})
    res = ExtractorChain(extractors, config).extract(HTML, "https://example.com/a")
    assert calls == ["readability", "newspaper4k"]
    assert res.status is FAILED


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/a", ["readability", "trafilatura"]),
        ("https://www.example.com/a", ["readability", "trafilatura"]),
        ("https://news.example.com/a", ["readability", "trafilatura"]),
        ("https://deep.news.example.com/a", ["readability", "trafilatura"]),
        ("https://WWW.Example.COM:8443/a", ["readability", "trafilatura"]),
        ("https://badexample.com/a", ["trafilatura", "readability"]),
        ("https://example.com.evil.test/a", ["trafilatura", "readability"]),
        ("https://other.test/a", ["trafilatura", "readability"]),
    ],
)
def test_overrides_match_the_domain_and_its_subdomains(url: str, expected: list[str]) -> None:
    extractors, calls = stubs(("trafilatura", result(FAILED)), ("readability", result(FAILED)))
    config = overrides(**{"www.example.com": {"order": ["readability", "trafilatura"]}})
    ExtractorChain(extractors, config).extract(HTML, url)
    assert calls == expected


def test_the_most_specific_domain_wins() -> None:
    extractors, calls = stubs(("trafilatura", result(FAILED)), ("readability", result(FAILED)))
    config = overrides(
        **{
            "example.com": {"order": ["readability"]},
            "blog.example.com": {"order": ["trafilatura"]},
        }
    )
    chain = ExtractorChain(extractors, config)
    chain.extract(HTML, "https://blog.example.com/a")
    chain.extract(HTML, "https://shop.example.com/a")
    assert calls == ["trafilatura", "readability"]


def test_summary_only_yields_skipped_without_extracting() -> None:
    extractor = FakeExtractor("trafilatura", default=result(OK, 500))
    config = overrides(**{"example.org": {"mode": "summary_only"}})
    chain = ExtractorChain([extractor], config)
    res = chain.extract(HTML, "https://www.news.example.org/a")
    assert res.status is ExtractionStatus.SKIPPED
    assert res.method is None
    assert res.content_html is None
    assert res.attempts == []
    assert res.error == "extraction skipped: news.example.org is configured summary_only"
    assert extractor.calls == []
    # The stage can see the override before fetching the page at all.
    override = chain.override_for("https://news.example.org/b")
    assert override == DomainOverride(mode="summary_only")


def test_domain_min_words_lowers_the_ok_threshold() -> None:
    # Extractors grade 80 words as partial against their default of 150.
    extractors, calls = stubs(
        ("trafilatura", result(PARTIAL, 80)), ("readability", result(OK, 200))
    )
    config = overrides(**{"example.com": {"min_words": 60}})
    res = ExtractorChain(extractors, config).extract(HTML, "https://example.com/a")
    assert calls == ["trafilatura"]
    assert res.status is OK
    assert res.error is None
    assert res.attempts == [ExtractionAttempt(method="trafilatura", status=OK, word_count=80)]


def test_domain_min_words_raises_the_ok_threshold() -> None:
    extractors, calls = stubs(("trafilatura", result(OK, 200)), ("readability", result(OK, 450)))
    config = overrides(**{"example.com": {"min_words": 400}})
    res = ExtractorChain(extractors, config).extract(HTML, "https://example.com/a")
    assert calls == ["trafilatura", "readability"]
    assert res.method == "readability"
    assert res.attempts[0] == ExtractionAttempt(
        method="trafilatura",
        status=PARTIAL,
        error="only 200 words extracted (minimum 400)",
        word_count=200,
    )


def test_domain_min_words_leaves_failed_results_alone() -> None:
    extractors, _ = stubs(("trafilatura", result(FAILED)))
    config = overrides(**{"example.com": {"min_words": 10}})
    res = ExtractorChain(extractors, config).extract(HTML, "https://example.com/a")
    assert res.attempts[0].status is FAILED
    assert res.attempts[0].error == "canned failed"


def test_render_tries_the_renderer_after_the_static_extractors() -> None:
    extractors, calls = stubs(("trafilatura", result(PARTIAL, 9)), ("readability", result(FAILED)))
    renderer = Recorder("playwright+trafilatura", result(OK, 400), calls)
    config = overrides(**{"app.example.com": {"render": "playwright"}})
    chain = ExtractorChain(extractors, config, renderer=renderer)

    res = chain.extract(HTML, "https://app.example.com/story")
    assert calls == ["trafilatura", "readability", "playwright+trafilatura"]
    assert res.status is OK
    assert res.method == "playwright+trafilatura"

    # Other domains never use the renderer.
    calls.clear()
    chain.extract(HTML, "https://example.com/story")
    assert calls == ["trafilatura", "readability"]


def test_render_is_skipped_when_a_static_extractor_succeeds() -> None:
    extractors, calls = stubs(("trafilatura", result(OK, 300)))
    renderer = Recorder("playwright+trafilatura", result(OK, 400), calls)
    config = overrides(**{"app.example.com": {"render": "playwright", "order": ["trafilatura"]}})
    ExtractorChain(extractors, config, renderer=renderer).extract(HTML, "https://app.example.com/")
    assert calls == ["trafilatura"]


def test_render_without_a_renderer_uses_static_extractors_only() -> None:
    extractors, calls = stubs(("trafilatura", result(FAILED)))
    config = overrides(**{"app.example.com": {"render": "playwright"}})
    res = ExtractorChain(extractors, config).extract(HTML, "https://app.example.com/")
    assert calls == ["trafilatura"]
    assert res.status is FAILED


@pytest.mark.parametrize("url", ["", "not a url", "https://[::1/broken", "file:///tmp/page.html"])
def test_urls_without_a_usable_host_get_the_default_chain(url: str) -> None:
    extractors, calls = stubs(("trafilatura", result(OK, 200)))
    config = overrides(**{"example.com": {"mode": "summary_only"}})
    res = ExtractorChain(extractors, config).extract(HTML, url)
    assert calls == ["trafilatura"]
    assert res.status is OK


# --- Real extractors on the synthetic fixtures -------------------------------


def real_chain() -> ExtractorChain:
    return ExtractorChain([TrafilaturaExtractor(), ReadabilityExtractor(), Newspaper4kExtractor()])


@pytest.mark.parametrize(
    "name", ["news_article.html", "blog_post.html", "cookie_banner.html", "lazy_images.html"]
)
def test_real_chain_extracts_article_fixtures_with_the_primary(name: str) -> None:
    html = (FIXTURES_DIR / name).read_text(encoding="utf-8")
    res = real_chain().extract(html, URL)
    assert res.status is OK
    assert res.method == "trafilatura"
    assert methods(res) == ["trafilatura"]


def test_real_chain_does_not_return_js_shell_boilerplate() -> None:
    html = (FIXTURES_DIR / "js_shell.html").read_text(encoding="utf-8")
    res = real_chain().extract(html, URL)
    assert res.status is FAILED
    assert res.content_html is None
    assert methods(res) == ["trafilatura", "readability", "newspaper4k"]
    # Trafilatura picks up the <noscript> notice; the fallbacks find nothing.
    assert res.attempts[0].status is PARTIAL
    assert 0 < res.attempts[0].word_count < DEFAULT_MIN_PARTIAL_WORDS


def test_real_chain_falls_back_when_trafilatura_finds_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from newsfeed.extract import trafilatura_extractor

    monkeypatch.setattr(
        trafilatura_extractor.trafilatura, "extract_with_metadata", lambda *a, **k: None
    )
    html = (FIXTURES_DIR / "news_article.html").read_text(encoding="utf-8")
    res = real_chain().extract(html, URL)
    assert res.status is OK
    assert res.method == "readability"
    assert methods(res) == ["trafilatura", "readability"]
    assert res.attempts[0].error == "no main content found"


# --- Paywall detection (#84) -------------------------------------------------

PAYWALL_DIR = FIXTURES_DIR / "paywall"
GATE_PAGE = (PAYWALL_DIR / "gate_markup.html").read_text(encoding="utf-8")
FREE_PAGE = (PAYWALL_DIR / "free_newsletter_signup.html").read_text(encoding="utf-8")
PAYWALLED = ExtractionStatus.PAYWALLED


@pytest.mark.parametrize(
    "canned",
    [
        result(PARTIAL, 90, title="Teaser title"),  # a usable-length teaser
        result(PARTIAL, 20),  # too short to be content
        result(FAILED),
    ],
)
def test_detected_paywall_turns_a_non_ok_result_into_paywalled(canned: ExtractionResult) -> None:
    extractors, _ = stubs(("trafilatura", canned), ("readability", canned))
    res = ExtractorChain(extractors).extract(GATE_PAGE, URL)
    assert res.status is PAYWALLED
    assert res.error == "paywall detected: subscriber gate element (class 'paywall')"
    # Every attempt is still recorded as the extractor reported it.
    assert [a.status for a in res.attempts] == [canned.status, canned.status]


def test_a_paywalled_teaser_keeps_its_content_and_metadata() -> None:
    teaser = result(PARTIAL, 90, title="Teaser title")
    extractors, _ = stubs(("trafilatura", teaser))
    res = ExtractorChain(extractors).extract(GATE_PAGE, URL)
    assert res.status is PAYWALLED
    assert (res.method, res.title, res.word_count) == ("trafilatura", "Teaser title", 90)
    assert res.content_html == teaser.content_html


def test_an_ok_result_on_a_paywall_page_is_left_as_it_is() -> None:
    extractors, _ = stubs(("trafilatura", result(OK, 900)))
    res = ExtractorChain(extractors).extract(GATE_PAGE, URL)
    assert res.status is OK
    assert res.error is None


def test_a_domain_min_words_decides_what_counts_as_ok_on_a_paywall_page() -> None:
    extractors, _ = stubs(("trafilatura", result(OK, 200)))
    config = overrides(**{"news.example.com": {"min_words": 300}})
    res = ExtractorChain(extractors, config).extract(GATE_PAGE, URL)
    assert res.status is PAYWALLED


def test_short_results_on_free_pages_are_not_paywalled() -> None:
    extractors, _ = stubs(("trafilatura", result(PARTIAL, 90)))
    assert ExtractorChain(extractors).extract(FREE_PAGE, URL).status is PARTIAL
    extractors, _ = stubs(("trafilatura", result(FAILED)))
    assert ExtractorChain(extractors).extract(FREE_PAGE, URL).status is FAILED


def test_an_extractor_reported_paywall_keeps_its_own_reason() -> None:
    own = result(PAYWALLED, 30).model_copy(update={"error": "extractor's own reason"})
    extractors, _ = stubs(("trafilatura", own))
    res = ExtractorChain(extractors).extract(GATE_PAGE, URL)
    assert (res.status, res.error) == (PAYWALLED, "extractor's own reason")


def test_summary_only_domains_are_not_checked_for_paywalls() -> None:
    extractors, _ = stubs(("trafilatura", result(FAILED)))
    config = overrides(**{"news.example.com": {"mode": "summary_only"}})
    res = ExtractorChain(extractors, config).extract(GATE_PAGE, URL)
    assert res.status is ExtractionStatus.SKIPPED


def test_a_detector_error_leaves_the_result_unchanged(
    monkeypatch: pytest.MonkeyPatch, log_records: list[logging.LogRecord]
) -> None:
    from newsfeed.extract import chain

    def boom(html: str, url: str) -> str | None:
        raise RuntimeError("detector bug")

    monkeypatch.setattr(chain, "detect_paywall", boom)
    extractors, _ = stubs(("trafilatura", result(PARTIAL, 90)))
    res = ExtractorChain(extractors).extract(GATE_PAGE, URL)
    assert res.status is PARTIAL
    (record,) = [r for r in log_records if r.getMessage() == "paywall detection failed"]
    assert record.error == "detector bug"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "name",
    [
        "jsonld_not_free.html",
        "microdata_not_free.html",
        "gate_markup.html",
        "notice_members_only.html",
        "notice_subscribe_to_continue.html",
    ],
)
def test_real_chain_marks_paywall_fixtures_paywalled(name: str) -> None:
    html = (PAYWALL_DIR / name).read_text(encoding="utf-8")
    res = real_chain().extract(html, URL)
    assert res.status is PAYWALLED
    assert res.error is not None
    assert res.error.startswith("paywall detected: ")


@pytest.mark.parametrize(
    "name", ["free_newsletter_signup.html", "free_article_about_paywalls.html"]
)
def test_real_chain_leaves_free_fixtures_alone(name: str) -> None:
    html = (PAYWALL_DIR / name).read_text(encoding="utf-8")
    res = real_chain().extract(html, URL)
    assert res.status is not PAYWALLED
