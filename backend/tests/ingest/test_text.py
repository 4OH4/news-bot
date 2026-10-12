"""Tests for ``newsfeed.ingest.text``."""

from __future__ import annotations

import pytest

from newsfeed.ingest.text import collapse_whitespace, html_to_text, truncate


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        ("", ""),
        ("Plain   text\n here ", "Plain text here"),
        ("<p>One</p><p>Two</p>", "One Two"),
        ("Line<br>break<br/>again", "Line break again"),
        ("<b>Bold</b> and <i>italic</i>", "Bold and italic"),
        ("Fish &amp; chips &ndash; &#8220;quoted&#8221;", "Fish & chips \u2013 \u201cquoted\u201d"),
        ("Before<script>alert(1)</script><style>p{}</style>after", "Beforeafter"),
        ("<img src='x.jpg' alt='ignored'>Caption", "Caption"),
        ("Unclosed <b>tag", "Unclosed tag"),
    ],
)
def test_html_to_text(value: str | None, expected: str) -> None:
    assert html_to_text(value) == expected


def test_truncate_leaves_short_text_alone() -> None:
    assert truncate("short", 5) == "short"


def test_truncate_cuts_at_a_word_boundary() -> None:
    result = truncate("alpha beta gamma delta", 15)
    assert result == "alpha beta…"
    assert len(result) <= 15


def test_truncate_cuts_mid_word_when_there_is_no_late_space() -> None:
    result = truncate("a supercalifragilistic", 10)
    assert result == "a superca…"
    assert len(result) == 10


def test_truncate_rejects_a_non_positive_limit() -> None:
    with pytest.raises(ValueError):
        truncate("text", 0)


def test_collapse_whitespace_keeps_markup_literally() -> None:
    assert collapse_whitespace(None) == ""
    assert collapse_whitespace("  Using <canvas>\n &amp;  more ") == "Using <canvas> &amp; more"
