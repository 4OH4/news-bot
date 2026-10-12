"""Sanitise extracted article HTML for the reader, and derive its plain text.

Every extractor passes its output through ``sanitize_html`` so the reader gets
the same small, safe subset of HTML whichever extractor produced it. The
allowlist is enforced by ``nh3`` (a binding to the Rust ``ammonia`` sanitiser):

1. A permissive ``nh3`` pass drops scripts, styles, event handlers, comments and
   unknown tags, but keeps a few tags and lazy-load attributes that the next
   pass rewrites.
2. An ``lxml`` pass normalises structure: lazy-loaded images get a real ``src``,
   URLs are made absolute, links and images with unsafe schemes are unwrapped or
   dropped, layout containers become paragraphs or are unwrapped, and elements
   left empty are removed.
3. A strict ``nh3`` pass applies the final allowlist and adds
   ``rel="noopener noreferrer"`` to links. It runs last so the output is always
   exactly what ``nh3`` emits, never a re-serialisation by another parser.

``html_to_text`` turns sanitised HTML into plain text. Extractors use it for
``ExtractionResult.text`` and the word count, so word counts are comparable
across extractors (the chain in #14 ranks partial results by word count).
"""

from __future__ import annotations

import re
from html import escape, unescape
from typing import Final
from urllib.parse import urljoin, urlsplit

import nh3
from lxml import html as lxml_html
from lxml.html import HtmlElement, HTMLParser

from newsfeed.logging import get_logger

__all__ = ["ALLOWED_ATTRIBUTES", "ALLOWED_TAGS", "html_to_text", "sanitize_html"]

ALLOWED_TAGS: Final[frozenset[str]] = frozenset(
    {
        "p",
        "h2",
        "h3",
        "h4",
        "ul",
        "ol",
        "li",
        "blockquote",
        "a",
        "img",
        "figure",
        "figcaption",
        "em",
        "strong",
        "code",
        "pre",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "br",
        "hr",
    }
)
"""Tags allowed in sanitised output. Anything else is unwrapped (text kept)."""

ALLOWED_ATTRIBUTES: Final[dict[str, frozenset[str]]] = {
    "a": frozenset({"href"}),
    "img": frozenset({"src", "alt"}),
}
"""Attributes allowed per tag. ``rel`` on links is added by the sanitiser itself."""

# nh3 allows ``title`` and ``lang`` on every tag unless the "*" entry says otherwise.
_NO_GENERIC_ATTRIBUTES: Final = {"*": frozenset[str]()}

LINK_REL: Final = "noopener noreferrer"

# Tags dropped together with their content: their text is never article prose.
_CLEAN_CONTENT_TAGS: Final = frozenset(
    {
        "script",
        "style",
        "iframe",
        "noscript",
        "template",
        "object",
        "embed",
        "svg",
        "math",
        "canvas",
        "button",
        "select",
        "textarea",
        "title",
    }
)

# Equivalent tags outside the allowlist, renamed so their meaning isn't lost.
# h1 becomes h2 because the reader renders the article title itself.
_RENAMES: Final = {
    "h1": "h2",
    "h5": "h4",
    "h6": "h4",
    "b": "strong",
    "i": "em",
}

# Block-level tags outside the allowlist. Each becomes a <p> if it only holds
# inline content; otherwise its loose inline runs are wrapped in <p> and it is
# unwrapped. Either way, text in neighbouring blocks (<div>-based markup,
# <dt>/<dd> pairs, ...) keeps a block boundary instead of running together.
_CONTAINERS: Final = frozenset(
    {
        "div",
        "section",
        "article",
        "main",
        "header",
        "footer",
        "aside",
        "nav",
        "address",
        "details",
        "summary",
        "dl",
        "dt",
        "dd",
        "caption",
    }
)

_BLOCK_TAGS: Final = frozenset(
    {
        "p",
        "h2",
        "h3",
        "h4",
        "ul",
        "ol",
        "li",
        "blockquote",
        "figure",
        "figcaption",
        "pre",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "hr",
    }
)

# Elements kept even when they contain no text. Table cells are kept so that
# rows with some empty cells keep their shape.
_KEEP_EMPTY: Final = frozenset({"img", "br", "hr", "td", "th"})

# Lazy-load attributes, in priority order: a lazy-loaded image's ``src`` is
# usually a placeholder, so these win over it.
_LAZY_SRC_ATTRS: Final = ("data-src", "data-lazy-src", "data-original")

# A private-use character marking block breaks in html_to_text. Any occurrence
# in the input is removed first, so it can't be confused with a real break.
_BREAK: Final = "\ue000"

