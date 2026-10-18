"""Tests for ``detect_paywall``, using the synthetic fixtures in ``fixtures/extract/paywall``."""

from __future__ import annotations

from pathlib import Path

import pytest

from newsfeed.extract.paywall import PaywallSignal, detect_paywall, find_paywall

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
        ("gate_open_dialog.html", "subscriber gate element (class 'paywall')"),
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
        "free_promos_and_hidden_gate.html",
        "free_closed_dialog_gate.html",
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


@pytest.mark.parametrize(
    ("name", "conclusive"),
    [
        ("jsonld_not_free.html", False),
        ("microdata_not_free.html", False),
        ("gate_markup.html", True),
        ("gate_open_dialog.html", True),
        ("notice_members_only.html", True),
        ("notice_subscribe_to_continue.html", True),
    ],
)
def test_only_visible_gates_are_conclusive(name: str, conclusive: bool) -> None:
    signal = find_paywall(fixture(name), URL)
    assert signal is not None
    assert signal.conclusive is conclusive
    assert signal.reason == detect_paywall(fixture(name), URL)


def test_a_visible_gate_wins_over_the_schema_org_marker() -> None:
    gate = fixture("gate_markup.html").replace(
        "</head>",
        '<script type="application/ld+json">{"isAccessibleForFree": false}</script></head>',
    )
    assert find_paywall(gate, URL) == PaywallSignal(
        "subscriber gate element (class 'paywall')", conclusive=True
    )


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


@pytest.mark.parametrize(
    "data",
    [
        '{"@graph": [{"@type": "WebSite"}, {"@type": "Article", "isAccessibleForFree": false}]}',
        '{"@type": "Article", "hasPart": {"isAccessibleForFree": false}}',
        '{"hasPart": [{"cssSelector": ".a"}, {"isAccessibleForFree": "False"}]}',
        '{"@type": "WebPage", "mainEntity": {"@type": "Article", "isAccessibleForFree": false}}',
        '{"@type": "WebPage", "mainEntity": {"hasPart": {"isAccessibleForFree": false}}}',
    ],
)
def test_json_ld_main_entity_and_its_parts_are_checked(data: str) -> None:
    assert detect_paywall(jsonld(data), URL) is not None


@pytest.mark.parametrize(
    "data",
    [
        '{"isAccessibleForFree": true, "relatedLink": [{"isAccessibleForFree": false}]}',
        '{"@type": "Article", "citation": {"isAccessibleForFree": false}}',
        '{"@type": "Article", "isPartOf": {"@type": "Periodical", "isAccessibleForFree": false}}',
        '{"@type": "Article", "hasPart": {"relatedLink": {"isAccessibleForFree": false}}}',
    ],
)
def test_json_ld_on_related_items_is_not_flagged(data: str) -> None:
    assert detect_paywall(jsonld(data), URL) is None


def test_malformed_json_ld_is_ignored() -> None:
    html = jsonld('{"@type": "NewsArticle", "isAccessibleForFree": false')  # unclosed
    assert detect_paywall(html, URL) is None


def test_microdata_value_can_be_element_text() -> None:
    html = page('<div itemscope><span itemprop="isAccessibleForFree">false</span></div>')
    assert detect_paywall(html, URL) == "schema.org isAccessibleForFree is false (microdata)"


# --- Gate markup --------------------------------------------------------------------

