"""End-to-end tests for the harness with three synthetic entries and stub extractors."""

from __future__ import annotations

import json
from collections.abc import Callable
from itertools import count
from pathlib import Path

import pytest

from newsfeed.eval.report import (
    build_report,
    compare_reports,
    load_report,
    render_markdown,
    render_summary,
    write_report,
)
from newsfeed.eval.runner import Evaluation, filter_entries, run_evaluation
from newsfeed.eval.snapshot import take_snapshots
from newsfeed.eval.urls import EvalEntry
from newsfeed.models import ExtractionResult, ExtractionStatus
from tests.fakes import FakeExtractor, FakeFetcher

LQ, RQ = chr(0x201C), chr(0x201D)

A_URL = "https://news.example.com/a"
B_URL = "https://blog.example.org/b"
C_URL = "https://news.example.com/c"
MISSING_URL = "https://news.example.com/missing"

ENTRIES = [
    EvalEntry(
        id="a",
        url=A_URL,
        expected_title="Invented Headline One",
        must_contain=["first invented sentence"],
        word_range=(5, 50),
    ),
    EvalEntry(
        id="b",
        url=B_URL,
        expected_title="Second Story",
        must_contain=['"quoted words"'],
        word_range=(3, 20),
    ),
    EvalEntry(id="c", url=C_URL, expected_title="Locked Story", paywalled=True),
]


def ok(title: str, text: str, words: int) -> ExtractionResult:
    return ExtractionResult(status=ExtractionStatus.OK, title=title, text=text, word_count=words)


GOOD = FakeExtractor(
    "good",
    {
        A_URL: ok("Invented Headline One", "The first invented sentence is here.", 7),
        B_URL: ok("Second Story", f"Some {LQ}quoted words{RQ} appear.", 4),
        C_URL: ExtractionResult(status=ExtractionStatus.PAYWALLED),
    },
)
BAD = FakeExtractor(
    "bad",
    {
        A_URL: ok("Cookie banner", "Nothing useful.", 100),
        B_URL: ok("Second Story", f"Some {LQ}quoted words{RQ} appear.", 4),
    },
)


@pytest.fixture
def fixtures_dir(tmp_path: Path) -> Path:
    page = b"<html><body><p>Invented.</p></body></html>"
    fetcher = FakeFetcher({A_URL: page, B_URL: page, C_URL: page})
    target = tmp_path / "fixtures"
    take_snapshots([*ENTRIES, EvalEntry(id="missing", url=MISSING_URL)], fetcher, target)
    return target


def make_timer() -> Callable[[], float]:
    ticks = count()
    return lambda: next(ticks) * 0.25


def evaluate(fixtures_dir: Path, entries: list[EvalEntry] | None = None) -> Evaluation:
    return run_evaluation(
        entries if entries is not None else [*ENTRIES, EvalEntry(id="missing", url=MISSING_URL)],
        fixtures_dir,
        [GOOD, BAD],
        timer=make_timer(),
    )


def test_report_numbers(fixtures_dir: Path) -> None:
    report = build_report(evaluate(fixtures_dir), date="2026-10-13", commit="abc1234")

    assert report["entries"] == {
        "total": 4,
        "evaluated": 3,
        "not_snapshotted": ["missing"],
        "unreadable": [],
    }
    # Paywalled entry c is out of the headline and the tables.
    assert report["extractors"]["good"]["clean"] == 2
    assert report["extractors"]["good"]["total"] == 2
    assert report["extractors"]["good"]["percent"] == 100.0
    assert report["extractors"]["bad"]["clean"] == 1
    assert report["extractors"]["bad"]["percent"] == 50.0
    assert report["extractors"]["bad"]["failures"] == {
        "missing_snippet": 1,
        "title_mismatch": 1,
        "word_count_high": 1,
    }
    assert report["headline"]["extractor"] == "good"
    assert report["headline"]["percent"] == 100.0
    assert report["domains"]["news.example.com"]["bad"] == {"clean": 0, "total": 1, "percent": 0.0}
    assert report["domains"]["blog.example.org"]["good"]["clean"] == 1
    assert report["paywalled"] == {
        "bad": {"total": 1, "detected_paywall": 0, "clean": 0},
        "good": {"total": 1, "detected_paywall": 1, "clean": 0},
    }


def test_slowest_is_sorted_with_ties_broken_by_name(fixtures_dir: Path) -> None:
    report = build_report(evaluate(fixtures_dir), date="2026-10-13", commit="abc1234")

    # The fake timer makes every extraction take exactly 0.25 s.
    assert {s["seconds"] for s in report["slowest"]} == {0.25}
    keys = [(s["entry"], s["extractor"]) for s in report["slowest"]]
    assert keys == sorted(keys)
    assert len(keys) == 5


def test_report_is_deterministic(fixtures_dir: Path, tmp_path: Path) -> None:
    first = build_report(evaluate(fixtures_dir), date="2026-10-13", commit="abc1234")
    second = build_report(evaluate(fixtures_dir), date="2026-10-13", commit="abc1234")

    assert render_markdown(first) == render_markdown(second)
    one = write_report(first, tmp_path / "one")
    two = write_report(second, tmp_path / "two")
    assert one[0].name == "2026-10-13-abc1234.md"
    assert one[0].read_bytes() == two[0].read_bytes()
    assert one[1].read_bytes() == two[1].read_bytes()