_LINK_SCHEMES: Final = frozenset({"http", "https", "mailto"})
_IMAGE_SCHEMES: Final = frozenset({"http", "https"})

_FIRST_PASS_TAGS: Final = ALLOWED_TAGS | _RENAMES.keys() | _CONTAINERS
_FIRST_PASS_ATTRIBUTES: Final = {
    **_NO_GENERIC_ATTRIBUTES,
    "a": frozenset({"href"}),
    "img": frozenset({"src", "alt", *_LAZY_SRC_ATTRS}),
}
_FINAL_ATTRIBUTES: Final = {**_NO_GENERIC_ATTRIBUTES, **ALLOWED_ATTRIBUTES}

# Tags that end a line in html_to_text. Containers are included so the function
# also separates words correctly in HTML that hasn't been sanitised.
_TEXT_BREAK_TAGS: Final = _BLOCK_TAGS | _CONTAINERS | {"br"}

_TAG_RE: Final = re.compile(r"<[^>]*>")

log = get_logger(__name__)


def sanitize_html(html: str, base_url: str, *, title: str | None = None) -> str:
    """Return ``html`` reduced to the reader's safe HTML subset.

    Args:
        html: extracted article HTML; a fragment or a whole document.
        base_url: the page URL, used to make ``href`` and ``src`` absolute.
        title: the article title. If given, a heading at the very start of the
            content that repeats it is removed, because the reader shows the
            title separately.

    Scripts, styles, iframes, event-handler attributes, comments and unsafe URL
    schemes (``javascript:``, ``data:`` and so on) are removed. Only
    ``ALLOWED_TAGS`` and ``ALLOWED_ATTRIBUTES`` survive; other tags are
    unwrapped, keeping their text. ``srcset`` is dropped, so each image has a
    single absolute ``src``. The result is deterministic for a given input.
    """
    if not html.strip():
        return ""
    permissive = nh3.clean(
        html,
        tags=_FIRST_PASS_TAGS,
        clean_content_tags=_CLEAN_CONTENT_TAGS,
        attributes=_FIRST_PASS_ATTRIBUTES,
        link_rel=None,
        url_relative="pass_through",
    )
    normalised = _normalise(permissive, base_url, title)
    if not normalised.strip() and unescape(_TAG_RE.sub("", permissive)).strip():
        _warn_text_lost(base_url, len(html))
    return nh3.clean(
        normalised,
        tags=ALLOWED_TAGS,
        clean_content_tags=_CLEAN_CONTENT_TAGS,
        attributes=_FINAL_ATTRIBUTES,
        url_schemes=_LINK_SCHEMES,
        link_rel=LINK_REL,
        url_relative=lambda url: urljoin(base_url, url),
    ).strip()


def html_to_text(html: str) -> str:
    """Return the plain text of ``html``: one line per block, spaces collapsed.

    Image ``alt`` text and attribute values aren't included, only text content.
    """
    if not html.strip():
        return ""
    root = _parse_fragment(html.replace(_BREAK, ""))
    for el in root.iter():
        if el.tag in _TEXT_BREAK_TAGS:
            # Mark block boundaries, so words in adjacent blocks stay apart while
            # newlines inside the source text are collapsed like other spaces.
            el.tail = _BREAK + (el.tail or "")
            if el.tag != "br":
                el.text = _BREAK + (el.text or "")
    lines = (" ".join(chunk.split()) for chunk in "".join(root.itertext()).split(_BREAK))
    return "\n".join(line for line in lines if line)


def _parse_fragment(html: str) -> HtmlElement:
    """Parse ``html`` into a wrapper ``<div>`` whose children are the fragment.

    ``huge_tree`` lifts libxml2's nesting limit: without it, markup nested about
    255 levels deep silently parses to an empty tree. A parser is created per
    call because lxml parsers mustn't be shared between threads.
    """
    parser = HTMLParser(huge_tree=True)
    return lxml_html.fragment_fromstring(html, create_parent="div", parser=parser)


def _warn_text_lost(url: str, input_chars: int) -> None:
    """Log that sanitising turned input with text into nothing (a parser limit)."""
    log.warning(
        "sanitiser lost all text",
        extra={
            "stage": "extract",
            "url": url,
            "error_type": "SanitiserTextLost",
            "error": "input had text but the sanitised output is empty",
            "input_chars": input_chars,
        },
    )


def _serialise_children(root: HtmlElement) -> str:
    """Serialise the content of the wrapper element, without the wrapper itself."""
    head = escape(root.text, quote=False) if root.text else ""
    return head + "".join(lxml_html.tostring(child, encoding="unicode") for child in root)


