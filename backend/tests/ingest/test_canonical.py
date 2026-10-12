"""Tests for ``newsfeed.ingest.canonical``. All URLs and pages are invented."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from newsfeed.ingest.canonical import canonical_from_html, normalise_url, with_canonical_url
from newsfeed.models import Article, article_id

# (input, expected) pairs for normalise_url with the default resolve_amp=True.
URL_CASES: list[tuple[str, str]] = [
    # Case, whitespace and the root path
    ("HTTPS://Example.COM/News/Story", "https://example.com/News/Story"),
    ("  https://example.com/a  ", "https://example.com/a"),
    ("https://example.com", "https://example.com/"),
    ("https://example.com/", "https://example.com/"),
    ("https://example.com.", "https://example.com/"),
    # Default and non-default ports
    ("http://example.com:80/a", "http://example.com/a"),
    ("https://example.com:443/a", "https://example.com/a"),
    ("https://example.com:8443/a", "https://example.com:8443/a"),
    ("http://example.com:443/a", "http://example.com:443/a"),
    # Fragments
    ("https://example.com/a#comments", "https://example.com/a"),
    ("https://example.com/a?id=3#top", "https://example.com/a?id=3"),
    # Trailing slashes
    ("https://example.com/a/b/", "https://example.com/a/b"),
    ("https://example.com/a/b//", "https://example.com/a/b"),
    # Tracking parameters
    (
        "https://example.com/a?utm_source=rss&utm_medium=feed&utm_campaign=x",
        "https://example.com/a",
    ),
    ("https://example.com/a?UTM_Source=rss&id=7", "https://example.com/a?id=7"),
    ("https://example.com/a?fbclid=abc123&gclid=def", "https://example.com/a"),
    ("https://example.com/a?mc_cid=1&mc_eid=2&igshid=3", "https://example.com/a"),
    ("https://example.com/a?ref=homepage&ref_src=twsrc", "https://example.com/a"),
    ("https://example.com/a?cmpid=x&ocid=y&page=2", "https://example.com/a?page=2"),
    ("https://example.com/a?pk_campaign=a&mtm_source=b&_ga=c", "https://example.com/a"),
    ("https://example.com/a?PK_Campaign=a&mtm_placement=b&id=1", "https://example.com/a?id=1"),
    # Publisher-specific or ambiguous names are kept: they may select content
    ("https://example.com/a?pk_id=5", "https://example.com/a?pk_id=5"),
    ("https://example.com/a?spm=x", "https://example.com/a?spm=x"),
    ("https://example.com/a?at_medium=x", "https://example.com/a?at_medium=x"),
    ("https://example.com/a?mtm_page=2&pk_campaign=z", "https://example.com/a?mtm_page=2"),
    ("https://example.com/a?smid=1&ito=2&s_cid=3", "https://example.com/a?ito=2&s_cid=3&smid=1"),
    # Remaining parameters are sorted, blanks kept, encoding made consistent
    ("https://example.com/s?b=2&a=1&c=3", "https://example.com/s?a=1&b=2&c=3"),
    ("https://example.com/s?a=2&a=1", "https://example.com/s?a=1&a=2"),
    ("https://example.com/s?q=hello%20world", "https://example.com/s?q=hello+world"),
    ("https://example.com/s?flag&x=1", "https://example.com/s?flag=&x=1"),
    ("https://example.com/s?utm_source=x", "https://example.com/s"),
    # Non-UTF-8 escapes survive byte-for-byte, so distinct values stay distinct
    ("https://example.com/s?q=caf%e9", "https://example.com/s?q=caf%E9"),
    ("https://example.com/s?q=caf%C3%A9", "https://example.com/s?q=caf%C3%A9"),
    ("https://example.com/s?q=café", "https://example.com/s?q=caf%C3%A9"),
    # Internationalised hosts and paths, percent-escapes and dot segments
    ("https://BÜCHER.example/a", "https://xn--bcher-kva.example/a"),
    ("https://xn--bcher-kva.example/a", "https://xn--bcher-kva.example/a"),
    ("https://example.com/café", "https://example.com/caf%C3%A9"),
    ("https://example.com/caf%c3%a9", "https://example.com/caf%C3%A9"),
    ("https://example.com/%7Euser/%41bc", "https://example.com/~user/Abc"),
    ("https://example.com/a%2Fb", "https://example.com/a%2Fb"),
    ("https://example.com/a/./b/../c", "https://example.com/a/c"),
    ("https://example.com/../a", "https://example.com/a"),
    # Hosts that aren't plain names
    ("http://[2001:DB8::1]:80/a", "http://[2001:db8::1]/a"),
    ("http://192.0.2.1:8080/a/", "http://192.0.2.1:8080/a"),
    ("https://User:Pw@Example.com/a", "https://User:Pw@example.com/a"),
    # AMP variants
    ("https://example.com/news/story/amp", "https://example.com/news/story"),
    ("https://example.com/news/story/amp/", "https://example.com/news/story"),
    ("https://example.com/amp/news/story", "https://example.com/news/story"),
    ("https://example.com/news/story.amp", "https://example.com/news/story"),
    ("https://example.com/news/story.amp.html", "https://example.com/news/story.html"),
    ("https://example.com/news/story?amp=1", "https://example.com/news/story"),
    ("https://example.com/news/story?amp", "https://example.com/news/story"),
    ("https://example.com/news/story?outputType=amp", "https://example.com/news/story"),
    ("https://example.com/amp", "https://example.com/amp"),
    ("https://example.com/news/amplifier", "https://example.com/news/amplifier"),
    (
        "https://example-com.cdn.ampproject.org/c/s/example.com/news/story/amp?utm_source=x",
        "https://example.com/news/story",
    ),
    (
        "https://example-com.cdn.ampproject.org/c/example.com/news/story",
        "http://example.com/news/story",
    ),
    ("https://www.google.com/amp/s/example.com/news/story.amp", "https://example.com/news/story"),
    # Cache URLs are recognised after normalisation (port, dot segments, AMP marker)
    ("https://www.google.com:443/amp/s/example.com/news/story", "https://example.com/news/story"),
    (
        "https://example-com.cdn.ampproject.org/x/../c/s/example.com/news/story",
        "https://example.com/news/story",
    ),
    (
        "https://example-com.cdn.ampproject.org/amp/c/s/example.com/news/story",
        "https://example.com/news/story",
    ),
    ("https://www.google.com/search?q=x&utm_source=y", "https://www.google.com/search?q=x"),
    (
        "https://example-com.cdn.ampproject.org/other",
        "https://example-com.cdn.ampproject.org/other",
    ),
    # Other schemes pass through
    ("mailto:someone@example.com", "mailto:someone@example.com"),
]


@pytest.mark.parametrize(("url", "expected"), URL_CASES)
def test_normalise_url(url: str, expected: str) -> None:
    assert normalise_url(url) == expected


def test_has_at_least_25_url_cases() -> None:
    assert len(URL_CASES) >= 25


# Awkward inputs that only need to be stable, not to have a particular output.
EXTRA_IDEMPOTENCE_INPUTS = [
    "https://example.com/amp/amp/story",
    "https://example.com/story/amp/amp",
    "https://example.com/a//amp",
    "https://example.com/story.amp.amp",
    "https://example.com/s?q=100%zz&b=%2B",
    "https://example.com:notaport/a",
    "https://a..b.example/x",
    "https://example.com/a/b/..",
]


@pytest.mark.parametrize(
    "url", [url for url, _ in URL_CASES] + [e for _, e in URL_CASES] + EXTRA_IDEMPOTENCE_INPUTS
)
def test_normalise_url_is_idempotent(url: str) -> None:
    once = normalise_url(url)
    assert normalise_url(once) == once


def test_resolve_amp_can_be_disabled() -> None:
    url = "https://example.com/news/story/amp?amp=1&utm_source=x"
    assert normalise_url(url, resolve_amp=False) == "https://example.com/news/story/amp?amp=1"


@pytest.mark.parametrize("url", ["", "/relative/path", "example.com/a", "https://"])
def test_normalise_url_rejects_non_absolute(url: str) -> None:
    with pytest.raises(ValueError, match="not an absolute URL"):
        normalise_url(url)


# --- canonical_from_html ------------------------------------------------------

PAGE = "https://www.example.com/news/2026/invented-story?utm_source=feed"


def page(head: str, body: str = "<p>Invented article text.</p>") -> str:
    """Wrap ``head`` and ``body`` fragments in a minimal synthetic page."""
    return (
        f"<!doctype html><html><head><title>Invented</title>{head}</head><body>{body}</body></html>"
    )


@pytest.fixture
def records() -> Iterator[list[logging.LogRecord]]:
    """Capture records from the ``newsfeed`` logger, which doesn't propagate to root."""
    captured: list[logging.LogRecord] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = _Handler(level=logging.DEBUG)
    logger = logging.getLogger("newsfeed")
    logger.addHandler(handler)
    try:
        yield captured
    finally:
        logger.removeHandler(handler)


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (
            '<link rel="canonical" href="https://www.example.com/news/2026/invented-story/">',
            "https://www.example.com/news/2026/invented-story",
        ),
        # Relative and root-relative hrefs are resolved against the page URL
        ('<link rel="canonical" href="/news/canon">', "https://www.example.com/news/canon"),
        (
            '<link rel="canonical" href="canon-story">',
            "https://www.example.com/news/2026/canon-story",
        ),
        ('<link rel="canonical" href="//www.example.com/x">', "https://www.example.com/x"),
        # <base href> changes what relative hrefs resolve against
        (
            '<base href="https://www.example.com/archive/"><link rel="canonical" href="story">',
            "https://www.example.com/archive/story",
        ),
        # Attribute order, case, extra rel tokens, self-closing tags and entities
        (
            '<LINK HREF="https://www.example.com/A?b=2&amp;a=1#x" REL="Canonical" />',
            "https://www.example.com/A?a=1&b=2",
        ),
        (
            '<link rel="alternate canonical" href="/two-tokens">',
            "https://www.example.com/two-tokens",
        ),
        # og:url is the fallback, via property= or name=
        (
            '<meta property="og:url" content="https://www.example.com/og">',
            "https://www.example.com/og",
        ),
        ('<meta name="og:url" content="/og-relative">', "https://www.example.com/og-relative"),
        # link rel=canonical wins over og:url, wherever they appear
        (
            '<meta property="og:url" content="/og"><link rel="canonical" href="/canon">',
            "https://www.example.com/canon",
        ),
        # Unusable values are skipped in favour of the next candidate
        (
            '<link rel="canonical" href=""><meta property="og:url" content="/og">',
            "https://www.example.com/og",
        ),
        ('<link rel="canonical" href="   ">', None),
        ('<link rel="canonical" href="javascript:void(0)">', None),
        ('<link rel="canonical" href="mailto:x@example.com">', None),
        ('<link rel="canonical" href="http://:80/no-host">', None),
        # The first of several canonicals wins
        (
            '<link rel="canonical" href="/first"><link rel="canonical" href="/second">',
            "https://www.example.com/first",
        ),
        # A canonical pointing at the site root from an article is ignored
        ('<link rel="canonical" href="https://www.example.com/">', None),
        (
            '<link rel="canonical" href="/"><meta property="og:url" content="/og">',
            "https://www.example.com/og",
        ),
        # Missing tags and other rels
        ("", None),
        ('<link rel="stylesheet" href="/style.css">', None),
        ('<meta property="og:title" content="Invented headline">', None),
    ],
)
def test_canonical_from_html(head: str, expected: str | None) -> None:
    assert canonical_from_html(page(head), PAGE) == expected


