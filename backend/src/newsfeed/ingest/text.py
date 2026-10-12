"""Plain-text helpers for feed metadata (titles, summaries, authors).

Feeds put HTML, entities and stray whitespace in fields that the reader shows
as plain text. These helpers are pure and shared by the ingesters.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

__all__ = ["collapse_whitespace", "html_to_text", "truncate"]

_WHITESPACE = re.compile(r"\s+")

# Elements whose content is never visible text.
_SKIP = frozenset({"script", "style", "template", "noscript", "head", "title"})
# Elements that separate words, so "<p>a</p><p>b</p>" reads "a b", not "ab".
_BREAKS = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
        "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header",
        "hr", "img", "li", "main", "nav", "ol", "p", "pre", "section", "table", "td",
        "th", "tr", "ul",
    }
)  # fmt: skip


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._skip_depth += 1
        elif tag in _BREAKS:
            self.parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BREAKS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BREAKS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def collapse_whitespace(value: str | None) -> str:
    """Return ``value`` with runs of whitespace collapsed to one space and trimmed.

    For values that are already plain text: nothing else is changed, so text
    such as "<canvas>" or "&amp;" survives literally.
    """
    return _WHITESPACE.sub(" ", value).strip() if value else ""


def html_to_text(value: str | None) -> str:
    """Return ``value`` as plain text: tags removed, entities decoded, whitespace collapsed.

    Plain text passes through unchanged apart from whitespace, so it is safe to
    call on fields that may or may not contain markup.
    """
    if not value:
        return ""
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return _WHITESPACE.sub(" ", "".join(parser.parts)).strip()


def truncate(text: str, max_chars: int) -> str:
    """Shorten ``text`` to at most ``max_chars`` characters, ending in "…" if cut.

    Cuts at the last word boundary when there is one in the second half of the
    limit, so a summary doesn't end mid-word.
    """
    if max_chars < 1:
        raise ValueError("max_chars must be at least 1")
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars - 1]
    space = cut.rfind(" ")
    if space >= max_chars // 2:
        cut = cut[:space]
    return cut.rstrip() + "…"
