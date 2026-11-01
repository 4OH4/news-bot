"""Tests for scripts/context.py: reading the session's context size, offline."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_script_context", SCRIPTS / "context.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


context = load()


def turn(tokens: int, **extra: Any) -> str:
    usage = {"input_tokens": 1, "cache_read_input_tokens": tokens, "cache_creation_input_tokens": 9}
    return json.dumps({"type": "assistant", "message": {"usage": usage}, **extra})


def transcript(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_the_last_main_session_turn_gives_the_size(tmp_path: Path) -> None:
    path = transcript(tmp_path, turn(100), turn(200), "not json", '{"type": "user"}')
    assert context.context_tokens(path) == 210


def test_a_subagent_turn_does_not_count(tmp_path: Path) -> None:
    path = transcript(tmp_path, turn(100), turn(900, isSidechain=True))
    assert context.context_tokens(path) == 110


def test_a_transcript_with_no_turns_gives_no_size(tmp_path: Path) -> None:
    assert context.context_tokens(transcript(tmp_path, "[1]", '{"message": "x"}')) is None


def test_the_folder_name_replaces_every_non_alphanumeric(tmp_path: Path) -> None:
    found = context.transcript_dir(Path("/home/a/news-bot.x"), tmp_path)
    assert found == tmp_path / "-home-a-news-bot-x"


def test_the_newest_transcript_is_the_one_read(tmp_path: Path) -> None:
    old = tmp_path / "old.jsonl"
    new = tmp_path / "new.jsonl"
    old.write_text("", encoding="utf-8")
    new.write_text("", encoding="utf-8")
    os.utime(old, (1, 1))
    assert context.latest_transcript(tmp_path) == new
    assert context.latest_transcript(tmp_path / "missing") is None


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        (149_999, "ok: below 150k"),
        (150_000, "restart: finish at this batch close"),
        (None, "context unavailable"),
    ],
)
def test_the_report_says_what_to_do(tokens: int | None, expected: str) -> None:
    assert expected in context.report(tokens)