def test_canonical_from_html_handles_empty_and_broken_markup() -> None:
    assert canonical_from_html("", PAGE) is None
    assert canonical_from_html("not html at all", PAGE) is None
    broken = '<html><head><link rel=canonical href=/unquoted><meta charset="utf-8"'
    assert canonical_from_html(broken, PAGE) == "https://www.example.com/unquoted"


def test_canonical_from_html_accepts_root_canonical_on_root_page() -> None:
    html = page('<link rel="canonical" href="https://www.example.com/">')
    assert canonical_from_html(html, "https://example.com/?ref=nav") == "https://www.example.com/"


def test_root_guard_counts_the_query() -> None:
    # On a WordPress plain-permalink page the query selects the article: a root
    # canonical there would merge every post into one id, while a "/?p=" canonical
    # from a pretty-permalink page is the real article.
    root = page('<link rel="canonical" href="https://blog.example/">')
    assert canonical_from_html(root, "https://blog.example/?p=123") is None

    plain = page('<link rel="canonical" href="https://blog.example/?p=123">')
    expected = "https://blog.example/?p=123"
    assert canonical_from_html(plain, "https://blog.example/2026/10/invented-post") == expected


def test_distinct_non_utf8_queries_stay_distinct() -> None:
    assert normalise_url("https://example.com/s?q=caf%E9") != normalise_url(
        "https://example.com/s?q=caf%E8"
    )


