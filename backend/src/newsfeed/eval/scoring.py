"""Score one extraction against its ``eval/urls.yaml`` expectations (#18).

An extraction is **clean** when none of these checks fails:

* the extractor produced content (status ``ok`` or ``partial``);
* the title matches ``expected_title`` (normalised ``difflib`` ratio of at least 0.9).
  A page has two titles, the visible ``<h1>`` and the metadata title (``og:title``,
  ``<title>``). The extractors return the metadata one, so when the extracted title
  is the page's metadata title the page's ``<h1>`` is accepted too (#62);
* every ``must_contain`` snippet is in the text (whitespace, case and quotes normalised);
* the word count is inside ``word_range``;
* boilerplate phrases (``eval/boilerplate.txt``) occur at most ``BOILERPLATE_MAX_HITS`` times.

A check whose expectation is absent from the entry is skipped. Scores hold only
categories and counts, never extracted text, because reports are committed to a
public repo.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from lxml import html as lxml_html
from lxml.etree import ParserError

from newsfeed.eval.urls import EvalEntry
from newsfeed.models import ExtractionResult, ExtractionStatus

__all__ = [
    "BOILERPLATE_MAX_HITS",
    "TITLE_MIN_RATIO",
    "Checks",
    "Failure",
    "Score",
    "load_boilerplate",
    "normalise",
    "page_titles",
    "score_result",
]

TITLE_MIN_RATIO: Final = 0.9
"""Least ``difflib`` similarity between expected and extracted title."""

BOILERPLATE_MAX_HITS: Final = 2
"""Most boilerplate phrase occurrences a clean extraction may contain.

A couple of hits are tolerated because an article may legitimately mention
cookies or a newsletter; page furniture that leaks in usually repeats.
"""

# Code points: left/right/low/reversed single quotes and prime; double quote
# equivalents; en and em dash.
_QUOTES = str.maketrans(
    {
        **dict.fromkeys((0x2018, 0x2019, 0x201A, 0x201B, 0x2032), "'"),
        **dict.fromkeys((0x201C, 0x201D, 0x201E, 0x201F, 0x2033), '"'),
        **dict.fromkeys((0x2013, 0x2014), "-"),
    }
)
_SPACE = re.compile(r"\s+")


class Failure(StrEnum):
    """Why an extraction isn't clean. The values are the report's failure categories."""

    EXTRACTION_FAILED = "extraction_failed"
    TITLE_MISMATCH = "title_mismatch"
    MISSING_SNIPPET = "missing_snippet"
    WORD_COUNT_LOW = "word_count_low"
    WORD_COUNT_HIGH = "word_count_high"
    BOILERPLATE = "boilerplate"


@dataclass(frozen=True)
class Checks:
    """The measured values behind a score, for the detailed run log.

    Holds numbers, snippet positions and boilerplate phrases (which come from the
    repo's own files), never extracted text.
    """

    title_ratio: float | None = None
    """Best similarity to ``expected_title``; None if either title is absent."""
    title_via: str | None = None
    """``extracted`` or ``h1``: which title the best similarity came from."""
    snippets_found: tuple[int, ...] = ()
    """1-based positions in ``must_contain`` of the snippets found in the text."""
    snippets_missing: tuple[int, ...] = ()
    """1-based positions in ``must_contain`` of the snippets not found."""
    boilerplate: tuple[tuple[str, int], ...] = ()
    """``(phrase, occurrences)`` for each boilerplate phrase found, sorted by phrase."""

    @property
    def boilerplate_hits(self) -> int:
        """Total boilerplate occurrences, as compared with ``BOILERPLATE_MAX_HITS``."""
        return sum(n for _, n in self.boilerplate)


@dataclass(frozen=True)
class Score:
    """The outcome of the checks for one extraction."""

    failures: tuple[Failure, ...] = ()
    details: tuple[str, ...] = ()
    """Short human-readable reasons, parallel to ``failures``; no article text."""
    checks: Checks | None = None
    """The values measured; None when extraction failed and the checks were skipped."""

    @property
    def clean(self) -> bool:
        """True when every check passed."""
        return not self.failures


def normalise(text: str) -> str:
    """Fold ``text`` for comparison: NFKC, straight quotes, one space, case-insensitive."""
    folded = unicodedata.normalize("NFKC", text).translate(_QUOTES).casefold()
    return _SPACE.sub(" ", folded).strip()


def _text_of(element: lxml_html.HtmlElement) -> str:
    return " ".join(element.text_content().split())


# lxml refuses a str that starts with an XML declaration naming an encoding;
# the text is already decoded, so the declaration is dropped rather than parsing
# bytes (which would guess the charset again for pages without a declaration).
_XML_DECLARATION = re.compile(r"^\s*<\?xml\b[^>]*\?>")


def page_titles(html: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return ``(metadata titles, h1 headings)`` found in ``html``.

    Metadata titles are ``og:title``, ``twitter:title`` and ``<title>``. Both
    lists may be empty, and unparseable HTML gives two empty tuples.
    """
    try:
        root = lxml_html.fromstring(_XML_DECLARATION.sub("", html, count=1))
    except (ValueError, ParserError):
        return (), ()
    meta = [
        content
        for prop in ("og:title", "twitter:title")
        for node in root.xpath(f'//meta[@property="{prop}" or @name="{prop}"]')
        if (content := " ".join(str(node.get("content") or "").split()))
    ]
    meta.extend(t for node in root.xpath("//title") if (t := _text_of(node)))
    headings = [t for node in root.xpath("//h1") if (t := _text_of(node))]
    return tuple(meta), tuple(headings)


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalise(a), normalise(b)).ratio()


