"""Tests for scripts/board.py and scripts/lead.py with canned `gh` responses (no network)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"_script_{name}", SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


board = load("board")
lead = load("lead")

OPTIONS = [{"id": f"opt-{s}", "name": s} for s in board.STATUSES]
FIELD = {"id": "field-1", "options": OPTIONS}
RATE = {"cost": 1, "remaining": 4990, "resetAt": "2026-10-12T15:00:00Z"}
MUTATION_OK = {"data": {"updateProjectV2ItemFieldValue": {}}}


def done(stdout: Any = "", stderr: str = "", code: int = 0) -> subprocess.CompletedProcess[str]:
    text = stdout if isinstance(stdout, str) else json.dumps(stdout)
    return subprocess.CompletedProcess(["gh"], code, text, stderr)


def issue_node(
    n: int, status: str, state: str = "OPEN", labels: tuple[str, ...] = (), body: str = ""
) -> dict[str, Any]:
    return {
        "id": f"item-{n}",
        "status": {"name": status},
        "content": {
            "__typename": "Issue",
            "number": n,
            "title": f"Issue {n}",
            "state": state,
            "body": body,
            "labels": {"nodes": [{"name": label} for label in labels]},
        },
    }


def page(nodes: list[dict[str, Any]], more: bool = False) -> dict[str, Any]:
    items = {"pageInfo": {"hasNextPage": more, "endCursor": "c1"}, "nodes": nodes}
    project = {"id": "proj-1", "field": FIELD, "items": items}
    return {"data": {"rateLimit": RATE, "user": {"projectV2": project}}}


@pytest.fixture(autouse=True)
def fresh_tally(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(board, "TALLY", board.Tally())


def script_gh(monkeypatch: pytest.MonkeyPatch, replies: list[Any]) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def fake(*args: str) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return replies.pop(0)

    monkeypatch.setattr(board, "_run_gh", fake)
    return calls


BOARD_NODES = [
    issue_node(1, "Backlog", "CLOSED", ("agent-ready",)),
    issue_node(2, "Backlog", "OPEN", ("agent-ready",), "Depends on #1"),
    issue_node(3, "Backlog", "OPEN", ("agent-ready",), "Depends on #4"),
    issue_node(4, "In progress", "OPEN", ("agent-ready",)),
    issue_node(5, "Backlog", "OPEN", ("needs-human",)),
    issue_node(6, "Backlog", "OPEN", ("agent-ready", "blocked")),
]


def test_load_paginates_one_query_per_page(monkeypatch: pytest.MonkeyPatch) -> None:
    replies = [done(page(BOARD_NODES[:3], more=True)), done(page(BOARD_NODES[3:]))]
    calls = script_gh(monkeypatch, replies)
    loaded = board.Board.load("o", 1)
    assert [i["number"] for i in loaded.items] == [1, 2, 3, 4, 5, 6]
    assert len(calls) == 2
    assert "after=c1" in calls[1]
    assert board.TALLY.cost == 2


def test_promote_rules(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    script_gh(monkeypatch, [done(page(BOARD_NODES))])
    board.promote(board.Board.load("o", 1), "o/r", dry_run=True)
    out = capsys.readouterr().out
    assert "#1: would move Backlog -> Done (closed)" in out
    assert "#2: would move Backlog -> Ready" in out
    for n in (3, 5, 6):  # open dependency / needs-human / blocked
        assert f"#{n}:" not in out


def test_promote_asks_rest_for_dependencies_off_the_board(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nodes = [issue_node(7, "Backlog", "OPEN", ("agent-ready",), "Depends on #99")]
    replies = [done(page(nodes)), done({"state": "closed"}), done(MUTATION_OK)]
    calls = script_gh(monkeypatch, replies)
    loaded = board.Board.load("o", 1)
    board.promote(loaded, "o/r", dry_run=False)
    assert calls[1] == ("api", "repos/o/r/issues/99")
    assert loaded.items[0]["status"] == "Ready"


def test_show_groups_by_status(monkeypatch: pytest.MonkeyPatch) -> None:
    script_gh(monkeypatch, [done(page(BOARD_NODES))])
    lines = board.render_show(board.Board.load("o", 1))
    assert lines[0] == "Backlog:"
    assert "  #5 [human] Issue 5" in lines
    assert "  #4 [agent] Issue 4" in lines
    assert lines.index("In progress:") > lines.index("Backlog:")


def set_query_reply(status: str = "Ready") -> dict[str, Any]:
    nodes = [
        {"id": "other", "project": {"id": "proj-x"}, "status": None},
        {"id": "item-9", "project": {"id": "proj-1"}, "status": {"name": status}},
    ]
    return {
        "data": {
            "rateLimit": RATE,
            "repository": {"issue": {"projectItems": {"nodes": nodes}}},
            "user": {"projectV2": {"id": "proj-1", "field": FIELD}},
        }
    }


def test_set_uses_one_query_and_one_mutation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = script_gh(monkeypatch, [done(set_query_reply()), done(MUTATION_OK)])
    board.set_status_directly("o/r", "o", 1, 9, "In progress")
    assert len(calls) == 2
    assert "item=item-9" in calls[1] and "option=opt-In progress" in calls[1]
    assert "#9: Ready -> In progress" in capsys.readouterr().out
    assert board.TALLY.cost == 2


def test_set_rejects_unknown_status(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = script_gh(monkeypatch, [])
    with pytest.raises(SystemExit, match="unknown status"):
        board.set_status_directly("o/r", "o", 1, 9, "Nope")
    assert calls == []


def test_set_fails_when_issue_not_on_project(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = set_query_reply()
    reply["data"]["repository"]["issue"]["projectItems"]["nodes"] = []
    script_gh(monkeypatch, [done(reply)])
    with pytest.raises(SystemExit, match="not on project"):
        board.set_status_directly("o/r", "o", 1, 9, "Ready")


def test_rate_limited_response_reports_reset_time(monkeypatch: pytest.MonkeyPatch) -> None:
    limited = {
        "data": {"rateLimit": {"cost": 0, "remaining": 0, "resetAt": "2026-10-12T15:30:00Z"}},
        "errors": [{"type": "RATE_LIMIT", "message": "API rate limit exceeded"}],
    }
    script_gh(monkeypatch, [done(limited, code=1)])
    with pytest.raises(SystemExit) as exc:
        board.main(["show"])
    assert exc.value.code == "GitHub GraphQL rate limit exhausted; resets at 2026-10-12 15:30 UTC"


def test_unknown_owner_type_is_treated_as_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    reset = {"resources": {"graphql": {"reset": 1791815400}}}  # 2026-10-12 14:30 UTC
    script_gh(monkeypatch, [done("", "unknown owner type", 1), done(reset)])
    with pytest.raises(SystemExit) as exc:
        board.main(["show"])
    assert exc.value.code == "GitHub GraphQL rate limit exhausted; resets at 2026-10-12 14:30 UTC"


def test_main_prints_allowance_unless_quiet(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script_gh(monkeypatch, [done(page(BOARD_NODES)), done(page(BOARD_NODES))])
    board.main(["show"])
    assert "4990 remaining" in capsys.readouterr().err
    board.main(["--quiet", "show"])
    assert capsys.readouterr().err == ""


def test_lead_loads_the_board_once(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(*args: str, **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        out = (
            f"nothing to promote\n{lead.BOARD_MARKER}\n"
            "Ready:\n  #2 [agent] Issue 2\nDone:\n  #1 [agent] x\n"
        )
        return subprocess.CompletedProcess(list(args), 0, out, "GraphQL: 1 point(s) used")

    monkeypatch.setattr(lead, "run", fake_run)
    lead.status_board(lead.sync_board(Path(".")))
    shown = capsys.readouterr().out
    assert len(calls) == 1 and "board.py" in calls[0][1] and "--show" in calls[0]
    assert "GraphQL: 1 point(s) used" in shown
    assert "#2 [agent] Issue 2" in shown and "#1" not in shown
