"""Smoke tests for the ``newsfeed`` command-line entry point."""

from __future__ import annotations

from typer.testing import CliRunner

from newsfeed.cli import app

runner = CliRunner()


def test_run_exits_cleanly() -> None:
    result = runner.invoke(app, ["run"])

    assert result.exit_code == 0, result.output


def test_help_lists_run_command() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0, result.output
    assert "run" in result.output
