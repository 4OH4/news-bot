"""Score one extraction against its ``eval/urls.yaml`` expectations (#18).

An extraction is **clean** when none of these checks fails:

* the extractor produced content (status ``ok`` or ``partial``);
* the title matches ``expected_title`` (normalised ``difflib`` ratio of at least 0.9);
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
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from newsfeed.eval.urls import EvalEntry
from newsfeed.models import ExtractionResult, ExtractionStatus

__all__ = [
    "BOILERPLATE_MAX_HITS",
    "TITLE_MIN_RATIO",
    "Failure",
    "Score",
    "load_boilerplate",
    "normalise",
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
class Score:
    """The outcome of the checks for one extraction."""

    failures: tuple[Failure, ...] = ()
    details: tuple[str, ...] = ()
    """Short human-readable reasons, parallel to ``failures``; no article text."""

    @property
    def clean(self) -> bool:
        """True when every check passed."""
        return not self.failures


def normalise(text: str) -> str:
    """Fold ``text`` for comparison: NFKC, straight quotes, one space, case-insensitive."""
    folded = unicodedata.normalize("NFKC", text).translate(_QUOTES).casefold()
    return _SPACE.sub(" ", folded).strip()


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


def _boilerplate_hits(text: str, phrases: Iterable[str]) -> int:
    return sum(text.count(phrase) for phrase in phrases if phrase)


def score_result(
    entry: EvalEntry, result: ExtractionResult, boilerplate: Iterable[str] = ()
) -> Score:
    """Check ``result`` against ``entry``'s expectations.

    ``boilerplate`` holds normalised phrases (see ``load_boilerplate``). If the
    extractor produced no content, only that failure is reported, because the
    other checks would all fail for the same reason.
    """
    if result.status not in {ExtractionStatus.OK, ExtractionStatus.PARTIAL}:
        return Score((Failure.EXTRACTION_FAILED,), (f"status {result.status}",))

    failures: list[Failure] = []
    details: list[str] = []

    def fail(kind: Failure, detail: str) -> None:
        failures.append(kind)
        details.append(detail)

    if entry.expected_title is not None:
        if not result.title:
            fail(Failure.TITLE_MISMATCH, "no title extracted")
        else:
            ratio = difflib.SequenceMatcher(
                None, normalise(entry.expected_title), normalise(result.title)
            ).ratio()
            if ratio < TITLE_MIN_RATIO:
                fail(Failure.TITLE_MISMATCH, f"title similarity {ratio:.2f} < {TITLE_MIN_RATIO}")

    text = normalise(result.text or "")
    if entry.must_contain:
        missing = [i for i, s in enumerate(entry.must_contain, 1) if normalise(s) not in text]
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

    hits = _boilerplate_hits(text, boilerplate)
    if hits > BOILERPLATE_MAX_HITS:
        fail(Failure.BOILERPLATE, f"{hits} boilerplate phrases > {BOILERPLATE_MAX_HITS}")

    return Score(tuple(failures), tuple(details))
