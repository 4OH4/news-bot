#!/usr/bin/env python3
"""Move issues between Status columns on the GitHub Project board.

Used by the lead agent and by worker agents (see CLAUDE.md, "Agent automation"):

  python3 scripts/board.py show                    # issues grouped by status
  python3 scripts/board.py set 12 "In progress"    # move issue #12
  python3 scripts/board.py promote --dry-run       # list Backlog issues that are now unblocked
  python3 scripts/board.py promote                 # ...and move them to Ready
  python3 scripts/board.py promote --show          # promote, then print the board

Statuses: Backlog, Ready, In progress, Agent review, Human review, Done. A worker
sets "Agent review" when it opens its PR; the lead sets "Human review" when the PR
waits only for the human (see CLAUDE.md, "Agent automation").

`promote` first moves closed issues to Done, then moves open `agent-ready` issues
whose every "Depends on #N" issue is closed to Ready. `needs-human` issues are never
promoted automatically.

GraphQL budget: GitHub allows 5,000 GraphQL points per user per hour, shared by every
agent and `gh` itself. `set` costs ~1 point (one targeted query plus one mutation);
`show` and `promote` read the board with one paginated query (~1 point per 50 items).
The points used and the allowance left are printed to stderr after each command
(silence with --quiet). If the allowance is exhausted the script says when it resets
and exits non-zero.

Requirements: Python 3.10+ (standard library only); gh CLI with the `project` scope.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any

DEFAULT_REPO = "4OH4/news-bot"
DEFAULT_OWNER = "4OH4"
DEFAULT_PROJECT = 1
STATUSES = ["Backlog", "Ready", "In progress", "Agent review", "Human review", "Done"]
DEPENDS_RE = re.compile(r"^Depends on #(\d+)", re.MULTILINE)
BOARD_MARKER = "--- board ---"  # separates promote's output from `promote --show`'s listing
PAGE_SIZE = 50  # roughly the board's size; GraphQL cost scales with `first:`

_STATUS_FIELD = """field(name: "Status") {
        ... on ProjectV2SingleSelectField { id options { id name } }
      }"""
_RATE = "rateLimit { cost remaining resetAt }"

SET_QUERY = f"""
query($owner: String!, $repo: String!, $issue: Int!, $project: Int!) {{
  {_RATE}
  repository(owner: $owner, name: $repo) {{
    issue(number: $issue) {{
      projectItems(first: 10) {{
        nodes {{
          id
          project {{ id }}
          status: fieldValueByName(name: "Status") {{
            ... on ProjectV2ItemFieldSingleSelectValue {{ name }}
          }}
        }}
      }}
    }}
  }}
  user(login: $owner) {{
    projectV2(number: $project) {{
      id
      {_STATUS_FIELD}
    }}
  }}
}}"""

ITEMS_QUERY = f"""
query($owner: String!, $project: Int!, $after: String) {{
  {_RATE}
  user(login: $owner) {{
    projectV2(number: $project) {{
      id
      {_STATUS_FIELD}
      items(first: {PAGE_SIZE}, after: $after) {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{
          id
          status: fieldValueByName(name: "Status") {{
            ... on ProjectV2ItemFieldSingleSelectValue {{ name }}
          }}
          content {{
            __typename
            ... on Issue {{ number title state body labels(first: 20) {{ nodes {{ name }} }} }}
            ... on PullRequest {{ number title state }}
          }}
        }}
      }}
    }}
  }}
}}"""

# Mutations can't query `rateLimit`; each costs 1 point (see Tally.record_mutation).
MUTATION = """
mutation($project: ID!, $item: ID!, $field: ID!, $option: String!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $project, itemId: $item, fieldId: $field,
    value: {singleSelectOptionId: $option}
  }) { projectV2Item { id } }
}"""


class RateLimitError(Exception):
    """GitHub's GraphQL allowance is exhausted; the message says when it resets."""


class Tally:
    """GraphQL points spent and the allowance left, as reported by the responses."""

    def __init__(self) -> None:
        self.cost = 0
        self.remaining: int | None = None

    def record(self, rate: dict[str, Any] | None) -> None:
        if rate:
            self.cost += rate.get("cost", 0)
            self.remaining = rate.get("remaining", self.remaining)

    def record_mutation(self) -> None:
        """A mutation costs 1 point but reports no `rateLimit`, so count it here."""
        self.cost += 1
        if self.remaining is not None:
            self.remaining -= 1

    def line(self) -> str:
        left = "unknown" if self.remaining is None else str(self.remaining)
        return f"GraphQL: {self.cost} point(s) used, {left} remaining this hour"


TALLY = Tally()


