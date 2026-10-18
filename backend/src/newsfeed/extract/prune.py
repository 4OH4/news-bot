"""Remove "recent articles" sidebars from a page before extraction.

Some blogs (simonwillison.net is the case that prompted this, #90) render a
list of the site's latest posts inside the main column, after the post. The
main-content extractors see it as part of the article and append it. The block
has a recognisable class name, so it is dropped from the parsed page before
Trafilatura runs; the post's paragraphs and code blocks are left alone.
"""

from __future__ import annotations

from typing import Final

from lxml import etree
from lxml import html as lxml_html
from lxml.html import HTMLParser

__all__ = ["strip_recent_articles"]

# Class tokens that name a list of other posts. Kept to names that mean exactly
# that, because a wrong match would delete article text.
_SIDEBAR_CLASSES: Final = frozenset({"recent-articles", "recent-posts", "latest-articles"})


def strip_recent_articles(html: str) -> str:
    """``html`` without any element classed as a recent-articles list.

    Returns the input unchanged when no such element is present, or when the
    page can't be parsed, so pages without the block are never re-serialised.
    """
    if not any(name in html for name in _SIDEBAR_CLASSES):
        return html
    try:
        root = lxml_html.document_fromstring(html, parser=HTMLParser())
    except (ValueError, etree.ParserError):
        return html
    doomed = [
        element
        for element in root.iter()
        if isinstance(element.tag, str)
        and _SIDEBAR_CLASSES.intersection((element.get("class") or "").split())
    ]
    if not doomed:
        return html
    for element in doomed:
        # A nested match may already have been removed with its ancestor.
        if element.getparent() is not None:
            element.drop_tree()
    return lxml_html.tostring(root, encoding="unicode")
