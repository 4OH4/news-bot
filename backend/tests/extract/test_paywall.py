"""Tests for ``detect_paywall``, using the synthetic fixtures in ``fixtures/extract/paywall``."""

from __future__ import annotations

from pathlib import Path

import pytest

from newsfeed.extract.paywall import detect_paywall

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "extract" / "paywall"
URL = "https://news.example.com/2026/10/story"


def fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def page(body: str, head: str = "") -> str:
    return f"<!DOCTYPE html><html><head>{head}</head><body>{body}</body></html>"


# --- One fixture per signal ------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("jsonld_not_free.html", "schema.org isAccessibleForFree is false (JSON-LD)"),
        ("microdata_not_free.html", "schema.org isAccessibleForFree is false (microdata)"),
        ("gate_markup.html", "subscriber gate element (class 'paywall')"),
        (
            "notice_members_only.html",
            "subscriber notice ('This post is for paid members')",
        ),
        (
            "notice_subscribe_to_continue.html",
            "subscriber notice ('available to Patchwork subscribers only')",
        ),
    ],
)
def test_each_signal_flags_its_fixture(name: str, expected: str) -> None:
    assert detect_paywall(fixture(name), URL) == expected


@pytest.mark.parametrize(
    "name",
    [
        "free_newsletter_signup.html",
        "free_article_about_paywalls.html",
        "free_metered_markup.html",
        # The existing article fixtures are free pages too.
        "../news_article.html",
        "../blog_post.html",
        "../cookie_banner.html",
        "../lazy_images.html",
        "../js_shell.html",
    ],
)
def test_free_pages_mentioning_subscriptions_are_not_flagged(name: str) -> None:
    assert detect_paywall(fixture(name), URL) is None


# --- schema.org ---------------------------------------------------------------------


def jsonld(data: str) -> str:
    return page("<p>Invented teaser.</p>", f'<script type="application/ld+json">{data}</script>')


@pytest.mark.parametrize(
    "value",
    ["false", '"false"', '"False"', '"https://schema.org/False"', '"http://schema.org/False"'],
)
def test_json_ld_false_values(value: str) -> None:
    html = jsonld(f'{{"@type": "NewsArticle", "isAccessibleForFree": {value}}}')
    assert detect_paywall(html, URL) is not None


@pytest.mark.parametrize("value", ["true", '"True"', '"https://schema.org/True"', "null", "0"])
def test_json_ld_free_or_unclear_values_are_not_flagged(value: str) -> None:
    html = jsonld(f'{{"@type": "NewsArticle", "isAccessibleForFree": {value}}}')
    assert detect_paywall(html, URL) is None


def test_json_ld_in_a_list_is_found() -> None:
    html = jsonld('[{"@type": "WebSite"}, {"@type": "Article", "isAccessibleForFree": false}]')
    assert detect_paywall(html, URL) is not None


def test_malformed_json_ld_is_ignored() -> None:
    html = jsonld('{"@type": "NewsArticle", "isAccessibleForFree": false')  # unclosed
    assert detect_paywall(html, URL) is None


def test_microdata_value_can_be_element_text() -> None:
    html = page('<div itemscope><span itemprop="isAccessibleForFree">false</span></div>')
    assert detect_paywall(html, URL) == "schema.org isAccessibleForFree is false (microdata)"


# --- Gate markup --------------------------------------------------------------------

# Asks the reader to pay, without a notice phrase, so only the gate markup counts.
_CTA = "Start your free trial today. Already a member? Sign in."


@pytest.mark.parametrize(
    "attrs",
    [
        'class="regwall"',
        'class="article-paywall-container"',
        'class="members-only-gate"',
        'class="subscribers_only"',
        'id="premium-only"',
        'class="box content-gate"',
    ],
)
def test_gate_class_and_id_names(attrs: str) -> None:
    assert detect_paywall(page(f"<section {attrs}>{_CTA}</section>"), URL) is not None


@pytest.mark.parametrize(
    "body",
    [
        # Token boundaries: "paywall" inside a longer word is something else.
        f'<div class="nopaywallads">{_CTA}</div>',
        # Empty placeholder for a metered paywall.
        '<div class="paywall"></div>',
        # Hidden, by attribute or style, itself or through an ancestor.
        f'<div class="paywall" hidden>{_CTA}</div>',
        f'<div style="display:none"><div class="paywall">{_CTA}</div></div>',
        f'<div class="paywall" style="visibility: hidden">{_CTA}</div>',
        # A paragraph of article text labelled "paywall".
        f'<p class="paywall">{_CTA}</p>',
        # A paywall-named element with no call to action.
        '<div class="paywall-wrapper">Photographs by the author.</div>',
        # Too long to be a gate: article text inside a paywall-named container.
        '<div class="paywall">' + "Subscribe " + "word " * 150 + "</div>",
    ],
)
def test_gate_markup_that_is_not_a_visible_gate(body: str) -> None:
    assert detect_paywall(page(body), URL) is None


# --- Notices --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "notice",
    [
        "Subscribe to continue reading",
        "Subscribe now to keep reading this story.",
        "To continue reading, please subscribe.",
        "This article is for subscribers only.",
        "This story is available to premium members.",
        "Subscriber-only content",
        "Subscribe to read the full article.",
        "For paid subscribers only",
    ],
)
def test_notice_phrases(notice: str) -> None:
    assert detect_paywall(page(f"<p>Teaser.</p><div><h2>{notice}</h2></div>"), URL) is not None


@pytest.mark.parametrize(
    "text",
    [
        "Subscribe to our newsletter",
        "Subscribe to them here.",
        "Already a subscriber? Sign in",
        "Become a member and support independent journalism.",
        "Continue reading below.",
        "Members of the club only meet on Sundays.",
    ],
)
def test_ordinary_subscription_mentions_are_not_notices(text: str) -> None:
    assert detect_paywall(page(f"<p>{text}</p>"), URL) is None


def test_notice_in_a_script_or_template_is_ignored() -> None:
    body = (
        "<script>var msg = 'Subscribe to continue reading';</script>"
        "<template><p>Subscribe to continue reading</p></template>"
    )
    assert detect_paywall(page(body), URL) is None


@pytest.mark.parametrize("html", ["", "   ", "not html at all", "<html></html>"])
def test_empty_or_odd_input_is_not_flagged(html: str) -> None:
    assert detect_paywall(html, URL) is None
