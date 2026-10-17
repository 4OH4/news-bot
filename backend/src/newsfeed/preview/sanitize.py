"""Make stored article HTML safe to embed in the offline preview.

The extractors already sanitise, but the preview promises "no JavaScript, nothing
loaded from other sites", so it re-checks rather than trusting stored content.
"""

from __future__ import annotations

import lxml.html

__all__ = ["prepare_content"]

_DROP_ALWAYS = ("script", "style", "link", "iframe", "frame", "object", "embed", "meta", "base")
_IMAGE_TAGS = ("img", "picture", "source", "video", "audio")
_URL_ATTRS = ("href", "src", "srcset", "action", "formaction")
_ACTIVE_SCHEMES = ("javascript:", "vbscript:", "data:text/html")


def prepare_content(html: str, *, images: bool) -> str:
    """Return ``html`` as an embeddable fragment with active content removed.

    Drops scripts, styles, frames and embeds, event-handler attributes and
    ``javascript:`` links. With ``images=False`` it also drops images and media,
    plus ``figure`` elements left empty by that.
    """
    if not html.strip():
        return ""
    root = lxml.html.fragment_fromstring(html, create_parent="div")
    tags = _DROP_ALWAYS if images else _DROP_ALWAYS + _IMAGE_TAGS
    for element in list(root.iter(*tags)):
        element.drop_tree()
    if not images:
        for figure in list(root.iter("figure")):
            if not (figure.text_content().strip() or len(figure)):
                figure.drop_tree()
    for element in root.iter():
        for name in list(element.attrib):
            lowered = name.lower()
            value = element.attrib[name].strip().lower()
            if lowered.startswith("on") or (
                lowered in _URL_ATTRS and value.startswith(_ACTIVE_SCHEMES)
            ):
                del element.attrib[name]
    parts = [root.text or ""]
    parts.extend(lxml.html.tostring(child, encoding="unicode") for child in root)
    return "".join(parts)
