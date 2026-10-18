"""Paywall and members-only preview detection (ADR-0003 decision 2, FR-6).

``detect_paywall`` reads a page we already fetched and says whether it is a
subscriber gate or a teaser for one. It never fetches anything and never tries
to reach the content behind the gate (CLAUDE.md rule 6): it only recognises
what the publisher showed us.

It looks for three generic signals:

1. **Gate markup** (conclusive): a visible container whose class or id names a
   paywall (``paywall``, ``regwall``, ``subscribers-only``, ``members-only``,
   ...) and whose short text asks the reader to subscribe or sign in, such as
   the region Substack puts after a paid post's preview.
2. **A gate notice** (conclusive): a short block of text such as "Subscribe to
   continue reading" or "This post is for paid members only".
3. **schema.org markup** (not conclusive): ``isAccessibleForFree`` set to
   false on the page's main entity or one of its parts, in JSON-LD or
   microdata. Metered sites (The Verge, MIT Technology Review) put it on
   articles they serve in full too, so on its own it only says the page *may*
   be a teaser.

The two visible signals are deliberately strict, because they override an
``ok`` extraction, and a free article flagged as paywalled would lose its
content in the feed. A gate or notice must be shown (not hidden by markup, a
``hidden`` class or a closed ``<dialog>``), must sit in the page's content rather than an aside,
navigation, header, footer or sidebar, must offer a subscribe or sign-in link,
button or form, and must end the article: little paragraph text may follow it.
Empty placeholders that many free pages carry for a metered paywall,
paragraphs labelled "paywall" (some publishers mark metered article text that
way), notice phrases inside long paragraphs of an article that discusses
paywalls, generic "Subscribe" buttons and newsletter sign-ups match none of
the signals.

The extractor chain decides what a detection means: a conclusive signal marks
the page ``paywalled`` whatever the extractors found, and the schema.org signal
only when no extractor produced an ``ok`` result.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final

from lxml import etree
from lxml import html as lxml_html
from lxml.html import HtmlElement, HTMLParser

__all__ = [
    "MAX_GATE_WORDS",
    "MAX_NOTICE_WORDS",
    "MAX_TRAILING_WORDS",
    "PaywallSignal",
    "detect_paywall",
    "find_paywall",
]

MAX_NOTICE_WORDS: Final = 40
"""Most words a block can have for a notice phrase in it to count as a gate notice.

Gate notices are a heading or a sentence or two; an article paragraph quoting
one is longer.
"""

MAX_GATE_WORDS: Final = 120
"""Most words a paywall-named element can have to count as a gate.

A gate holds a heading, a pitch and a few buttons; more text than this is
article content that a publisher's markup happens to label "paywall".
"""

MAX_TRAILING_WORDS: Final = 40
"""Most words of paragraph text that may follow a gate in its article.

