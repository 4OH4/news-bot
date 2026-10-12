"""Tests for ``newsfeed.eval.scoring``: one check at a time."""

from __future__ import annotations

from pathlib import Path

import pytest

from newsfeed.eval.scoring import (
    BOILERPLATE_MAX_HITS,
    Failure,
    load_boilerplate,
    normalise,
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
