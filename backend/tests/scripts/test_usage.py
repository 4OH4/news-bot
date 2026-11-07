"""Tests for scripts/usage.py: reading both usage limits, offline."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import time
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
TOKEN = "sk-test-secret-token"  # invented
REPLY = {
    "five_hour": {"utilization": 42.4, "resets_at": "2026-10-31T21:00:00+00:00"},
    "seven_day": {"utilization": 12.0, "resets_at": "2026-11-03T09:30:00Z"},
}


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"_script_{name}", SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


usage = load("usage")


def all_lines(payload: Any) -> list[str]:
    result: list[str] = usage.read_limits(lambda: TOKEN, lambda token: payload)
    return result


def lines(payload: Any) -> list[str]:
    """The report without the `parallel:` line, which has its own tests below."""
    return [line for line in all_lines(payload) if not line.startswith("parallel:")]


def parallel(five: float, week: float = 10.0) -> str:
    """The `parallel:` line for the given usage percentages."""
    payload = {
        "five_hour": {**REPLY["five_hour"], "utilization": five},
        "seven_day": {**REPLY["seven_day"], "utilization": week},
    }
    found = [line for line in all_lines(payload) if line.startswith("parallel:")]
    assert len(found) == 1, found
    return found[0]


@pytest.fixture(autouse=True)
def utc(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_reads_both_limits_and_their_reset_times() -> None:
    assert lines(REPLY) == [
        "5-hour: 42% used, resets 2026-10-31 21:00 +0000",
        "weekly: 12% used, resets 2026-11-03 09:30 +0000",
        "ok: both limits below 90%",
    ]


@pytest.mark.parametrize(
    ("five", "week", "expected"),
    [
        (90.0, 10.0, "pause: 5-hour at or above 90%"),
        (10.0, 95.0, "pause: weekly at or above 90%"),
        (90.0, 90.0, "pause: 5-hour and weekly at or above 90%"),
    ],
)
def test_says_which_limit_calls_for_a_pause(five: float, week: float, expected: str) -> None:
    payload = {
        "five_hour": {**REPLY["five_hour"], "utilization": five},
        "seven_day": {**REPLY["seven_day"], "utilization": week},
    }
    assert lines(payload)[-1] == expected


def test_just_below_the_threshold_is_ok() -> None:
    payload = {**REPLY, "seven_day": {**REPLY["seven_day"], "utilization": 89.9}}
    assert lines(payload)[-1] == "ok: both limits below 90%"


def test_a_missing_reset_time_still_shows_the_percentage() -> None:
    payload = {**REPLY, "seven_day": {"utilization": 12.0, "resets_at": None}}
    assert lines(payload)[1] == "weekly: 12% used, resets unknown"


def test_an_unreadable_reset_time_is_shown_as_it_came() -> None:
    payload = {**REPLY, "seven_day": {"utilization": 12.0, "resets_at": "next week"}}
    assert lines(payload)[1] == "weekly: 12% used, resets next week"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"five_hour": REPLY["five_hour"]},  # no weekly figure: not a reading of "fine"
        {**REPLY, "seven_day": None},
        {**REPLY, "five_hour": {"utilization": "lots"}},
        ["unexpected"],
    ],
)
def test_a_malformed_reply_means_unavailable_not_low_usage(payload: Any) -> None:
    result = lines(payload)
    assert len(result) == 1
    assert result[0].startswith("usage unavailable (")


def test_a_failed_request_never_shows_the_token_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(token: str) -> Any:
        raise OSError(f"could not send Authorization: Bearer {token}")

    monkeypatch.setattr(usage, "read_limits", partial(usage.read_limits, lambda: TOKEN, fail))
    with pytest.raises(SystemExit) as exit_info:
        usage.main()
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert TOKEN not in out
    assert out == "usage unavailable (OSError)\nparallel: 3\n"


def test_missing_credentials_mean_unavailable(tmp_path: Path) -> None:
    def fetcher(token: str) -> Any:
        raise AssertionError("must not make a request with no token")

    result = usage.read_limits(lambda: usage.read_token(tmp_path / "none.json"), fetcher)
    assert result == ["usage unavailable (FileNotFoundError)", "parallel: 3"]


def test_reads_the_token_from_the_credentials_file(tmp_path: Path) -> None:
    file = tmp_path / "credentials.json"
    file.write_text(json.dumps({"claudeAiOauth": {"accessToken": TOKEN}}), encoding="utf-8")
    assert usage.read_token(file) == TOKEN


def test_request_sends_the_token_only_in_the_header(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class Reply(io.BytesIO):
        def __enter__(self) -> Reply:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def urlopen(request: Any, timeout: float) -> Reply:
        seen.update(
            url=request.full_url,
            auth=request.get_header("Authorization"),
            beta=request.get_header("Anthropic-beta"),
            t=timeout,
        )
        return Reply(json.dumps(REPLY).encode())

    monkeypatch.setattr(usage.urllib.request, "urlopen", urlopen)
    assert usage.request_usage(TOKEN) == REPLY
    assert seen == {
        "url": usage.USAGE_URL,
        "auth": f"Bearer {TOKEN}",
        "beta": "oauth-2025-04-20",
        "t": usage.TIMEOUT,
    }
    assert TOKEN not in seen["url"]


def test_nothing_is_written_to_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    lines(REPLY)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("five", "week", "expected"),
    [
        (0.0, 0.0, "parallel: 10"),
        (40.0, 10.0, "parallel: 10"),
        (50.0, 10.0, "parallel: 10"),
        (50.1, 10.0, "parallel: 5"),
        (60.0, 10.0, "parallel: 5"),
        (89.9, 10.0, "parallel: 5"),
        (90.0, 10.0, "parallel: 0"),
        (92.0, 10.0, "parallel: 0"),
        (100.0, 10.0, "parallel: 0"),
        (10.0, 90.0, "parallel: 0"),
        (10.0, 91.0, "parallel: 0"),
    ],
)
def test_parallel_limit_follows_five_hour_usage(five: float, week: float, expected: str) -> None:
    assert parallel(five, week) == expected


def test_low_usage_prints_parallel_10_and_ok() -> None:
    payload = {
        "five_hour": {**REPLY["five_hour"], "utilization": 40.0},
        "seven_day": {**REPLY["seven_day"], "utilization": 10.0},
    }
    result = all_lines(payload)
    assert "parallel: 10" in result
    assert any(line.startswith("ok:") for line in result)
    assert not any(line.startswith("pause:") for line in result)


def test_high_usage_prints_parallel_0_and_pause() -> None:
    result = all_lines(
        {
            "five_hour": {**REPLY["five_hour"], "utilization": 92.0},
            "seven_day": {**REPLY["seven_day"], "utilization": 10.0},
        }
    )
    assert "parallel: 0" in result
    assert any(line.startswith("pause:") for line in result)


def test_a_weekly_limit_of_91_pauses_even_with_low_five_hour_use() -> None:
    result = all_lines(
        {
            "five_hour": {**REPLY["five_hour"], "utilization": 5.0},
            "seven_day": {**REPLY["seven_day"], "utilization": 91.0},
        }
    )
    assert "parallel: 0" in result
    assert any(line.startswith("pause:") for line in result)


def test_unreadable_usage_keeps_the_old_limit_of_3() -> None:
    result = all_lines({})
    assert result[0].startswith("usage unavailable (")
    assert "parallel: 3" in result


def test_unreadable_usage_prints_parallel_3_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(token: str) -> Any:
        raise OSError("down")

    monkeypatch.setattr(usage, "read_limits", partial(usage.read_limits, lambda: TOKEN, fail))
    with pytest.raises(SystemExit) as exit_info:
        usage.main()
    assert exit_info.value.code == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "usage unavailable (OSError)"
    assert "parallel: 3" in out