# Asks the reader to pay, without a notice phrase, so only the gate markup counts.
_CTA = 'Start your free trial today. Already a member? <a href="/login">Sign in</a>.'
# Article text long enough that a block before it doesn't end the article.
_MORE_ARTICLE = "<p>" + "More of the invented article. " * 10 + "</p>"


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
        f'<div class="paywall" aria-hidden="true">{_CTA}</div>',
        f'<div class="paywall hidden">{_CTA}</div>',
        # A <dialog> without "open" isn't rendered, so neither is a gate inside it.
        f'<dialog class="paywall">{_CTA}</dialog>',
        f'<dialog><div class="paywall">{_CTA}</div></dialog>',
        f'<div class="modal d-none"><div class="paywall">{_CTA}</div></div>',
        # A paywall-named element whose words ask, but which offers no link or button.
        '<div class="paywall">Start your free trial today. Already a member? Sign in.</div>',
        # A members-area link above the article, not a gate that ends it.
        f'<div class="members-only-link">Members area: <a href="/login">log in</a></div>'
        f"<main><article>{_MORE_ARTICLE}</article></main>",
        '<div class="members-only-link">Members area: log in</div>',
        # Promos outside the article: an aside, a sidebar, navigation, a footer.
        f'<aside><div class="paywall">{_CTA}</div></aside>',
        f'<div class="post-sidebar"><div class="paywall">{_CTA}</div></div>',
        f'<nav><div class="members-only">{_CTA}</div></nav>',
        f'<div role="complementary"><div class="paywall">{_CTA}</div></div>',
        f'<footer><div class="paywall">{_CTA}</div></footer>',
        # A promo box in the middle of an article.
        f'<article><p>Teaser.</p><div class="paywall-promo">{_CTA}</div>{_MORE_ARTICLE}</article>',
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


@pytest.mark.parametrize(
    "body",
    [
        # A gate at the end of the article, inside an article that has a sidebar.
        f'<article class="post has-sidebar"><p>Teaser.</p><div class="paywall">{_CTA}</div>'
        "</article>",
        # A short footer note after the gate doesn't stop it ending the article.
        f'<main><p>Teaser.</p><div class="paywall">{_CTA}</div><p>Share this post.</p></main>',
        # The action can be a button or a submit input.
        '<div class="paywall">Keep reading with a trial. <button>Subscribe</button></div>',
        '<form class="regwall">Register to read on. <input type="submit" value="Sign up"></form>',
        # Text after the article ends (comments, related posts) doesn't count.
        f'<article><p>Teaser.</p><div class="paywall">{_CTA}</div></article>{_MORE_ARTICLE}',
    ],
)
def test_gates_that_end_the_article(body: str) -> None:
    assert detect_paywall(page(body), URL) is not None


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
    gate = f'<div><h2>{notice}</h2><a href="/subscribe">Subscribe</a></div>'
    assert detect_paywall(page(f"<p>Teaser.</p>{gate}"), URL) is not None


_ACTION = '<a href="/subscribe">Subscribe</a>'


def test_notice_in_an_open_dialog_is_a_gate() -> None:
    body = f"<p>Teaser.</p><dialog open><h2>Subscribe to continue reading</h2>{_ACTION}</dialog>"
    assert detect_paywall(page(body), URL) is not None


@pytest.mark.parametrize(
    "body",
    [
        # A notice with nothing to act on: a promo line, not a gate.
        "<p>Teaser.</p><div><h2>This article is for subscribers only.</h2></div>",
        # Sidebar and aside promos mentioning paid subscribers.
        "<aside><p>Ad-free bonus episodes for paid subscribers only.</p></aside>",
        f"<aside><p>Bonus episodes for paid subscribers only. {_ACTION}</p></aside>",
        f'<div class="sidebar"><p>Bonus episodes for paid subscribers only. {_ACTION}</p></div>',
        # Hidden by a class.
        f'<div class="hidden"><h2>Subscribe to continue reading</h2>{_ACTION}</div>',
        # A closed <dialog> isn't rendered; the same notice in an open one is (below).
        f"<p>Teaser.</p><dialog><h2>Subscribe to continue reading</h2>{_ACTION}</dialog>",
        # A promo box in the middle of the article, with more article after it.
        f"<article><p>Teaser.</p><div><h3>For paid subscribers only</h3>{_ACTION}</div>"
        f"{_MORE_ARTICLE}</article>",
    ],
)
def test_notices_that_are_not_gates(body: str) -> None:
    assert detect_paywall(page(body), URL) is None


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
