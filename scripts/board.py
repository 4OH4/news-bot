#!/usr/bin/env python3
"""Move issues between Status columns on the GitHub Project board.

Used by the lead agent and by worker agents (see CLAUDE.md, "Agent automation"):

  python3 scripts/board.py show                    # issues grouped by status
  python3 scripts/board.py set 12 "In progress"    # move issue #12
  python3 scripts/board.py promote --dry-run       # list Backlog issues that are now unblocked
  python3 scripts/board.py promote                 # ...and move them to Ready

`promote` first moves closed issues to Done, then moves open `agent-ready` issues
whose every "Depends on #N" issue is closed to Ready. `needs-human` issues are never
promoted automatically.

Requirements: Python 3.10+ (standard library only); gh CLI with the `project` scope.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any

DEFAULT_REPO = "4OH4/news-bot"
DEFAULT_OWNER = "4OH4"
DEFAULT_PROJECT = 1
STATUSES = ["Backlog", "Ready", "In progress", "In review", "Done"]
DEPENDS_RE = re.compile(r"^Depends on #(\d+)", re.MULTILINE)


def gh(*args: str) -> str:
    result = subprocess.run(
        ["gh", *args], capture_output=True, text=True, encoding="utf-8", check=False
    )
    if result.returncode != 0:
        sys.exit(f"gh {' '.join(args[:3])} ... failed:\n{result.stderr.strip()}")
    return result.stdout


def gh_json(*args: str) -> Any:
    return json.loads(gh(*args))


class Board:
    """The project's items plus the ids needed to edit their Status field."""

    def __init__(self, owner: str, number: int) -> None:
        self.owner, self.number = owner, number
        self.project_id: str = gh_json(
            "project", "view", str(number), "--owner", owner, "--format", "json"
        )["id"]
        fields = gh_json(
            "project", "field-list", str(number), "--owner", owner, "--format", "json"
        )["fields"]
        status = next(f for f in fields if f["name"] == "Status")
        self.field_id: str = status["id"]
        self.options: dict[str, str] = {o["name"]: o["id"] for o in status["options"]}
        self.items: list[dict[str, Any]] = gh_json(
            "project", "item-list", str(number), "--owner", owner,
            "--limit", "1000", "--format", "json",
        )["items"]

    def item_for(self, issue: int) -> dict[str, Any]:
        for item in self.items:
            if item["content"].get("number") == issue:
                return item
        sys.exit(f"issue #{issue} is not on project {self.number}")

    def set_status(self, issue: int, status: str) -> None:
        if status not in self.options:
            sys.exit(f"unknown status {status!r}; expected one of {list(self.options)}")
        item = self.item_for(issue)
        gh(
            "project", "item-edit", "--id", item["id"], "--project-id", self.project_id,
            "--field-id", self.field_id, "--single-select-option-id", self.options[status],
        )
        print(f"#{issue}: {item.get('status')} -> {status}")


def cmd_show(board: Board, _: argparse.Namespace) -> None:
    by_status: dict[str, list[dict[str, Any]]] = {}
    for item in board.items:
        by_status.setdefault(item.get("status") or "(none)", []).append(item)
    for status in [*STATUSES, *sorted(set(by_status) - set(STATUSES))]:
        rows = sorted(by_status.get(status, []), key=lambda i: i["content"].get("number", 0))
        if not rows:
            continue
        print(f"{status}:")
        for item in rows:
            kind = "human" if "needs-human" in item.get("labels", []) else "agent"
            print(f"  #{item['content'].get('number')} [{kind}] {item['title']}")


def cmd_set(board: Board, args: argparse.Namespace) -> None:
    board.set_status(args.issue, args.status)


def cmd_promote(board: Board, args: argparse.Namespace) -> None:
    issues = gh_json(
        "issue", "list", "--repo", args.repo, "--state", "all", "--limit", "1000",
        "--json", "number,state,body,labels",
    )
    state = {i["number"]: i["state"] for i in issues}

    # The board's built-in "closed -> Done" workflow isn't enabled, so sync it here.
    for item in board.items:
        n = item["content"].get("number")
        if state.get(n) == "CLOSED" and item.get("status") != "Done":
            if args.dry_run:
                print(f"#{n}: would move {item.get('status')} -> Done (closed)")
            else:
                board.set_status(n, "Done")

    promoted = 0
    for issue in sorted(issues, key=lambda i: i["number"]):
        n = issue["number"]
        labels = {label["name"] for label in issue["labels"]}
        if issue["state"] != "OPEN" or "agent-ready" not in labels or "blocked" in labels:
            continue
        item = next((i for i in board.items if i["content"].get("number") == n), None)
        if item is None or item.get("status") != "Backlog":
            continue
        deps = [int(d) for d in DEPENDS_RE.findall(issue["body"] or "")]
        open_deps = [d for d in deps if state.get(d) != "CLOSED"]
        if open_deps:
            continue
        promoted += 1
        if args.dry_run:
            print(f"#{n}: would move Backlog -> Ready (deps {deps or 'none'} closed)")
        else:
            board.set_status(n, "Ready")
    if not promoted:
        print("nothing to promote")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--owner", default=DEFAULT_OWNER)
    parser.add_argument("--project", type=int, default=DEFAULT_PROJECT)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show", help="list issues grouped by status").set_defaults(fn=cmd_show)
    p_set = sub.add_parser("set", help="move an issue to a status")
    p_set.add_argument("issue", type=int)
    p_set.add_argument("status", choices=STATUSES)
    p_set.set_defaults(fn=cmd_set)
    p_promote = sub.add_parser("promote", help="move unblocked agent-ready issues to Ready")
    p_promote.add_argument("--dry-run", action="store_true")
    p_promote.set_defaults(fn=cmd_promote)
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    args.fn(Board(args.owner, args.project), args)


if __name__ == "__main__":
    main()
