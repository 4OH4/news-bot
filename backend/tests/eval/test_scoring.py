"""Tests for ``newsfeed.eval.scoring``: one check at a time."""

from __future__ import annotations

from pathlib import Path

import pytest

from newsfeed.eval.scoring import (
    BOILERPLATE_MAX_HITS,
    Failure,
    _boilerplate_hits,
    load_boilerplate,
    normalise,
    page_titles,
    score_result,
)
from newsfeed.eval.urls import EvalEntry
from newsfeed.models import ExtractionResult, ExtractionStatus

LQ, RQ, SQ = chr(0x201C), chr(0x201D), chr(0x2019)

ENTRY = EvalEntry(
    id="story",
    url="https://news.example.com/story",
    expected_title="Invented Headline",
    must_contain=["first invented sentence", 'the "quoted" part'],
    word_range=(5, 50),
)


def result(**overrides: object) -> ExtractionResult:
    fields: dict[str, object] = {
        "status": ExtractionStatus.OK,
        "title": "Invented Headline",
        "text": f"The first   invented sentence. And the {LQ}quoted{RQ} part follows.",
        "word_count": 10,
    }
    fields.update(overrides)
    return ExtractionResult.model_validate(fields)


def test_all_checks_pass_is_clean() -> None:
    score = score_result(ENTRY, result())

    assert score.clean
    assert score.failures == ()


def test_normalise_folds_case_quotes_and_whitespace() -> None:
    assert normalise(f"  The {LQ}Quoted{RQ}\n{SQ}s  thing ") == 'the "quoted" \'s thing'


def test_title_just_below_threshold_fails_and_near_match_passes() -> None:
    assert score_result(ENTRY, result(title="invented  headline!")).clean
    score = score_result(ENTRY, result(title="Something else entirely"))
    assert score.failures == (Failure.TITLE_MISMATCH,)


def test_missing_title_fails_the_title_check() -> None:
    assert score_result(ENTRY, result(title=None)).failures == (Failure.TITLE_MISMATCH,)


def test_missing_snippet_is_reported_with_count_but_not_text() -> None:
    score = score_result(ENTRY, result(text="Only the first invented sentence."))

    assert score.failures == (Failure.MISSING_SNIPPET,)
    assert "1 of 2" in score.details[0]
    assert "quoted" not in score.details[0]


@pytest.mark.parametrize(
    ("words", "expected"),
    [(4, Failure.WORD_COUNT_LOW), (51, Failure.WORD_COUNT_HIGH), (5, None), (50, None)],
)
def test_word_range_bounds_are_inclusive(words: int, expected: Failure | None) -> None:
    score = score_result(ENTRY, result(word_count=words))

    assert score.failures == (() if expected is None else (expected,))


def test_boilerplate_over_threshold_fails() -> None:
    phrases = ("accept cookies",)
    ok_text = 'The first invented sentence. The "quoted" part. ' + "accept cookies " * (
        BOILERPLATE_MAX_HITS
    )
    bad_text = ok_text + "accept cookies"

    assert score_result(ENTRY, result(text=ok_text), phrases).clean
    assert score_result(ENTRY, result(text=bad_text), phrases).failures == (Failure.BOILERPLATE,)


def test_overlapping_boilerplate_phrases_are_counted_once() -> None:
    phrases = ("newsletter", "sign up for our newsletter")
    text = "Body text. Sign up for our newsletter. Our newsletters are weekly."

    assert _boilerplate_hits(normalise(text), phrases) == 1


def test_checks_record_the_measured_values() -> None:
    text = "The first invented sentence. Accept cookies. Accept cookies. Subscribe now."
    phrases = ("accept cookies", "subscribe now", "unused phrase")

    checks = score_result(ENTRY, result(text=text, title="Invented Headlines"), phrases).checks

    assert checks is not None
    assert checks.title_ratio is not None and 0.9 < checks.title_ratio < 1.0
    assert checks.snippets_found == (1,)
    assert checks.snippets_missing == (2,)
    assert checks.boilerplate == (("accept cookies", 2), ("subscribe now", 1))
    assert checks.boilerplate_hits == 3


def test_failed_extraction_has_no_checks() -> None:
    score = score_result(ENTRY, ExtractionResult(status=ExtractionStatus.FAILED))

    assert score.checks is None