def test_canonical_from_html_rejects_relative_base_url() -> None:
    with pytest.raises(ValueError, match="not an absolute URL"):
        canonical_from_html(page('<link rel="canonical" href="/x">'), "/news/story")


def test_canonical_tag_in_body_is_used() -> None:
    html = page("", body='<p>Invented.</p><link rel="canonical" href="/late">')
    assert canonical_from_html(html, PAGE) == "https://www.example.com/late"


def test_cross_domain_canonical_is_accepted_and_logged(records: list[logging.LogRecord]) -> None:
    html = page('<link rel="canonical" href="https://original.example.org/story?utm_medium=x">')

    assert canonical_from_html(html, PAGE) == "https://original.example.org/story"

    [record] = [r for r in records if r.getMessage() == "cross-domain canonical"]
    assert record.levelno == logging.INFO
    assert record.__dict__["url"] == PAGE
    assert record.__dict__["canonical"] == "https://original.example.org/story"
    assert record.__dict__["stage"] == "ingest"


@pytest.mark.parametrize(
    "canonical",
    ["https://example.com/news/x", "https://WWW.example.com/news/x", "/news/x"],
)
def test_same_site_canonical_is_not_logged(
    canonical: str, records: list[logging.LogRecord]
) -> None:
    canonical_from_html(page(f'<link rel="canonical" href="{canonical}">'), PAGE)
    assert not [r for r in records if r.getMessage() == "cross-domain canonical"]


# --- with_canonical_url -------------------------------------------------------


def make_article(url: str = "https://example.com/news/story?utm_source=rss") -> Article:
    """Build an article whose canonical URL is still the raw feed URL."""
    return Article(
        canonical_url=url,
        url=url,
        source_id="example",
        title="Invented headline",
        published_at=datetime(2026, 10, 11, 9, 0, tzinfo=UTC),
        fetched_at=datetime(2026, 10, 11, 10, 0, tzinfo=UTC),
    )


def test_with_canonical_url_rebuilds_article_with_new_id() -> None:
    article = make_article()
    canonical = normalise_url(article.url)

    updated = with_canonical_url(article, canonical)

    assert updated.canonical_url == canonical
    assert updated.id == article_id(canonical)
    assert updated.id != article.id
    assert updated.model_dump(exclude={"id", "canonical_url"}) == article.model_dump(
        exclude={"id", "canonical_url"}
    )


def test_with_canonical_url_returns_same_article_when_unchanged() -> None:
    article = make_article("https://example.com/news/story")
    assert with_canonical_url(article, article.canonical_url) is article