A gate ends the visible article; a block with more article text after it is a
link or promo in the middle of the page, not a gate.
"""

_JSON_LD_TYPES: Final = frozenset({"application/ld+json"})

# Class and id tokens that name a paywall or subscriber gate. Matched against
# whole tokens split on "-" and "_", so "paywall-cta" matches and
# "nopaywallads" doesn't.
_GATE_TOKEN: Final = re.compile(
    r"(?:^|[-_])(?:"
    r"paywall(?:ed)?|regwall|subscriberwall|"
    r"(?:subscribers?|members?|premium|paid)[-_]?only|"
    r"subscriber[-_]?gate|members?[-_]?gate|content[-_]?gate"
    r")(?:$|[-_])",
)

# Container elements a gate is built from. Paragraphs are left out on purpose:
# some publishers mark every metered article paragraph with class "paywall".
_GATE_TAGS: Final = ("div", "section", "form", "dialog")

# Words a gate uses to ask the reader to pay or sign in.
_CALL_TO_ACTION: Final = re.compile(
    r"\b(?:subscri\w*|members?(?:hip)?|sign (?:in|up)|log ?in|upgrade|unlock|(?:free )?trial)\b",
    re.IGNORECASE,
)

# Notices that only appear on a gate. Each requires a phrase about being
# blocked ("to continue reading", "for paid members only"), not just the word
# "subscribe", so newsletter sign-ups and "Subscribe" buttons don't match.
_AUDIENCE: Final = r"(?:paid |premium |paying )?(?:subscribers?|members?)"
_NOTICE: Final = re.compile(
    "|".join(
        [
            # "Subscribe to continue reading", "Subscribe now to keep reading"
            r"\bsubscribe (?:now |today )?to (?:continue|keep) reading\b",
            # "To continue reading, subscribe", "... please sign in"
            r"\bto (?:continue|keep) reading(?: this \w+)?,? (?:please )?"
            r"(?:subscribe|become a|sign up|log in|sign in)\b",
            # "This post is for paid members only", "This article is for subscribers"
            r"\bthis (?:post|article|story|content|page|piece) is (?:only )?"
            rf"(?:for|available to|available for|reserved for|exclusive to) {_AUDIENCE}\b",
            # "currently available to LWN subscribers only", "for subscribers only"
            rf"\b(?:available|reserved|open) (?:only )?to (?:\w+ )?{_AUDIENCE} only\b",
            rf"\bfor {_AUDIENCE} only\b",
            # "Subscriber-only content", "Members-only article"
            r"\b(?:subscribers?|members?)[- ]only (?:content|article|post|story)\b",
            # "Subscribe to read the full article", "... to unlock this story"
            r"\bsubscribe to (?:read|unlock|access) (?:the (?:full|rest|whole)|this)\b",
        ]
    ),
    re.IGNORECASE,
)

# Elements whose content is never rendered as page text.
_NON_RENDERED: Final = frozenset({"script", "style", "noscript", "template", "head"})
_NOTICE_TAGS: Final = frozenset(
    {"p", "h1", "h2", "h3", "h4", "h5", "h6", "div", "section", "span", "strong"}
)
_HIDDEN_STYLE: Final = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)
# Class tokens that hide an element in common CSS conventions.
_HIDDEN_CLASSES: Final = frozenset({"hidden", "is-hidden", "d-none"})

# Page regions outside the article, where a subscription promo is not a gate.
_OUTSIDE_ARTICLE_TAGS: Final = frozenset({"aside", "nav", "header", "footer"})
_OUTSIDE_ARTICLE_ROLES: Final = frozenset({"complementary", "navigation", "banner", "contentinfo"})
# "sidebar", "post-sidebar", "sidebar-promo", but not a layout flag such as
# "has-sidebar" on the article itself.
_OUTSIDE_ARTICLE_TOKEN: Final = re.compile(r"^(?!(?:has|with|no)[-_])(?:\w+[-_])?sidebar(?:$|[-_])")

# The article a gate must end: the nearest of these around it.
_ARTICLE_TAGS: Final = frozenset({"article", "main", "body"})

# Elements a reader acts on: a gate offers one that subscribes or signs in.
_ACTION_TAGS: Final = ("a", "button", "input")


@dataclass(frozen=True)
class PaywallSignal:
    """What ``find_paywall`` found on a page.

    ``conclusive`` is True for a visible gate or gate notice, which means the
    page served to us is a teaser whatever its length. It is False for the
    schema.org marker alone, which metered sites also put on articles they
    serve in full.
    """

    reason: str
    conclusive: bool


def detect_paywall(html: str, url: str) -> str | None:
    """A short reason if ``html`` (fetched from ``url``) is a paywall or teaser, else None.

    Pure, and doesn't raise on malformed HTML or JSON-LD: an unparseable page
    is simply not detected. ``url`` is part of the signature so callers pass
    the page's context, but today's signals are generic and don't use it.
    """
    signal = find_paywall(html, url)
    return None if signal is None else signal.reason


def find_paywall(html: str, url: str) -> PaywallSignal | None:
    """The strongest paywall signal on the page, or None; see ``detect_paywall``.

    Conclusive signals (gate markup, then a notice) come before the schema.org
    marker, so a caller can tell a visible gate from a marker alone.
    """
    root = _parse(html)
    if root is None:
        return None
    reason = _gate_markup_signal(root) or _notice_signal(root)
    if reason is not None:
        return PaywallSignal(reason, conclusive=True)
    reason = _schema_org_signal(root)
    if reason is not None:
        return PaywallSignal(reason, conclusive=False)
    return None


def _parse(html: str) -> HtmlElement | None:
    if not html.strip():
        return None
    # A parser per call: lxml parsers mustn't be shared between threads.
    parser = HTMLParser(huge_tree=True)
    try:
        return lxml_html.document_fromstring(html, parser=parser)
    except (etree.ParserError, ValueError):
        return None


# --- Signal 1: schema.org isAccessibleForFree -------------------------------------


def _schema_org_signal(root: HtmlElement) -> str | None:
    for script in root.iter("script"):
        if (script.get("type") or "").strip().lower() not in _JSON_LD_TYPES:
            continue
        try:
            data = json.loads(script.text or "", strict=False)
        except ValueError:
            continue
        if any(_is_false(_get(node, "isaccessibleforfree")) for node in _main_nodes(data)):
            return "schema.org isAccessibleForFree is false (JSON-LD)"
    for el in root.xpath("//*[@itemprop]"):
        if "isaccessibleforfree" not in (el.get("itemprop") or "").lower().split():
            continue
        value = el.get("content")
        if value is None:
            value = el.text_content()
        if _is_false(value):
            return "schema.org isAccessibleForFree is false (microdata)"
    return None


def _main_nodes(data: object) -> Iterator[dict[str, object]]:
    """The JSON-LD nodes that describe this page: its entities and their parts.

    These are the top-level nodes (or ``@graph`` members), a ``mainEntity``
    they name, and the ``hasPart`` sections of any of those. Other nested items,
    such as a ``relatedLink`` or ``citation``, describe other pages, so their
    ``isAccessibleForFree`` says nothing about this one.
    """
    for node in _nodes(data):
        yield node
        yield from _nodes(_get(node, "haspart"))
        for entity in _nodes(_get(node, "mainentity")):
            yield entity
            yield from _nodes(_get(entity, "haspart"))


def _nodes(data: object) -> Iterator[dict[str, object]]:
    """The objects in ``data``: itself, a list's items, and an ``@graph``'s members."""
    if isinstance(data, list):
        for item in data:
            yield from _nodes(item)
    elif isinstance(data, dict):
        yield data
        yield from _nodes(_get(data, "@graph"))


def _get(node: object, key: str) -> object:
    """``node[key]``, matching ``key`` case-insensitively, or None."""
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str) and k.lower() == key:
                return v
    return None


def _is_false(value: object) -> bool:
    if isinstance(value, bool):
        return not value
    if isinstance(value, str):
        # schema.org also allows the Boolean URLs, e.g. "https://schema.org/False".
        return value.strip().lower().rsplit("/", 1)[-1] in {"false", "no"}
    return False


# --- Signal 2: gate markup ----------------------------------------------------------


def _gate_markup_signal(root: HtmlElement) -> str | None:
    for el in root.iter(*_GATE_TAGS):
        for attr in ("class", "id"):
            token = _gate_token(el.get(attr))
            if token is not None and _is_gate(el):
                return f"subscriber gate element ({attr} {token!r})"
    return None


def _gate_token(value: str | None) -> str | None:
    for token in (value or "").lower().split():
        if _GATE_TOKEN.search(token):
            return token
    return None


def _is_gate(el: HtmlElement) -> bool:
    """Whether a paywall-named element looks like a gate a reader would see.

    Some publishers put a ``paywall`` class on the article's own paragraphs to
    mark metered content, so a gate must be a short block (not article prose)
    that offers a way to subscribe or sign in, shown where it ends the article.
    """
    text = _text(el)
    if not text or len(text.split()) > MAX_GATE_WORDS:
        return False
    return _has_action(el) and _ends_article(el)


# --- Signal 3: gate notice ------------------------------------------------------------


def _notice_signal(root: HtmlElement) -> str | None:
    # Cheap check on the whole text first: most pages have no notice at all.
    if _NOTICE.search(_text(root)) is None:
        return None
    for el in root.iter(*_NOTICE_TAGS):
        text = _text(el)
        if not text or len(text.split()) > MAX_NOTICE_WORDS:
            continue
        match = _NOTICE.search(text)
        if match is not None and _notice_block(el) is not None:
            return f"subscriber notice ({match.group(0)!r})"
    return None


def _notice_block(el: HtmlElement) -> HtmlElement | None:
    """The gate a notice belongs to: the smallest short block around it with an action.

    A gate notice comes with a subscribe or sign-in link, button or form; a
    phrase such as "for paid subscribers only" in a promo without one isn't a
    gate. The block must also end the article.
    """
    node: HtmlElement | None = el
    while node is not None and node.tag not in _ARTICLE_TAGS:
        if len(_text(node).split()) > MAX_GATE_WORDS:
            return None
        if _has_action(node):
            return node if _ends_article(node) else None
        node = node.getparent()
    return None


# --- Shared checks for a visible gate ---------------------------------------------------


def _has_action(el: HtmlElement) -> bool:
    """Whether ``el`` holds a link, button or submit input that subscribes or signs in."""
    for action in el.iter(*_ACTION_TAGS):
        if action.tag == "input":
            if (action.get("type") or "").lower() not in {"submit", "button"}:
                continue
            label = action.get("value") or ""
        else:
            label = _text(action)
        label = _collapse(f"{label} {action.get('aria-label') or ''}")
        if _CALL_TO_ACTION.search(label) is not None:
            return True
    return False


def _ends_article(el: HtmlElement) -> bool:
    """Whether ``el`` is a shown part of the article, with little text after it.

    The article is ``el``'s nearest ``article``, ``main`` or ``body``. A block
    in an aside, navigation, header, footer or sidebar is a promo, and one
    followed by more than ``MAX_TRAILING_WORDS`` words of paragraph text in the
    article is a link or promo in the middle of the page.
    """
    if not _rendered(el) or _outside_article(el):
        return False
    scope = next((a for a in el.iterancestors() if a.tag in _ARTICLE_TAGS), None)
    if scope is None:
        return True
    trailing = 0
    for para in el.xpath("following::p"):
        if scope not in para.iterancestors():
            break  # in document order, so every later paragraph is outside too
        if not _rendered(para) or _outside_article(para):
            continue
        trailing += len(_text(para).split())
        if trailing > MAX_TRAILING_WORDS:
            return False
    return True


def _outside_article(el: HtmlElement) -> bool:
    """Whether ``el`` is in an aside, navigation, header, footer or sidebar."""
    node: HtmlElement | None = el
    while node is not None:
        if node.tag in _OUTSIDE_ARTICLE_TAGS:
            return True
        if (node.get("role") or "").lower() in _OUTSIDE_ARTICLE_ROLES:
            return True
        for attr in ("class", "id"):
            tokens = (node.get(attr) or "").lower().split()
            if any(_OUTSIDE_ARTICLE_TOKEN.search(token) for token in tokens):
                return True
        node = node.getparent()
    return False


def _text(el: HtmlElement) -> str:
    """``el``'s text with whitespace collapsed, and a space between elements.

    ``text_content()`` joins adjacent elements directly, so a heading "... only"
    followed by a "Subscribe" link would read "onlySubscribe".
    """
    return _collapse(" ".join(el.itertext()))


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _rendered(el: HtmlElement) -> bool:
    """False if ``el`` or an ancestor is never shown: script-like, hidden or a closed dialog."""
    node: HtmlElement | None = el
    while node is not None:
        if node.tag in _NON_RENDERED:
            return False
        # A <dialog> stays hidden until script opens it; metered sites ship one
        # ready-made and open it only when the reader runs out of free articles.
        if node.tag == "dialog" and node.get("open") is None:
            return False
        if node.get("hidden") is not None or (node.get("aria-hidden") or "").lower() == "true":
            return False
        if _HIDDEN_STYLE.search(node.get("style") or ""):
            return False
        if _HIDDEN_CLASSES.intersection((node.get("class") or "").lower().split()):
            return False
        node = node.getparent()
    return True
