"""Tests for the detailed per-run evaluation log (``runlog.py``), with no network."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from typing import Any

import pytest

from newsfeed.eval.runlog import (
    ERROR_MAX_CHARS,
    LOG_SCHEMA_VERSION,
    build_log_records,
    log_path,
    write_log,
)
from newsfeed.eval.runner import run_evaluation
from newsfeed.eval.scoring import normalise
from newsfeed.eval.snapshot import snapshot_paths, take_snapshots
from newsfeed.eval.urls import EvalEntry
from newsfeed.extract.chain import ExtractorChain
from newsfeed.models import ExtractionResult, ExtractionStatus
from tests.fakes import FakeExtractor, FakeFetcher

A_URL = "https://news.example.com/a"
B_URL = "https://blog.example.org/b"
FAILED_URL = "https://news.example.com/gone"
NEVER_URL = "https://news.example.com/never"
BROKEN_URL = "https://news.example.com/broken"

# Invented text that only the extractors return: it must never reach the log.
A_TEXT = "The first invented sentence sits beside a sign up for our newsletter box."
A_TITLE = "Invented Headline One"
BAD_TITLE = "Totally Different Heading"

ENTRIES = [
    EvalEntry(
        id="a",
        url=A_URL,
        expected_title=A_TITLE,
        must_contain=["first invented sentence", "a sentence that is absent"],
        word_range=(5, 50),
    ),
    EvalEntry(id="b", url=B_URL, paywalled=True),
    EvalEntry(id="failed", url=FAILED_URL),
    EvalEntry(id="never", url=NEVER_URL),
    EvalEntry(id="broken", url=BROKEN_URL),
]

GOOD = FakeExtractor(
    "good",
    {
        A_URL: ExtractionResult(
            status=ExtractionStatus.OK, title=A_TITLE, text=A_TEXT, word_count=13
        ),
        B_URL: ExtractionResult(status=ExtractionStatus.PAYWALLED, error="paywall marker"),
    },
)
BAD = FakeExtractor(
    "bad",
    {
        A_URL: ExtractionResult(
            status=ExtractionStatus.OK, title=BAD_TITLE, text=A_TEXT, word_count=400
        ),
        B_URL: ExtractionResult(status=ExtractionStatus.FAILED, error="x" * 1000),
    },
)
STARTED = datetime(2026, 10, 13, 7, 20, 5, tzinfo=UTC)


def make_timer() -> Callable[[], float]:
    ticks = count()
    return lambda: next(ticks) * 0.25


@pytest.fixture
def fixtures_dir(tmp_path: Path) -> Path:
    page = b"<html><body><p>Invented.</p></body></html>"
    target = tmp_path / "fixtures"
    # FAILED_URL isn't registered, so the fake fetcher answers 404 and only a
    # meta file is saved; NEVER_URL is never snapshotted at all.
    fetcher = FakeFetcher({A_URL: page, B_URL: page, BROKEN_URL: page})
    take_snapshots([e for e in ENTRIES if e.id != "never"], fetcher, target)
    snapshot_paths(target, ENTRIES[4]).html.write_bytes(b"not gzip")
    return target


def records_for(fixtures_dir: Path, extractors: list[Any] | None = None) -> list[dict[str, Any]]:
    evaluation = run_evaluation(
        ENTRIES,
        fixtures_dir,
        extractors if extractors is not None else [GOOD, BAD],
        [normalise("sign up for our newsletter"), normalise("cookies")],
        timer=make_timer(),
    )
    return build_log_records(
        ENTRIES,
        evaluation,
        started=STARTED,
        commit="abc1234",
        fixtures_dir=fixtures_dir,
        urls_path=Path("eval/urls.yaml"),
    )


def by_id(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {r["entry"]: r for r in records if r["record"] == "entry"}


def test_header_then_one_record_per_entry_in_order(fixtures_dir: Path) -> None:
    records = records_for(fixtures_dir)

    header = records[0]
    assert header == {
        "record": "run",
        "schema": LOG_SCHEMA_VERSION,
        "started": "2026-10-13T07:20:05+00:00",
        "commit": "abc1234",
        "urls_file": "eval/urls.yaml",
        "fixtures_dir": str(fixtures_dir),
        "extractors": ["good", "bad"],
        "entries": 5,
        "evaluated": 2,
        "not_snapshotted": 2,
        "unreadable": 1,
    }
    assert [r["entry"] for r in records[1:]] == ["a", "b", "failed", "never", "broken"]


def test_entry_record_holds_metrics_and_check_outcomes(fixtures_dir: Path) -> None:
    a = by_id(records_for(fixtures_dir))["a"]

    assert a["url"] == A_URL
    assert a["final_url"] == A_URL
    assert a["domain"] == "news.example.com"
    assert a["snapshot"] == "ok"
    good = a["results"]["good"]
    assert good["status"] == "ok"
    assert good["method"] == "good"
    assert good["words"] == 13
    assert good["seconds"] == 0.25
    assert good["clean"] is False
    assert good["failures"] == ["missing_snippet"]
    assert good["checks"]["title"] == {
        "expected": True,
        "ratio": 1.0,
        "via": "extracted",
        "min_ratio": 0.9,
    }
    assert good["checks"]["must_contain"] == [
        {"n": 1, "snippet": "first invented sentence", "found": True},
        {"n": 2, "snippet": "a sentence that is absent", "found": False},
    ]
    assert good["checks"]["word_range"] == {"min": 5, "max": 50, "words": 13, "in_range": True}
    assert good["checks"]["boilerplate"] == {
        "hits": 1,
        "max_hits": 2,
        "phrases": {"sign up for our newsletter": 1},
    }
    bad = a["results"]["bad"]
    assert bad["failures"] == ["title_mismatch", "missing_snippet", "word_count_high"]
    assert bad["checks"]["title"]["ratio"] < 0.9
    assert bad["checks"]["word_range"]["in_range"] is False


def test_failed_extraction_skips_checks_and_truncates_error(fixtures_dir: Path) -> None:
    b = by_id(records_for(fixtures_dir))["b"]

    assert b["paywalled"] is True
    assert b["results"]["good"]["status"] == "paywalled"
    assert b["results"]["good"]["error"] == "paywall marker"
    assert b["results"]["good"]["checks"] is None
    error = b["results"]["bad"]["error"]
    assert len(error) == ERROR_MAX_CHARS
    assert error.endswith("…")


def test_skipped_entries_say_why(fixtures_dir: Path) -> None:
    records = by_id(records_for(fixtures_dir))

    assert records["failed"]["snapshot"] == "fetch_failed"
    assert "404" in records["failed"]["snapshot_detail"]
    assert records["never"]["snapshot"] == "missing"
    assert records["never"]["snapshot_detail"] is None
    assert records["broken"]["snapshot"] == "unreadable"
    assert "cannot read snapshot HTML" in records["broken"]["snapshot_detail"]
    assert all(records[k]["results"] == {} for k in ("failed", "never", "broken"))


def test_chain_steps_are_logged(fixtures_dir: Path) -> None:
    first = FakeExtractor(
        "first", default=ExtractionResult(status=ExtractionStatus.FAILED, error="empty body")
    )
    second = FakeExtractor(
        "second",
        default=ExtractionResult(
            status=ExtractionStatus.OK, title=A_TITLE, text=A_TEXT, word_count=300
        ),
    )

    a = by_id(records_for(fixtures_dir, [ExtractorChain([first, second])]))["a"]

    chain = a["results"]["chain"]
    assert chain["method"] == "second"
    assert [(s["method"], s["status"], s["error"]) for s in chain["attempts"]] == [
        ("first", "failed", "empty body"),
        ("second", "ok", None),
    ]
    assert chain["attempts"][1]["words"] == 300


def test_written_log_is_jsonl_without_extracted_text(fixtures_dir: Path, tmp_path: Path) -> None:
    records = records_for(fixtures_dir)
    path = log_path(tmp_path / "logs", STARTED, "abc1234")

    written = write_log(records, path)

    assert written == tmp_path / "logs" / "20261013T072005Z-abc1234.jsonl"
    lines = written.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == records
    content = written.read_text(encoding="utf-8")
    for extracted in (A_TEXT, A_TITLE, BAD_TITLE, "Invented."):
        assert extracted not in content