def _title_match(expected: str, extracted: str, html: str | None) -> tuple[float, str]:
    """Best similarity of ``expected`` to the extracted title or, if allowed, an h1.

    The page's ``<h1>`` counts only when the extracted title is itself one of the
    page's metadata titles, so a wrong extraction can't pass via an unrelated h1.
    """
    best = (_ratio(expected, extracted), "extracted")
    if html is None or best[0] >= TITLE_MIN_RATIO:
        return best
    meta, headings = page_titles(html)
    if any(_ratio(extracted, m) >= TITLE_MIN_RATIO for m in meta):
        for heading in headings:
            best = max(best, (_ratio(expected, heading), "h1"))
    return best


def load_boilerplate(path: Path) -> tuple[str, ...]:
    """Read boilerplate phrases from ``path``, one per line, normalised.

    Blank lines and lines starting with ``#`` are ignored. A missing file means
    no phrases, so the check is skipped rather than failing the harness.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return ()
    phrases = (line.strip() for line in lines)
    return tuple(normalise(p) for p in phrases if p and not p.startswith("#"))


def _boilerplate_matches(text: str, phrases: Iterable[str]) -> Counter[str]:
    """Count occurrences of each boilerplate phrase, each stretch of text at most once.

    One alternation, longest phrase first, so "newsletter" doesn't also count
    inside "sign up for our newsletter". Word boundaries stop it matching inside
    longer words such as "newsletters". Keys are the phrases as given.
    """
    unique = sorted({p for p in phrases if p}, key=len, reverse=True)
    if not unique:
        return Counter()
    # One capture group per phrase: the group that matched names the phrase, so
    # the keys always come from the phrase list, never from the page text.
    pattern = r"\b(?:" + "|".join(f"({re.escape(p)})" for p in unique) + r")\b"
    return Counter(
        unique[m.lastindex - 1]
        for m in re.finditer(pattern, text, re.IGNORECASE)
        if m.lastindex is not None
    )


def _boilerplate_hits(text: str, phrases: Iterable[str]) -> int:
    """Total boilerplate phrase occurrences (see ``_boilerplate_matches``)."""
    return sum(_boilerplate_matches(text, phrases).values())


def score_result(
    entry: EvalEntry,
    result: ExtractionResult,
    boilerplate: Iterable[str] = (),
    html: str | None = None,
) -> Score:
    """Check ``result`` against ``entry``'s expectations.

    ``boilerplate`` holds normalised phrases (see ``load_boilerplate``). If the
    extractor produced no content, only that failure is reported, because the
    other checks would all fail for the same reason. ``html`` is the page the
    extractor ran on; with it the title check can fall back to the page's ``<h1>``.
    """
    if result.status not in {ExtractionStatus.OK, ExtractionStatus.PARTIAL}:
        return Score((Failure.EXTRACTION_FAILED,), (f"status {result.status}",))

    failures: list[Failure] = []
    details: list[str] = []

    def fail(kind: Failure, detail: str) -> None:
        failures.append(kind)
        details.append(detail)

    ratio: float | None = None
    via: str | None = None
    if entry.expected_title is not None:
        if not result.title:
            fail(Failure.TITLE_MISMATCH, "no title extracted")
        else:
            ratio, via = _title_match(entry.expected_title, result.title, html)
            if ratio < TITLE_MIN_RATIO:
                fail(Failure.TITLE_MISMATCH, f"title similarity {ratio:.2f} < {TITLE_MIN_RATIO}")

    text = normalise(result.text or "")
    found = [i for i, s in enumerate(entry.must_contain, 1) if normalise(s) in text]
    missing = [i for i in range(1, len(entry.must_contain) + 1) if i not in found]
    if missing:
        numbers = ", ".join(str(i) for i in missing)
        fail(
            Failure.MISSING_SNIPPET,
            f"{len(missing)} of {len(entry.must_contain)} snippets missing (#{numbers})",
        )

    if entry.word_range is not None:
        low, high = entry.word_range
        if result.word_count < low:
            fail(Failure.WORD_COUNT_LOW, f"{result.word_count} words < {low}")
        elif result.word_count > high:
            fail(Failure.WORD_COUNT_HIGH, f"{result.word_count} words > {high}")

    matches = _boilerplate_matches(text, boilerplate)
    hits = sum(matches.values())
    if hits > BOILERPLATE_MAX_HITS:
        fail(Failure.BOILERPLATE, f"{hits} boilerplate phrases > {BOILERPLATE_MAX_HITS}")

    checks = Checks(
        title_ratio=None if ratio is None else round(ratio, 3),
        title_via=via,
        snippets_found=tuple(found),
        snippets_missing=tuple(missing),
        boilerplate=tuple(sorted(matches.items())),
    )
    return Score(tuple(failures), tuple(details), checks)