@pytest.mark.parametrize(
    "status", [ExtractionStatus.FAILED, ExtractionStatus.SKIPPED, ExtractionStatus.PAYWALLED]
)
def test_no_content_reports_only_extraction_failed(status: ExtractionStatus) -> None:
    score = score_result(ENTRY, ExtractionResult(status=status))

    assert score.failures == (Failure.EXTRACTION_FAILED,)


def test_partial_result_is_still_checked() -> None:
    assert score_result(ENTRY, result(status=ExtractionStatus.PARTIAL)).clean


def test_absent_expectations_skip_their_checks() -> None:
    bare = EvalEntry(id="bare", url="https://news.example.com/bare")

    assert score_result(bare, ExtractionResult(status=ExtractionStatus.OK)).clean


def test_load_boilerplate_skips_comments_and_blanks(tmp_path: Path) -> None:
    path = tmp_path / "boilerplate.txt"
    path.write_text("# comment\n\nAccept   Cookies\n  Subscribe Now \n", encoding="utf-8")

    assert load_boilerplate(path) == ("accept cookies", "subscribe now")


def test_load_boilerplate_missing_file_means_no_phrases(tmp_path: Path) -> None:
    assert load_boilerplate(tmp_path / "absent.txt") == ()


def test_repo_boilerplate_file_loads() -> None:
    path = Path(__file__).resolve().parents[3] / "eval" / "boilerplate.txt"

    assert "accept cookies" in load_boilerplate(path)


# Title check against the page's two titles (#62): a visible h1 and a metadata title.
TITLE_PAGE = """<html><head>
<title>Invented Headline | Example Daily</title>
<meta property="og:title" content="Invented Headline | Example Daily">
</head><body><nav><h1>Example Daily</h1></nav><article><h1>Invented Headline</h1></article>
</body></html>"""
H1_ONLY_PAGE = TITLE_PAGE.replace("<h1>Example Daily</h1>", "")


def test_title_matches_h1_when_extracted_title_is_the_metadata_title() -> None:
    extracted = result(title="Invented Headline | Example Daily")

    score = score_result(ENTRY, extracted, html=TITLE_PAGE)

    assert score.clean
    assert score.checks is not None
    assert score.checks.title_via == "h1"
    assert score.checks.title_ratio == 1.0


def test_title_matches_extracted_title_directly_without_h1() -> None:
    score = score_result(ENTRY, result(), html=TITLE_PAGE)

    assert score.clean
    assert score.checks is not None
    assert score.checks.title_via == "extracted"


def test_title_mismatch_without_html_still_fails() -> None:
    extracted = result(title="Invented Headline | Example Daily")

    assert score_result(ENTRY, extracted).failures == (Failure.TITLE_MISMATCH,)


def test_title_fails_when_neither_title_matches() -> None:
    other = EvalEntry(id="s", url=ENTRY.url, expected_title="Something Else Entirely")
    extracted = result(title="Invented Headline | Example Daily")

    score = score_result(other, extracted, html=H1_ONLY_PAGE)

    assert score.failures == (Failure.TITLE_MISMATCH,)


def test_h1_ignored_when_extracted_title_is_not_the_metadata_title() -> None:
    # A wrong extraction must not pass just because the page has a matching h1.
    extracted = result(title="A completely different title")

    score = score_result(ENTRY, extracted, html=TITLE_PAGE)

    assert score.failures == (Failure.TITLE_MISMATCH,)


def test_page_titles_reads_meta_and_headings_and_survives_bad_html() -> None:
    meta, headings = page_titles(TITLE_PAGE)

    assert "Invented Headline | Example Daily" in meta
    assert headings == ("Example Daily", "Invented Headline")
    assert page_titles("") == ((), ())


def test_page_titles_reads_xhtml_with_encoding_declaration() -> None:
    xhtml = '<?xml version="1.0" encoding="UTF-8"?>\n' + TITLE_PAGE

    assert page_titles(xhtml) == page_titles(TITLE_PAGE)
    assert page_titles(xhtml)[1] == ("Example Daily", "Invented Headline")
    assert page_titles('<?xml version="1.0" encoding="UTF-8"?>') == ((), ())
