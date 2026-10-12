"""Tests for the readability-lxml and newspaper4k fallback extractors.

Both run against the same synthetic fixtures as ``TrafilaturaExtractor``. The
test run blocks sockets, so any attempt by a library to fetch something (the
page, images, a meta refresh target) fails the test.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from newsfeed.extract import newspaper_extractor, readability_extractor
from newsfeed.extract.common import DEFAULT_MIN_WORDS
from newsfeed.extract.newspaper_extractor import Newspaper4kExtractor
from newsfeed.extract.readability_extractor import ReadabilityExtractor
from newsfeed.models import ExtractionResult, ExtractionStatus
from newsfeed.ports import Extractor

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "extract"
FIXTURES = sorted(p.name for p in FIXTURES_DIR.glob("*.html"))
URL = "https://news.example.com/local/2025/03/story"
ARTICLES = ["news_article.html", "blog_post.html", "cookie_banner.html", "lazy_images.html"]

FallbackExtractor = type[ReadabilityExtractor] | type[Newspaper4kExtractor]
EXTRACTORS: list[FallbackExtractor] = [ReadabilityExtractor, Newspaper4kExtractor]


def load(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def assert_clean(result: ExtractionResult) -> None:
    """The content contains none of the markup the sanitiser must remove."""
    html = (result.content_html or "").lower()
    for needle in ("<script", "<style", "<iframe", "<div", "onclick", "javascript:", "srcset"):
        assert needle not in html, needle


@pytest.fixture(params=EXTRACTORS, ids=lambda cls: cls.__name__)
def cls(request: pytest.FixtureRequest) -> FallbackExtractor:
    extractor_class: FallbackExtractor = request.param
    return extractor_class


# --- Shared behaviour --------------------------------------------------------


def test_names() -> None:
    assert ReadabilityExtractor().name == "readability"
    assert Newspaper4kExtractor().name == "newspaper4k"


def test_implements_the_extractor_port(cls: FallbackExtractor) -> None:
    extractor = cls()
    assert isinstance(extractor, Extractor)
    assert extractor.min_words == DEFAULT_MIN_WORDS


def test_rejects_non_positive_min_words(cls: FallbackExtractor) -> None:
    with pytest.raises(ValueError, match="min_words"):
        cls(min_words=0)


@pytest.mark.parametrize("name", ARTICLES)
def test_article_fixtures_are_ok_and_sanitised(cls: FallbackExtractor, name: str) -> None:
    result = cls().extract(load(name), URL)
    assert result.status is ExtractionStatus.OK
    assert result.method == cls().name
    assert result.error is None
    assert result.word_count >= DEFAULT_MIN_WORDS
    assert result.word_count == len((result.text or "").split())
    assert_clean(result)


def test_news_article_content(cls: FallbackExtractor) -> None:
    result = cls().extract(load("news_article.html"), URL)
    html = result.content_html or ""
    text = result.text or ""
    assert result.title == "Marrowby council approves floating cycle bridge"
    assert "floating cycle bridge across the Wendle reservoir" in text
    # Links are absolute and get rel=noopener from the sanitiser.
    assert (
        '<a href="https://news.example.com/documents/wendle-crossing-report.pdf"'
        ' rel="noopener noreferrer">'
    ) in html
    # Site chrome outside the article body is left out. (Unlike Trafilatura,
    # both fallbacks keep the "Advertisement" label inside the body.)
    for chrome in ("Most read", "Goose blocks", "Related stories"):
        assert chrome not in text


def test_title_is_not_repeated_in_the_content(cls: FallbackExtractor) -> None:
    result = cls().extract(load("cookie_banner.html"), URL)
    assert result.title == "Library trials overnight returns robot"
    assert not (result.content_html or "").startswith("<h2>Library")


def test_cookie_banner_is_not_extracted(cls: FallbackExtractor) -> None:
    text = cls().extract(load("cookie_banner.html"), URL).text or ""
    assert "overnight drop box" in text
    for banner in ("847 partners", "Accept all", "We value your privacy"):
        assert banner not in text


def test_lazy_loaded_images_never_keep_placeholders(cls: FallbackExtractor) -> None:
    html = cls().extract(load("lazy_images.html"), URL).content_html or ""
    assert "placeholder" not in html
    assert "data:" not in html


def test_readability_keeps_lazy_loaded_images_with_absolute_sources() -> None:
    html = ReadabilityExtractor().extract(load("lazy_images.html"), URL).content_html or ""
    assert '<img src="https://news.example.com/media/lighthouses/saltmere-point.jpg"' in html


def test_js_shell_is_not_ok_and_does_not_raise(cls: FallbackExtractor) -> None:
    result = cls().extract(load("js_shell.html"), URL)
    assert result.status is ExtractionStatus.FAILED
    assert result.error == "no text extracted"
    assert result.content_html is None


@pytest.mark.parametrize("html", ["", "   ", "<html><body></body></html>", "not html at all"])
def test_empty_or_contentless_input_fails_without_raising(
    cls: FallbackExtractor, html: str
) -> None:
    result = cls().extract(html, URL)
    assert result.method == cls().name
    assert result.status in {ExtractionStatus.FAILED, ExtractionStatus.PARTIAL}
    assert result.error


@pytest.mark.parametrize("name", FIXTURES)
def test_output_is_deterministic(cls: FallbackExtractor, name: str) -> None:
    first = cls().extract(load(name), URL)
    second = cls().extract(load(name), URL)
    assert first.model_dump() == second.model_dump()


def test_status_boundary_follows_min_words(cls: FallbackExtractor) -> None:
    words = cls().extract(load("blog_post.html"), URL).word_count
    at = cls(min_words=words).extract(load("blog_post.html"), URL)
    above = cls(min_words=words + 1).extract(load("blog_post.html"), URL)
    assert at.status is ExtractionStatus.OK
    assert above.status is ExtractionStatus.PARTIAL
    assert above.error == f"only {words} words extracted (minimum {words + 1})"


# --- readability-lxml --------------------------------------------------------


def test_readability_metadata() -> None:
    result = ReadabilityExtractor().extract(load("news_article.html"), URL)
    # readability's short title drops the " | Site name" suffix.
    assert result.title == "Marrowby council approves floating cycle bridge"
    assert result.published_at is None
    assert result.lead_image is None


def test_readability_reads_the_author_meta_tag() -> None:
    page = (
        '<html><head><title>Invented</title><meta name="author" content="  Ada  Quill ">'
        "</head><body><article><p>Invented text.</p></article></body></html>"
    )
    assert ReadabilityExtractor().extract(page, URL).author == "Ada Quill"
    no_author = page.replace('<meta name="author" content="  Ada  Quill ">', "")
    assert ReadabilityExtractor().extract(no_author, URL).author is None


def test_readability_exception_yields_failed_result_and_warning(
    monkeypatch: pytest.MonkeyPatch, log_records: list[logging.LogRecord]
) -> None:
    class Broken:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise ValueError("cannot parse")

    monkeypatch.setattr(readability_extractor, "Document", Broken)
    result = ReadabilityExtractor().extract("<p>x</p>", URL)
    assert result.status is ExtractionStatus.FAILED
    assert result.error == "ValueError: cannot parse"
    [record] = [r for r in log_records if r.levelno == logging.WARNING]
    assert record.name == "newsfeed.extract.readability_extractor"
    assert vars(record)["extractor"] == "readability"
    assert vars(record)["url"] == URL
    assert vars(record)["error_type"] == "ValueError"


# --- newspaper4k -------------------------------------------------------------


def test_newspaper_drops_images_from_the_body() -> None:
    # newspaper4k's article HTML has no images; the lead image is in metadata.
    result = Newspaper4kExtractor().extract(load("lazy_images.html"), URL)
    assert "<img" not in (result.content_html or "")


def test_newspaper_metadata() -> None:
    result = Newspaper4kExtractor().extract(load("news_article.html"), URL)
    assert result.title == "Marrowby council approves floating cycle bridge"
    assert result.author == "Priya Fenwick-Oduya"
    assert result.published_at == datetime(2025, 3, 11, 6, 30, tzinfo=UTC)
    assert result.lead_image == "https://cdn.example.com/images/marrowby-bridge-lead.jpg"


def test_newspaper_does_not_follow_meta_refresh() -> None:
    # Following it would fetch another page; sockets are blocked, so a fetch
    # would surface as a failed result.
    body = " ".join(["Invented sentence about a harbour festival."] * 40)
    page = (
        '<html><head><meta http-equiv="refresh" content="0; url=https://elsewhere.example/x">'
        f"<title>Harbour festival</title></head><body><article><p>{body}</p>"
        "</article></body></html>"
    )
    result = Newspaper4kExtractor().extract(page, URL)
    assert result.status is ExtractionStatus.OK
    assert "harbour festival" in (result.text or "")


def test_newspaper_never_downloads(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("newspaper4k tried to fetch")

    import requests
    from newspaper import network

    monkeypatch.setattr(network, "get_html", no_network)
    monkeypatch.setattr(requests.Session, "send", no_network)
    result = Newspaper4kExtractor().extract(load("lazy_images.html"), URL)
    assert result.status is ExtractionStatus.OK
    # With image fetching off, the top image is the page's og:image.
    assert result.lead_image == "https://news.example.com/media/lighthouses/og-card.jpg"


def test_newspaper_exception_yields_failed_result_and_warning(
    monkeypatch: pytest.MonkeyPatch, log_records: list[logging.LogRecord]
) -> None:
    class Broken:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("parser exploded")

    monkeypatch.setattr(newspaper_extractor, "Article", Broken)
    result = Newspaper4kExtractor().extract("<p>x</p>", URL)
    assert result.status is ExtractionStatus.FAILED
    assert result.error == "RuntimeError: parser exploded"
    [record] = [r for r in log_records if r.levelno == logging.WARNING]
    assert record.name == "newsfeed.extract.newspaper_extractor"
    assert vars(record)["extractor"] == "newspaper4k"
