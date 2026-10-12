"""Tests for ``TrafilaturaExtractor`` against synthetic page fixtures."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from trafilatura.settings import Document

from newsfeed.extract import trafilatura_extractor
from newsfeed.extract.trafilatura_extractor import DEFAULT_MIN_WORDS, TrafilaturaExtractor
from newsfeed.models import ExtractionResult, ExtractionStatus
from newsfeed.ports import Extractor

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "extract"
FIXTURES = sorted(p.name for p in FIXTURES_DIR.glob("*.html"))
URL = "https://news.example.com/local/2025/03/story"


def load(name: str) -> str:
    """Return the synthetic page ``name`` from ``tests/fixtures/extract/``."""
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def extract(name: str, **kwargs: Any) -> ExtractionResult:
    return TrafilaturaExtractor(**kwargs).extract(load(name), URL)


def assert_clean(result: ExtractionResult) -> None:
    """The content contains none of the markup the sanitiser must remove."""
    html = (result.content_html or "").lower()
    for needle in ("<script", "<style", "<iframe", "onclick", "javascript:", "srcset", "data:"):
        assert needle not in html, needle


def test_has_at_least_five_fixtures() -> None:
    assert len(FIXTURES) >= 5


def test_implements_the_extractor_port() -> None:
    extractor = TrafilaturaExtractor()
    assert isinstance(extractor, Extractor)
    assert extractor.name == "trafilatura"
    assert extractor.min_words == DEFAULT_MIN_WORDS == 150


def test_rejects_non_positive_min_words() -> None:
    with pytest.raises(ValueError, match="min_words"):
        TrafilaturaExtractor(min_words=0)


# --- Fixtures ----------------------------------------------------------------


def test_news_article_metadata() -> None:
    result = extract("news_article.html")
    assert result.status is ExtractionStatus.OK
    assert result.method == "trafilatura"
    assert result.error is None
    assert result.title == "Marrowby council approves floating cycle bridge"
    assert result.author == "Priya Fenwick-Oduya"
    assert result.published_at == datetime(2025, 3, 11, 6, 30, tzinfo=UTC)
    assert result.lead_image == "https://cdn.example.com/images/marrowby-bridge-lead.jpg"


def test_news_article_content_without_publisher_chrome() -> None:
    result = extract("news_article.html")
    html = result.content_html or ""
    text = result.text or ""
    assert result.word_count >= DEFAULT_MIN_WORDS
    assert result.word_count == len(text.split())
    assert "floating cycle bridge across the Wendle reservoir" in text
    assert "<h2>Costs and timetable</h2>" in html
    assert "<blockquote>" in html
    # Navigation, related links, ads and footer are left out.
    for chrome in ("Most read", "Goose blocks", "Related stories", "Advertisement", "Privacy"):
        assert chrome not in text
    # The title is shown by the reader, so it isn't repeated at the top.
    assert not html.startswith("<h2>Marrowby")
    # Links and images are absolute; links get rel=noopener.
    assert (
        '<a href="https://news.example.com/documents/wendle-crossing-report.pdf"'
        ' rel="noopener noreferrer">'
    ) in html
    assert '<img src="https://news.example.com/images/marrowby-bridge-lead.jpg"' in html
    # The javascript: link loses its href but keeps its text.
    assert "Sign up for our weekly local newsletter" in text
    assert_clean(result)


def test_blog_post_keeps_formatting() -> None:
    result = extract("blog_post.html")
    html = result.content_html or ""
    assert result.status is ExtractionStatus.OK
    assert result.title == "Teaching a sourdough starter to keep a diary"
    assert result.author == "Hal Brindlecombe"
    assert result.published_at == datetime(2024, 11, 17, tzinfo=UTC)
    for tag in ("<h2>", "<h3>", "<ul>", "<li>", "<blockquote>", "<pre><code>"):
        assert tag in html, tag
    # Reader comments aren't part of the article.
    assert "kombucha" not in (result.text or "")
    assert_clean(result)


def test_cookie_banner_is_not_extracted() -> None:
    result = extract("cookie_banner.html")
    text = result.text or ""
    assert result.status is ExtractionStatus.OK
    assert result.title == "Library trials overnight returns robot"
    assert "overnight drop box" in text
    for banner in ("847 partners", "Accept all", "We value your privacy", "cookie settings"):
        assert banner not in text
    assert_clean(result)


def test_lazy_loaded_images_get_absolute_sources() -> None:
    result = extract("lazy_images.html")
    html = result.content_html or ""
    assert result.status is ExtractionStatus.OK
    assert '<img src="https://news.example.com/media/lighthouses/saltmere-point.jpg"' in html
    assert '<img src="https://images.example.org/lighthouses/little-skerrow.jpg"' in html
    assert "placeholder" not in html
    assert result.lead_image == "https://news.example.com/media/lighthouses/og-card.jpg"
    assert_clean(result)


def test_js_shell_is_not_ok_and_does_not_raise() -> None:
    result = extract("js_shell.html")
    assert result.status in {ExtractionStatus.FAILED, ExtractionStatus.PARTIAL}
    assert result.word_count < DEFAULT_MIN_WORDS
    assert result.error
    assert_clean(result)


@pytest.mark.parametrize("name", FIXTURES)
def test_output_is_deterministic(name: str) -> None:
    first = extract(name)
    second = TrafilaturaExtractor().extract(load(name), URL)
    assert first.model_dump() == second.model_dump()


# --- Status ------------------------------------------------------------------


def test_status_boundary_follows_min_words() -> None:
    words = extract("blog_post.html").word_count
    at_threshold = extract("blog_post.html", min_words=words)
    above_threshold = extract("blog_post.html", min_words=words + 1)
    assert at_threshold.status is ExtractionStatus.OK
    assert at_threshold.error is None
    assert above_threshold.status is ExtractionStatus.PARTIAL
    assert above_threshold.error == f"only {words} words extracted (minimum {words + 1})"
    assert above_threshold.content_html == at_threshold.content_html


@pytest.mark.parametrize("html", ["", "   ", "<html><body></body></html>", "not html at all"])
def test_empty_or_contentless_input_fails_without_raising(html: str) -> None:
    result = TrafilaturaExtractor().extract(html, URL)
    assert result.method == "trafilatura"
    assert result.status in {ExtractionStatus.FAILED, ExtractionStatus.PARTIAL}


def test_no_content_from_trafilatura_is_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        trafilatura_extractor.trafilatura, "extract_with_metadata", lambda *a, **k: None
    )
    result = TrafilaturaExtractor().extract(load("news_article.html"), URL)
    assert result.status is ExtractionStatus.FAILED
    assert result.error == "no main content found"
    assert result.word_count == 0


def test_exception_yields_failed_result_and_structured_warning(
    monkeypatch: pytest.MonkeyPatch, log_records: list[logging.LogRecord]
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("parser exploded")

    monkeypatch.setattr(trafilatura_extractor.trafilatura, "extract_with_metadata", boom)

    result = TrafilaturaExtractor().extract("<p>whatever</p>", URL)

    assert result.status is ExtractionStatus.FAILED
    assert result.method == "trafilatura"
    assert result.error == "RuntimeError: parser exploded"
    assert result.content_html is None
    warnings = [r for r in log_records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    record = warnings[0]
    assert record.name == "newsfeed.extract.trafilatura_extractor"
    assert vars(record)["stage"] == "extract"
    assert vars(record)["url"] == URL
    assert vars(record)["extractor"] == "trafilatura"
    assert vars(record)["error_type"] == "RuntimeError"
    assert vars(record)["error"] == "parser exploded"


def test_relative_and_unsafe_metadata_images(monkeypatch: pytest.MonkeyPatch) -> None:
    page = load("blog_post.html")
    with_relative = page.replace(
        "<head>", '<head><meta property="og:image" content="/img/card.png">', 1
    )
    with_unsafe = page.replace(
        "<head>", '<head><meta property="og:image" content="javascript:alert(1)">', 1
    )
    extractor = TrafilaturaExtractor()
    assert extractor.extract(with_relative, URL).lead_image == (
        "https://news.example.com/img/card.png"
    )
    assert extractor.extract(with_unsafe, URL).lead_image is None


WHITESPACE_EMPHASIS_PAGE = (
    "<html><head><title>Invented fair</title></head><body><article>"
    "<h1>Invented fair</h1>"
    "<p>Organisers said the<em> </em>event would return to the invented village green next"
    " spring, with stalls, a brass band and a competition for the largest marrow.</p>"
    "<p>Volunteers spent the<strong> </strong>weekend repainting the bandstand and mending"
    " the bunting that a storm had torn down during the previous autumn.</p>"
    "</article></body></html>"
)


def test_whitespace_only_inline_elements_keep_words_apart() -> None:
    raw = trafilatura_extractor.trafilatura.extract(
        WHITESPACE_EMPHASIS_PAGE, url=URL, output_format="html", include_formatting=True
    )
    # The page reproduces the Trafilatura output that triggered the bug.
    assert raw is not None
    assert re.search(r"the<(i|em)> </\1>event", raw)

    result = TrafilaturaExtractor().extract(WHITESPACE_EMPHASIS_PAGE, URL)
    text = result.text or ""
    assert "the event" in text
    assert "the weekend" in text
    assert "theevent" not in text
    assert "theweekend" not in text
    assert "theevent" not in (result.content_html or "")
    assert result.word_count == len(text.split())


def test_content_without_text_is_failed_but_keeps_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = Document(title="  A   title ")
    document.text = "<html><body><p><script>only()</script></p></body></html>"
    monkeypatch.setattr(
        trafilatura_extractor.trafilatura, "extract_with_metadata", lambda *a, **k: document
    )
    result = TrafilaturaExtractor().extract("<p>ignored</p>", URL)
    assert result.status is ExtractionStatus.FAILED
    assert result.error == "no text extracted"
    assert result.title == "A title"
    assert result.content_html is None
    assert result.text is None
    assert result.word_count == 0