def _run_gh(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gh", *args], capture_output=True, text=True, encoding="utf-8", check=False
    )


def gh(*args: str) -> str:
    result = _run_gh(*args)
    if result.returncode != 0:
        if "unknown owner type" in result.stderr:  # gh's misleading rate-limit symptom
            raise RateLimitError(rate_limit_message(None))
        sys.exit(f"gh {' '.join(args[:3])} ... failed:\n{result.stderr.strip()}")
    return result.stdout


def format_reset(reset: str | int | None) -> str:
    """Render an ISO-8601 time or epoch seconds as 'YYYY-MM-DD HH:MM UTC'."""
    if reset is None:
        return "an unknown time"
    if isinstance(reset, int):
        moment = datetime.fromtimestamp(reset, tz=timezone.utc)
    else:
        moment = datetime.fromisoformat(reset.replace("Z", "+00:00")).astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%d %H:%M UTC")


def rate_limit_message(reset_at: str | int | None) -> str:
    if reset_at is None:  # ask the REST API, which has its own allowance
        result = _run_gh("api", "rate_limit")
        try:
            reset_at = json.loads(result.stdout)["resources"]["graphql"]["reset"]
        except (ValueError, KeyError):
            reset_at = None
    return f"GitHub GraphQL rate limit exhausted; resets at {format_reset(reset_at)}"


def graphql(query: str, **variables: str | int | None) -> dict[str, Any]:
    """Run one GraphQL query and return its `data`, tracking the points spent."""
    args = ["api", "graphql", "-f", f"query={query}"]
    for name, value in variables.items():
        if value is not None:
            args += ["-F" if isinstance(value, int) else "-f", f"{name}={value}"]
    result = _run_gh(*args)
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        payload = {}
    errors = payload.get("errors") or []
    data = payload.get("data") or {}
    if any(e.get("type") == "RATE_LIMIT" for e in errors) or (
        "rate limit" in result.stderr.lower() and not data
    ):
        raise RateLimitError(rate_limit_message((data.get("rateLimit") or {}).get("resetAt")))
    if "unknown owner type" in result.stderr:
        raise RateLimitError(rate_limit_message(None))
    if result.returncode != 0 or errors:
        detail = json.dumps(errors) if errors else result.stderr.strip()
        sys.exit(f"gh api graphql failed:\n{detail}")
    TALLY.record(data.get("rateLimit"))
    return data


def parse_item(node: dict[str, Any]) -> dict[str, Any]:
    content = node.get("content") or {}
    return {
        "id": node["id"],
        "status": (node.get("status") or {}).get("name"),
        "kind": content.get("__typename"),
        "number": content.get("number"),
        "title": content.get("title") or "(untitled)",
        "state": content.get("state"),
        "body": content.get("body") or "",
        "labels": [label["name"] for label in (content.get("labels") or {}).get("nodes", [])],
    }


class Board:
    """The project's items plus the ids needed to edit their Status field."""

    def __init__(
        self, project_id: str, field_id: str, options: dict[str, str], items: list[dict[str, Any]]
    ) -> None:
        self.project_id = project_id
        self.field_id = field_id
        self.options = options
        self.items = items

    @classmethod
    def load(cls, owner: str, number: int) -> Board:
        """Read every item with one paginated query asking only for what's used."""
        items: list[dict[str, Any]] = []
        after: str | None = None
        while True:
            project = graphql(ITEMS_QUERY, owner=owner, project=number, after=after)["user"][
                "projectV2"
            ]
            items += [parse_item(n) for n in project["items"]["nodes"]]
            page = project["items"]["pageInfo"]
            if not page["hasNextPage"]:
                break
            after = page["endCursor"]
        return cls(
            project["id"],
            project["field"]["id"],
            {o["name"]: o["id"] for o in project["field"]["options"]},
            items,
        )

    def item_for(self, issue: int) -> dict[str, Any]:
        for item in self.items:
            if item["number"] == issue:
                return item
        sys.exit(f"issue #{issue} is not on the project")

    def set_status(self, issue: int, status: str) -> None:
        if status not in self.options:
            sys.exit(f"unknown status {status!r}; expected one of {list(self.options)}")
        item = self.item_for(issue)
        update_status(self.project_id, item["id"], self.field_id, self.options[status])
        print(f"#{issue}: {item['status']} -> {status}")
        item["status"] = status


def update_status(project_id: str, item_id: str, field_id: str, option_id: str) -> None:
    graphql(MUTATION, project=project_id, item=item_id, field=field_id, option=option_id)
    TALLY.record_mutation()


