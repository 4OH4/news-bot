"""Paywall and members-only preview detection (ADR-0003 decision 2, FR-6).

``detect_paywall`` reads a page we already fetched and says whether it is a
subscriber gate or a teaser for one. It never fetches anything and never tries
to reach the content behind the gate (CLAUDE.md rule 6): it only recognises
what the publisher showed us.

It looks for three generic signals, in order:

1. **schema.org markup**: ``isAccessibleForFree`` set to false, in JSON-LD or
   microdata. Publishers add it so search engines can tell a paywall from
   cloaking, which makes it the most reliable signal.
2. **Gate markup**: a visible container whose class or id names a paywall
   (``paywall``, ``regwall``, ``subscribers-only``, ``members-only``, ...) and
   whose short text asks the reader to subscribe or sign in, such as the
   region Substack puts after a paid post's preview.
3. **A gate notice**: a short block of text such as "Subscribe to continue
   reading" or "This post is for paid members only".

The signals are deliberately conservative, because a free article flagged as
paywalled would lose its content in the feed. Empty or hidden gate elements
(placeholders many free pages carry for a metered paywall) and paragraphs
labelled "paywall" (some publishers mark metered article text that way) don't
count, and
notice phrases only count in short blocks, not in a paragraph of an article
that happens to discuss paywalls. Generic "Subscribe" buttons and newsletter
sign-ups match none of the signals.

The extractor chain decides what a detection means: it marks a page
``paywalled`` only when no extractor produced a full-length ``ok`` result.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Final

from lxml import etree
from lxml import html as lxml_html
from lxml.html import HtmlElement, HTMLParser

__all__ = ["MAX_GATE_WORDS", "MAX_NOTICE_WORDS", "detect_paywall"]

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
_GATE_TAGS: Final = ("div", "section", "aside", "form", "dialog")

# Words a gate uses to ask the reader to pay or sign in.
_CALL_TO_ACTION: Final = re.compile(
    r"\b(?:subscri\w*|members?(?:hip)?|sign (?:in|up)|log ?in|upgrade|unlock|free trial)\b",
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
    {"p", "h1", "h2", "h3", "h4", "h5", "h6", "div", "section", "aside", "span", "strong"}
)
_HIDDEN_STYLE: Final = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)


def detect_paywall(html: str, url: str) -> str | None:
    """A short reason if ``html`` (fetched from ``url``) is a paywall or teaser, else None.

    Pure, and doesn't raise on malformed HTML or JSON-LD: an unparseable page
    is simply not detected. ``url`` is part of the signature so callers pass
    the page's context, but today's signals are generic and don't use it.
    """
    root = _parse(html)
    if root is None:
        return None
    return _schema_org_signal(root) or _gate_markup_signal(root) or _notice_signal(root)


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
        if any(_is_false(value) for value in _values_of(data, "isaccessibleforfree")):
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


def _values_of(data: object, key: str) -> Iterator[object]:
    """Every value under ``key`` (case-insensitive) anywhere in parsed JSON."""
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(k, str) and k.lower() == key:
                yield v
            yield from _values_of(v, key)
    elif isinstance(data, list):
        for item in data:
            yield from _values_of(item, key)


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
    that asks the reader to subscribe or sign in, and must be shown.
    """
    text = _collapse(el.text_content())
    if not text or len(text.split()) > MAX_GATE_WORDS:
        return False
    return _CALL_TO_ACTION.search(text) is not None and _rendered(el)


# --- Signal 3: gate notice ------------------------------------------------------------


def _notice_signal(root: HtmlElement) -> str | None:
    # Cheap check on the whole text first: most pages have no notice at all.
    if _NOTICE.search(_collapse(root.text_content())) is None:
        return None
    for el in root.iter(*_NOTICE_TAGS):
        text = _collapse(el.text_content())
        if not text or len(text.split()) > MAX_NOTICE_WORDS:
            continue
        match = _NOTICE.search(text)
        if match is not None and _rendered(el):
            return f"subscriber notice ({match.group(0)!r})"
    return None


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _rendered(el: HtmlElement) -> bool:
    """False if ``el`` or an ancestor is never shown: script-like, or hidden."""
    node: HtmlElement | None = el
    while node is not None:
        if node.tag in _NON_RENDERED:
            return False
        if node.get("hidden") is not None or (node.get("aria-hidden") or "").lower() == "true":
            return False
        if _HIDDEN_STYLE.search(node.get("style") or ""):
            return False
        node = node.getparent()
    return True
