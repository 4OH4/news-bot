#!/usr/bin/env python3
"""Print how much of the 5-hour and weekly usage limits is used, and when each resets,
then `parallel: <n>`, the number of issues allowed in progress at once.

  python3 scripts/usage.py

The delivery manager runs this before starting an issue (AUTOMATION.md §11). It reads
the endpoint behind Claude Code's own /usage view, using the local login token. The
token only ever goes in the request header: it is never printed or saved, and no
response is cached.

The endpoint is undocumented, so a failed lookup prints "usage unavailable" and exits 0:
if usage can't be read, work continues as normal.

Requirements: Python 3.10+ (standard library only).
"""

from __future__ import annotations

import json
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CREDENTIALS = Path.home() / ".claude" / ".credentials.json"
TIMEOUT = 10
PAUSE_AT = 90.0
# Issues allowed in progress at once: many while the 5-hour window has room, fewer as it
# fills, none near the limit. UNREADABLE_PARALLEL is the old fixed limit, used when usage
# can't be read.
LOW_USAGE_AT = 50.0
PARALLEL_LOW = 10
PARALLEL_HIGH = 5
UNREADABLE_PARALLEL = 3
LIMITS = (("5-hour", "five_hour"), ("weekly", "seven_day"))


@dataclass
class Limit:
    name: str
    percent: float
    resets_at: str


def read_token(credentials: Path = CREDENTIALS) -> str:
    token: str = json.loads(credentials.read_text(encoding="utf-8"))["claudeAiOauth"]["accessToken"]
    return token


def request_usage(token: str) -> Any:
    request = urllib.request.Request(
        USAGE_URL,
        headers={"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.load(response)


def format_reset(value: Any) -> str:
    """A reset time in local time with its offset, as cron schedules need local time."""
    if not value:
        return "unknown"
    try:
        # Python before 3.11 can't read a trailing Z.
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    return moment.astimezone().strftime("%Y-%m-%d %H:%M %z")


def parse_usage(payload: dict[str, Any]) -> list[Limit]:
    """Both limits from the endpoint's reply. Raises KeyError, TypeError or ValueError if
    a limit is missing or malformed, so a partial reading can't look like low usage."""
    return [
        Limit(
            name,
            float(payload[key]["utilization"]),
            format_reset(payload[key].get("resets_at")),
        )
        for name, key in LIMITS
    ]


def parallel_limit(limits: list[Limit]) -> int:
    """How many issues may be in progress at once, from the 5-hour use (weekly only pauses)."""
    if any(lim.percent >= PAUSE_AT for lim in limits):
        return 0
    five_hour = next(lim.percent for lim in limits if lim.name == "5-hour")
    return PARALLEL_LOW if five_hour <= LOW_USAGE_AT else PARALLEL_HIGH


def report(limits: list[Limit]) -> list[str]:
    lines = [f"{lim.name}: {lim.percent:.0f}% used, resets {lim.resets_at}" for lim in limits]
    reached = [lim.name for lim in limits if lim.percent >= PAUSE_AT]
    if reached:
        lines.append(f"pause: {' and '.join(reached)} at or above {PAUSE_AT:.0f}%")
    else:
        lines.append(f"ok: both limits below {PAUSE_AT:.0f}%")
    lines.append(f"parallel: {parallel_limit(limits)}")
    return lines


def read_limits(
    token_reader: Callable[[], str] = read_token,
    fetcher: Callable[[str], Any] = request_usage,
) -> list[str]:
    try:
        return report(parse_usage(fetcher(token_reader())))
    except Exception as exc:  # noqa: BLE001
        # Only the type: the text of an HTTP or parsing error could echo a header or the
        # credentials file. Any failure here must leave work running, not stop it.
        return [f"usage unavailable ({type(exc).__name__})", f"parallel: {UNREADABLE_PARALLEL}"]


def main() -> None:
    print("\n".join(read_limits()))
    sys.exit(0)


if __name__ == "__main__":
    main()