def set_status_directly(repo: str, owner: str, number: int, issue: int, status: str) -> None:
    """Move one card without loading the board: one targeted query, one mutation."""
    if status not in STATUSES:
        sys.exit(f"unknown status {status!r}; expected one of {STATUSES}")
    name = repo.split("/", 1)[1]
    data = graphql(SET_QUERY, owner=owner, repo=name, issue=issue, project=number)
    project = data["user"]["projectV2"]
    options = {o["name"]: o["id"] for o in project["field"]["options"]}
    if status not in options:
        sys.exit(f"unknown status {status!r}; expected one of {list(options)}")
    nodes = ((data["repository"]["issue"] or {}).get("projectItems") or {}).get("nodes", [])
    item = next((n for n in nodes if n["project"]["id"] == project["id"]), None)
    if item is None:
        sys.exit(f"issue #{issue} is not on project {number}")
    update_status(project["id"], item["id"], project["field"]["id"], options[status])
    print(f"#{issue}: {(item.get('status') or {}).get('name')} -> {status}")


def render_show(board: Board) -> list[str]:
    """The board grouped by status, one line per issue."""
    by_status: dict[str, list[dict[str, Any]]] = {}
    for item in board.items:
        by_status.setdefault(item["status"] or "(none)", []).append(item)
    lines: list[str] = []
    for status in [*STATUSES, *sorted(set(by_status) - set(STATUSES))]:
        rows = sorted(by_status.get(status, []), key=lambda i: i["number"] or 0)
        if not rows:
            continue
        lines.append(f"{status}:")
        for item in rows:
            kind = "human" if "needs-human" in item["labels"] else "agent"
            lines.append(f"  #{item['number']} [{kind}] {item['title']}")
    return lines


def promote(board: Board, repo: str, dry_run: bool) -> None:
    """Sync closed -> Done, then move unblocked open agent-ready issues to Ready."""
    issues = [i for i in board.items if i["kind"] == "Issue"]
    state = {i["number"]: i["state"] for i in issues}

    def dep_state(n: int) -> str | None:
        if n not in state:  # a dependency that isn't on the board: ask REST
            state[n] = json.loads(gh("api", f"repos/{repo}/issues/{n}"))["state"].upper()
        return state[n]

    # The board's built-in "closed -> Done" workflow isn't enabled, so sync it here.
    for item in issues:
        n = item["number"]
        if item["state"] == "CLOSED" and item["status"] != "Done":
            if dry_run:
                print(f"#{n}: would move {item['status']} -> Done (closed)")
            else:
                board.set_status(n, "Done")

    promoted = 0
    for issue in sorted(issues, key=lambda i: i["number"]):
        n = issue["number"]
        labels = set(issue["labels"])
        if issue["state"] != "OPEN" or "agent-ready" not in labels or "blocked" in labels:
            continue
        if issue["status"] != "Backlog":
            continue
        deps = [int(d) for d in DEPENDS_RE.findall(issue["body"])]
        if any(dep_state(d) != "CLOSED" for d in deps):
            continue
        promoted += 1
        if dry_run:
            print(f"#{n}: would move Backlog -> Ready (deps {deps or 'none'} closed)")
        else:
            board.set_status(n, "Ready")
    if not promoted:
        print("nothing to promote")


def cmd_show(args: argparse.Namespace) -> None:
    print("\n".join(render_show(Board.load(args.owner, args.project))))


def cmd_set(args: argparse.Namespace) -> None:
    set_status_directly(args.repo, args.owner, args.project, args.issue, args.status)


def cmd_promote(args: argparse.Namespace) -> None:
    board = Board.load(args.owner, args.project)
    promote(board, args.repo, args.dry_run)
    if args.show:
        print(BOARD_MARKER)
        print("\n".join(render_show(board)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--owner", default=DEFAULT_OWNER)
    parser.add_argument("--project", type=int, default=DEFAULT_PROJECT)
    parser.add_argument("--quiet", action="store_true", help="don't print the GraphQL allowance")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show", help="list issues grouped by status").set_defaults(fn=cmd_show)
    p_set = sub.add_parser("set", help="move an issue to a status")
    p_set.add_argument("issue", type=int)
    p_set.add_argument("status", choices=STATUSES, help="one of: %(choices)s")
    p_set.set_defaults(fn=cmd_set)
    p_promote = sub.add_parser("promote", help="move unblocked agent-ready issues to Ready")
    p_promote.add_argument("--dry-run", action="store_true")
    p_promote.add_argument("--show", action="store_true", help="then print the board")
    p_promote.set_defaults(fn=cmd_promote)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        args.fn(args)
    except RateLimitError as err:
        sys.exit(str(err))
    if not args.quiet:
        print(TALLY.line(), file=sys.stderr)


if __name__ == "__main__":
    main()
