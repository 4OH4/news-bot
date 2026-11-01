#!/usr/bin/env python3
"""Print how much context the lead's session is using, and whether to checkpoint.

  python3 scripts/lead.py context

The lead runs this at each batch close (AUTOMATION.md §11), through lead.py because that
is on the permission allow list. It reads the token counts Claude Code records in the
newest session transcript for this folder.

The transcript format is undocumented, so a failed lookup prints "context unavailable"
and exits 0: the lead then goes by the number of batches.

Requirements: Python 3.10+ (standard library only).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"
RESTART_AT = 150_000


def transcript_dir(folder: Path, projects: Path = PROJECTS) -> Path:
    """Return the folder holding transcripts for a working folder."""
    return projects / re.sub(r"[^a-zA-Z0-9]", "-", str(folder))


def latest_transcript(directory: Path) -> Path | None:
    """Return the most recently written transcript, if there is one."""
    files = list(directory.glob("*.jsonl"))
    return max(files, key=lambda path: path.stat().st_mtime) if files else None


def context_tokens(transcript: Path) -> int | None:
    """Return the context size at the main session's last turn."""
    latest = None
    for line in transcript.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("isSidechain"):
            continue
        message = entry.get("message")
        usage = message.get("usage") if isinstance(message, dict) else None
        if entry.get("type") != "assistant" or not isinstance(usage, dict):
            continue
        total = sum(
            usage.get(key) or 0
            for key in (
                "input_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            )
        )
        if total:
            latest = total
    return latest


def report(tokens: int | None) -> str:
    """Return the lines to print for a context size."""
    if tokens is None:
        return "context unavailable: go by the number of batches"
    line = f"context: {tokens // 1000}k tokens"
    if tokens >= RESTART_AT:
        return f"{line}\ncheckpoint: rewrite the handoff note, then carry on"
    return f"{line}\nok: below {RESTART_AT // 1000}k"


def main(folder: Path) -> None:
    """Print the report for a working folder's newest session."""
    try:
        transcript = latest_transcript(transcript_dir(folder))
        print(report(context_tokens(transcript) if transcript else None))
    except OSError:
        print(report(None))