def _normalise(html: str, base_url: str, title: str | None) -> str:
    root = _parse_fragment(html)
    _fix_images(root, base_url)
    _fix_links(root, base_url)
    for old, new in _RENAMES.items():
        for el in root.iter(old):
            el.tag = new
    for el in root.iter("pre"):
        # Trafilatura emits code blocks as <pre><pre>; the inner one is the code.
        parent = el.getparent()
        if parent is not None and parent.tag == "pre":
            el.tag = "code"
    _flatten_containers(root)
    _remove_empty(root)
    if title:
        _drop_leading_title(root, title)
    return _serialise_children(root)


def _absolute_url(value: str | None, base_url: str, schemes: frozenset[str]) -> str | None:
    """Resolve ``value`` against ``base_url``; None if empty or the scheme isn't allowed."""
    if not value or not value.strip():
        return None
    url = urljoin(base_url, value.strip())
    return url if urlsplit(url).scheme.lower() in schemes else None


def _fix_images(root: HtmlElement, base_url: str) -> None:
    for img in list(root.iter("img")):
        candidates = [img.get(attr) for attr in (*_LAZY_SRC_ATTRS, "src")]
        src = next(
            (
                url
                for url in (_absolute_url(c, base_url, _IMAGE_SCHEMES) for c in candidates)
                if url
            ),
            None,
        )
        if src is None:
            # Nothing loadable: a placeholder, a data: URI or a script URL.
            img.drop_tree()
            continue
        alt = img.get("alt")
        # Rebuild the attributes so the output order is always src, alt.
        img.attrib.clear()
        img.set("src", src)
        if alt is not None:
            img.set("alt", alt)


def _fix_links(root: HtmlElement, base_url: str) -> None:
    for link in list(root.iter("a")):
        href = _absolute_url(link.get("href"), base_url, _LINK_SCHEMES)
        if href is None:
            # Keep the link text but not a link that can't be followed safely.
            link.drop_tag()
        else:
            link.set("href", href)


def _flatten_containers(root: HtmlElement) -> None:
    # Deepest first, so a container's children are already resolved.
    for el in reversed(list(root.iter(*_CONTAINERS))):
        if el is root:
            continue
        if any(child.tag in _BLOCK_TAGS for child in el):
            _wrap_inline_runs(el)
            el.drop_tag()
        else:
            el.tag = "p"


def _new_paragraph() -> HtmlElement:
    return lxml_html.Element("p")


def _wrap_inline_runs(el: HtmlElement) -> None:
    """Wrap each run of text and inline children of ``el`` in its own ``<p>``.

    Called before unwrapping a container that also holds blocks, so loose text
    can't merge with text in a neighbouring element once the container is gone.
    """
    run: HtmlElement | None = None
    if el.text and el.text.strip():
        run = _new_paragraph()
        run.text = el.text
        el.text = None
        el.insert(0, run)
    for child in list(el):
        if child is run:
            continue
        if child.tag in _BLOCK_TAGS:
            run = None
            if child.tail and child.tail.strip():
                run = _new_paragraph()
                run.text = child.tail
                child.tail = None
                child.addnext(run)
            continue
        if run is None:
            run = _new_paragraph()
            child.addprevious(run)
        run.append(child)  # moves the child together with its tail


def _has_content(el: HtmlElement) -> bool:
    if "".join(el.itertext()).strip():
        return True
    return next(el.iter("img"), None) is not None


def _remove_empty(root: HtmlElement) -> None:
    # Deepest first, so a parent emptied by removing its children goes too.
    for el in reversed(list(root.iter())):
        if el is root or el.tag in _KEEP_EMPTY:
            continue
        if _has_content(el):
            continue
        if el.tag in _BLOCK_TAGS:
            # A block separates the text around it; keep a space in its place.
            if el.tail and not el.tail[0].isspace():
                el.tail = " " + el.tail
            el.drop_tree()
        else:
            # Unwrap rather than delete, so whitespace inside an inline element
            # (``foo<em> </em>bar``) still separates the words around it.
            el.drop_tag()


def _normalise_text(text: str) -> str:
    return " ".join(text.split()).casefold()


def _drop_leading_title(root: HtmlElement, title: str) -> None:
    if (root.text or "").strip() or len(root) == 0:
        return
    first = root[0]
    if first.tag in {"h2", "h3", "h4"} and _normalise_text(first.text_content()) == (
        _normalise_text(title)
    ):
        first.drop_tree()
