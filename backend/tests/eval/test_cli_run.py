"""Tests for ``newsfeed eval run``, using real extractors on synthetic snapshots."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from newsfeed import cli
from newsfeed.eval.snapshot import take_snapshots
from newsfeed.eval.urls import load_eval_urls
from tests.fakes import FakeFetcher

runner = CliRunner()

BODY = " ".join(f"Invented sentence number {i} about a made-up harbour town." for i in range(40))
PAGE = (
    f"<html><head><title>Harbour Town News</title></head><body><article><h1>Harbour Town News"
    f"</h1><p>{BODY}</p></article></body></html>"
).encode()
URLS_YAML = """
urls:
  - id: harbour
    url: https://news.example.com/harbour
    expected_title: Harbour Town News
    must_contain: ["invented sentence number 3"]
    word_range: [100, 1000]
  - id: unsnapshotted
    url: https://blog.example.org/other
"""


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A repo-like tree with eval/urls.yaml, plus a fixtures dir holding one snapshot."""
    root = tmp_path / "repo"
    (root / "eval" / "results").mkdir(parents=True)
    urls = root / "eval" / "urls.yaml"
    urls.write_text(URLS_YAML, encoding="utf-8")
    fixtures = tmp_path / "fixtures"
    entries = load_eval_urls(urls)
    take_snapshots(entries[:1], FakeFetcher({"https://news.example.com/harbour": PAGE}), fixtures)
    monkeypatch.setenv("NEWSFEED_CONFIG_DIR", str(root / "config"))
    monkeypatch.chdir(root)
    return root, fixtures


def test_run_writes_report_and_lists_missing(layout: tuple[Path, Path]) -> None:
    root, fixtures = layout

    result = runner.invoke(cli.app, ["eval", "run", "--fixtures-dir", str(fixtures)])

    assert result.exit_code == 0, result.output
    assert "Evaluated 1 of 2 entries" in result.output
    assert "Not snapshotted: unsnapshotted" in result.output
    reports = sorted((root / "eval" / "results").glob("*.json"))
    assert len(reports) == 1
    assert reports[0].with_suffix(".md").exists()
    data = json.loads(reports[0].read_text(encoding="utf-8"))
    assert set(data["extractors"]) == {"trafilatura", "readability", "newspaper4k", "chain"}
    assert data["headline"]["extractor"] == "chain"


def test_run_filters_by_extractor_and_compares(
    layout: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, fixtures = layout
    base = ["eval", "run", "--fixtures-dir", str(fixtures), "--extractor", "trafilatura"]
    # Distinct commits, so the second report doesn't overwrite the first.
    shas = iter(["aaaaaaa", "bbbbbbb"])
    monkeypatch.setattr(cli, "git_short_sha", lambda _cwd: next(shas))
    assert runner.invoke(cli.app, base).exit_code == 0
    (first,) = (root / "eval" / "results").glob("*.json")

    result = runner.invoke(cli.app, [*base, "--compare", str(first)])

    assert result.exit_code == 0, result.output
    assert "Compared with" in result.output
    assert "`trafilatura`" in result.output
    assert "readability" not in json.loads(first.read_text(encoding="utf-8"))["extractors"]
    assert len(list((root / "eval" / "results").glob("*.json"))) == 2


def test_run_rejects_unknown_extractor(layout: tuple[Path, Path]) -> None:
    _, fixtures = layout

    result = runner.invoke(
        cli.app, ["eval", "run", "--fixtures-dir", str(fixtures), "--extractor", "nope"]
    )

    assert result.exit_code != 0
    assert "unknown extractor" in result.output


def test_run_with_no_snapshots_exits_zero_and_writes_nothing(
    layout: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _ = layout

    result = runner.invoke(cli.app, ["eval", "run", "--fixtures-dir", str(tmp_path / "none")])

    assert result.exit_code == 0, result.output
    assert "no report was written" in result.output
    assert list((root / "eval" / "results").glob("*.json")) == []


def test_run_domain_filter_with_no_match(layout: tuple[Path, Path]) -> None:
    _, fixtures = layout

    result = runner.invoke(
        cli.app, ["eval", "run", "--fixtures-dir", str(fixtures), "--domain", "nowhere.test"]
    )

    assert result.exit_code == 0, result.output
    assert "Evaluated 0 of 0 entries" in result.output


def test_run_without_urls_file_is_a_harness_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)

    result = runner.invoke(cli.app, ["eval", "run"])

    assert result.exit_code == 1
    assert "urls.yaml not found" in result.output


def test_run_with_bad_compare_file_is_a_harness_error(layout: tuple[Path, Path]) -> None:
    _, fixtures = layout

    result = runner.invoke(
        cli.app,
        ["eval", "run", "--fixtures-dir", str(fixtures), "--compare", "/nonexistent/x.json"],
    )

    assert result.exit_code == 1
    assert "cannot read report" in result.output


def test_run_writes_detailed_log_and_prints_its_path(layout: tuple[Path, Path]) -> None:
    root, fixtures = layout

    result = runner.invoke(cli.app, ["eval", "run", "--fixtures-dir", str(fixtures)])

    assert result.exit_code == 0, result.output
    (log,) = (root / "eval" / "logs").glob("*.jsonl")
    assert f"Detailed log: {log}" in result.output
    content = log.read_text(encoding="utf-8")
    records = [json.loads(line) for line in content.splitlines()]
    assert records[0]["record"] == "run"
    entries = {r["entry"]: r for r in records[1:]}
    assert entries["unsnapshotted"]["snapshot"] == "missing"
    harbour = entries["harbour"]
    assert set(harbour["results"]) == {"trafilatura", "readability", "newspaper4k", "chain"}
    assert harbour["results"]["chain"]["attempts"]
    # Metrics only: the page body never reaches the log.
    assert "made-up harbour town" not in content


def test_run_with_no_snapshots_still_writes_the_log(
    layout: tuple[Path, Path], tmp_path: Path
) -> None:
    root, _ = layout

    result = runner.invoke(cli.app, ["eval", "run", "--fixtures-dir", str(tmp_path / "none")])

    assert result.exit_code == 0, result.output
    (log,) = (root / "eval" / "logs").glob("*.jsonl")
    assert f"Detailed log: {log}" in result.output
    lines = log.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line).get("snapshot") for line in lines] == [None, "missing", "missing"]
