"""Tests for ``newsfeed eval snapshot``, with ``FakeFetcher`` in place of HTTP."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner

from newsfeed import cli
from newsfeed.eval.snapshot import read_snapshot, snapshot_paths
from newsfeed.eval.urls import EvalEntry
from tests.fakes import FakeFetcher

runner = CliRunner()

PAGE = b"<html><head><title>Invented</title></head><body><p>Invented text.</p></body></html>"
URLS_YAML = """
urls:
  - id: first
    url: https://news.example.com/first
  - id: second
    url: https://blog.example.org/second
  - id: missing
    url: https://news.example.com/missing
"""


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeFetcher:
    fetcher = FakeFetcher(
        {
            "https://news.example.com/first": PAGE,
            "https://blog.example.org/second": PAGE,
        }
    )

    @contextmanager
    def open_fake() -> Iterator[FakeFetcher]:
        yield fetcher

    monkeypatch.setattr(cli, "_open_fetcher", open_fake)
    return fetcher


@pytest.fixture
def urls_file(tmp_path: Path) -> Path:
    path = tmp_path / "urls.yaml"
    path.write_text(URLS_YAML, encoding="utf-8")
    return path


def test_eval_help_lists_snapshot() -> None:
    result = runner.invoke(cli.app, ["eval", "--help"])

    assert result.exit_code == 0, result.output
    assert "snapshot" in result.output


def test_snapshot_fetches_then_skips(fake: FakeFetcher, urls_file: Path, tmp_path: Path) -> None:
    target = tmp_path / "snaps"
    args = ["eval", "snapshot", "--urls", str(urls_file), "--fixtures-dir", str(target)]

    first = runner.invoke(cli.app, args)
    second = runner.invoke(cli.app, args)

    assert first.exit_code == 0, first.output
    assert f"Snapshots in {target}: 2 fetched, 0 skipped, 1 failed" in first.output
    assert "missing (https://news.example.com/missing): HTTP 404" in first.output
    assert second.exit_code == 0, second.output
    assert "0 fetched, 2 skipped, 1 failed" in second.output
    entry = EvalEntry(id="first", url="https://news.example.com/first")
    assert read_snapshot(snapshot_paths(target, entry))[1] == PAGE


def test_refresh_and_only(fake: FakeFetcher, urls_file: Path, tmp_path: Path) -> None:
    target = tmp_path / "snaps"
    base = ["eval", "snapshot", "--urls", str(urls_file), "--fixtures-dir", str(target)]
    runner.invoke(cli.app, base)
    fake.calls.clear()

    result = runner.invoke(cli.app, [*base, "--refresh", "--only", "second"])

    assert result.exit_code == 0, result.output
    assert "1 fetched, 0 skipped, 0 failed" in result.output
    assert fake.fetched_urls() == ["https://blog.example.org/second"]


def test_unknown_only_id_is_a_usage_error(
    fake: FakeFetcher, urls_file: Path, tmp_path: Path
) -> None:
    target = str(tmp_path / "s")
    result = runner.invoke(
        cli.app,
        ["eval", "snapshot", "--urls", str(urls_file), "--fixtures-dir", target, "--only", "nope"],
    )

    assert result.exit_code == 2
    assert "nope" in result.output
    assert fake.calls == []


def test_uses_env_var_when_no_flag(
    monkeypatch: pytest.MonkeyPatch, fake: FakeFetcher, urls_file: Path, tmp_path: Path
) -> None:
    monkeypatch.setenv("NEWSFEED_FIXTURES_DIR", str(tmp_path / "from-env"))

    result = runner.invoke(cli.app, ["eval", "snapshot", "--urls", str(urls_file)])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "from-env" / "news.example.com" / "first.html.gz").is_file()


def test_uses_home_default_when_no_flag_or_env(
    monkeypatch: pytest.MonkeyPatch, fake: FakeFetcher, urls_file: Path, tmp_path: Path
) -> None:
    monkeypatch.delenv("NEWSFEED_FIXTURES_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    result = runner.invoke(cli.app, ["eval", "snapshot", "--urls", str(urls_file)])

    assert result.exit_code == 0, result.output
    default = tmp_path / "home" / ".newsfeed" / "eval-fixtures"
    assert (default / "blog.example.org" / "second.html.gz").is_file()


def test_refuses_fixtures_dir_inside_a_repo(
    fake: FakeFetcher, urls_file: Path, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)

    result = runner.invoke(
        cli.app,
        ["eval", "snapshot", "--urls", str(urls_file), "--fixtures-dir", str(repo / "snaps")],
    )

    assert result.exit_code == 1
    assert "refusing to write snapshots" in result.output
    assert fake.calls == []
    assert not (repo / "snaps").exists()


def test_finds_urls_file_from_working_directory(
    monkeypatch: pytest.MonkeyPatch, fake: FakeFetcher, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    (project / "eval").mkdir(parents=True)
    (project / "eval" / "urls.yaml").write_text(URLS_YAML, encoding="utf-8")
    (project / "backend").mkdir()
    monkeypatch.chdir(project / "backend")

    result = runner.invoke(cli.app, ["eval", "snapshot", "--fixtures-dir", str(tmp_path / "snaps")])

    assert result.exit_code == 0, result.output
    assert "2 fetched" in result.output


def test_missing_urls_file_is_an_error(
    monkeypatch: pytest.MonkeyPatch, fake: FakeFetcher, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli.app, ["eval", "snapshot", "--fixtures-dir", str(tmp_path / "snaps")])

    assert result.exit_code == 1
    assert "eval/urls.yaml not found" in result.output


def test_invalid_urls_file_is_an_error(fake: FakeFetcher, tmp_path: Path) -> None:
    bad = tmp_path / "urls.yaml"
    bad.write_text("urls:\n  - id: x\n", encoding="utf-8")

    result = runner.invoke(
        cli.app,
        ["eval", "snapshot", "--urls", str(bad), "--fixtures-dir", str(tmp_path / "snaps")],
    )

    assert result.exit_code == 1
    assert "validation error" in result.output
    assert fake.calls == []


def test_real_fetcher_is_opened_and_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[bool] = []
    monkeypatch.setattr(cli.HttpFetcher, "close", lambda self: closed.append(True))

    with cli._open_fetcher() as fetcher:
        assert isinstance(fetcher, cli.HttpFetcher)
        assert fetcher.user_agent == cli.DEFAULT_USER_AGENT

    assert closed == [True]