def test_report_holds_no_extracted_text(fixtures_dir: Path, tmp_path: Path) -> None:
    report = build_report(evaluate(fixtures_dir), date="2026-10-13", commit="abc1234")
    md_path, json_path = write_report(report, tmp_path / "out")
    written = md_path.read_text(encoding="utf-8") + json_path.read_text(encoding="utf-8")

    for secret in ("Nothing useful", "Cookie banner", "quoted words appear", "first invented"):
        assert secret not in written


def test_markdown_has_the_sections(fixtures_dir: Path) -> None:
    report = build_report(evaluate(fixtures_dir), date="2026-10-13", commit="abc1234")
    text = render_markdown(report)

    for heading in (
        "## By extractor",
        "## By domain",
        "## Failure reasons",
        "## Paywalled entries",
        "## Slowest extractions",
        "## Not snapshotted",
        "## Failures",
    ):
        assert heading in text
    assert "**Headline clean:** 100.0% (2/2" in text
    assert "| news.example.com | 0/1 | 1/1 |" in text


def test_summary_lists_not_snapshotted(fixtures_dir: Path) -> None:
    report = build_report(evaluate(fixtures_dir), date="2026-10-13", commit="abc1234")

    summary = render_summary(report)

    assert "Evaluated 3 of 4 entries" in summary
    assert "Not snapshotted: missing" in summary


def test_extractor_that_raises_is_scored_as_failed(fixtures_dir: Path) -> None:
    class Boom:
        name = "boom"

        def extract(self, html: str, url: str) -> ExtractionResult:
            raise RuntimeError("secret detail")

    evaluation = run_evaluation(ENTRIES[:1], fixtures_dir, [Boom()], timer=make_timer())

    (outcome,) = evaluation.outcomes
    assert outcome.status == "failed"
    assert [str(f) for f in outcome.score.failures] == ["extraction_failed"]


def test_no_snapshots_means_nothing_evaluated(tmp_path: Path) -> None:
    evaluation = run_evaluation(ENTRIES, tmp_path / "empty", [GOOD])
    report = build_report(evaluation, date="2026-10-13", commit="abc1234")

    assert evaluation.evaluated == 0
    assert report["headline"] is None
    assert report["entries"]["not_snapshotted"] == ["a", "b", "c"]
    assert "no entries were evaluated" in render_markdown(report)


def test_damaged_snapshot_is_unreadable_not_not_snapshotted(fixtures_dir: Path) -> None:
    (fixtures_dir / "news.example.com" / "a.meta.json").write_text("{not json", encoding="utf-8")

    evaluation = evaluate(fixtures_dir, ENTRIES)

    assert evaluation.unreadable == ["a"]
    assert evaluation.not_snapshotted == []
    assert evaluation.evaluated == 2


def test_filter_entries_matches_domain_and_subdomains() -> None:
    assert [e.id for e in filter_entries(ENTRIES, "news.example.com")] == ["a", "c"]
    assert [e.id for e in filter_entries(ENTRIES, "example.org")] == ["b"]
    assert [e.id for e in filter_entries(ENTRIES, None)] == ["a", "b", "c"]
    assert filter_entries(ENTRIES, "ample.org") == []


def test_compare_reports_lists_changes(fixtures_dir: Path, tmp_path: Path) -> None:
    old = build_report(evaluate(fixtures_dir), date="2026-10-01", commit="old1111")
    improved_bad = FakeExtractor(
        "bad",
        {
            A_URL: ok("Invented Headline One", "The first invented sentence is here.", 7),
            B_URL: ok("Wrong", "Nothing.", 4),
        },
    )
    new_eval = run_evaluation(ENTRIES, fixtures_dir, [GOOD, improved_bad], timer=make_timer())
    new = build_report(new_eval, date="2026-10-13", commit="new2222")

    lines = compare_reports(old, new)

    text = "\n".join(lines)
    assert "Compared with 2026-10-01 (old1111)" in text
    assert "`bad`: 50.0% -> 50.0% (+0.0)" in text
    assert "Newly clean (1): a (`bad`)" in text
    assert "Regressed (1): b (`bad`)" in text


def test_load_report_round_trips_and_rejects_junk(fixtures_dir: Path, tmp_path: Path) -> None:
    report = build_report(evaluate(fixtures_dir), date="2026-10-13", commit="abc1234")
    _, json_path = write_report(report, tmp_path / "out")

    assert load_report(json_path) == json.loads(json_path.read_text(encoding="utf-8"))
    junk = tmp_path / "junk.json"
    junk.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="not a schema"):
        load_report(junk)
    with pytest.raises(ValueError, match="cannot read"):
        load_report(tmp_path / "absent.json")


def test_extractors_get_the_snapshots_final_url(tmp_path: Path) -> None:
    entry = EvalEntry(id="moved", url="https://old.example.com/m")
    final = "https://new.example.com/m"
    fetcher = FakeFetcher()
    fetcher.add(entry.url, b"<html><body><p>x</p></body></html>", final_url=final)
    target = tmp_path / "fixtures"
    take_snapshots([entry], fetcher, target)
    extractor = FakeExtractor("spy", default=ok("t", "text", 1))

    run_evaluation([entry], target, [extractor])

    assert [url for url, _ in extractor.calls] == [final]
