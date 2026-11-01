"""Tests for scripts/lead.py: a locked worktree is kept back, not a failure (no network)."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"_script_{name}", SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lead = load("lead")

LOCK_REASON = "claude agent 4242 is using this"


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A main checkout with an origin, and two merged agent worktrees (one locked)."""
    origin = tmp_path / "origin.git"
    git("init", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    root = tmp_path / "main"
    git("clone", str(origin), str(root), cwd=tmp_path)
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    git("add", "README.md", cwd=root)
    git("commit", "-m", "init", cwd=root)
    git("push", "origin", "HEAD:main", cwd=root)
    git("fetch", "origin", cwd=root)
    for name in ("agent-locked", "agent-free"):
        path = root / ".claude" / "worktrees" / name
        git("worktree", "add", "-b", f"worktree-{name}", str(path), "origin/main", cwd=root)
    git(
        "worktree",
        "lock",
        "--reason",
        LOCK_REASON,
        str(root / ".claude" / "worktrees" / "agent-locked"),
        cwd=root,
    )
    return root


def branches(root: Path) -> set[str]:
    return set(git("branch", "--format=%(refname:short)", cwd=root).splitlines())


def test_locked_merged_worktree_is_kept_back_with_gits_reason(repo: Path) -> None:
    locked = repo / ".claude" / "worktrees" / "agent-locked"

    kept = lead.tidy_worktrees(repo, set())

    assert locked.exists()
    entry = next(k for k in kept if "agent-locked" in k)
    assert "locked" in entry
    assert LOCK_REASON in entry


def test_unlocked_merged_worktree_is_still_removed(repo: Path) -> None:
    free = repo / ".claude" / "worktrees" / "agent-free"

    kept = lead.tidy_worktrees(repo, set())

    assert not free.exists()
    assert not any("agent-free" in k for k in kept)


def test_branch_of_locked_worktree_is_kept_and_tidy_does_not_fail(repo: Path) -> None:
    lead.tidy_worktrees(repo, set())

    lead.tidy_branches(repo, set())

    assert "worktree-agent-locked" in branches(repo)
    assert "worktree-agent-free" not in branches(repo)


def test_start_runs_board_sync_and_lists_locked_worktree_under_kept_back(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(lead, "main_checkout", lambda: repo)
    monkeypatch.setattr(lead, "update_main", lambda root: [])
    monkeypatch.setattr(lead, "ensure_env_and_hook", lambda root: None)
    monkeypatch.setattr(lead, "merged_pr_heads", lambda: set())
    monkeypatch.setattr(lead, "sync_board", lambda root: calls.append("board") or [])
    monkeypatch.setattr(lead, "summary", lambda: calls.append("summary"))

    lead.cmd("start")

    assert calls == ["board", "summary"]
    printed = capsys.readouterr().out
    kept_section = printed.split("Kept back", 1)[1]
    assert "agent-locked" in kept_section
    assert LOCK_REASON in kept_section
    assert (repo / ".claude" / "worktrees" / "agent-locked").exists()
    assert not (repo / ".claude" / "worktrees" / "agent-free").exists()


def test_finish_also_carries_on_past_a_locked_worktree(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(lead, "main_checkout", lambda: repo)
    monkeypatch.setattr(lead, "update_main", lambda root: [])
    monkeypatch.setattr(lead, "ensure_env_and_hook", lambda root: None)
    monkeypatch.setattr(lead, "merged_pr_heads", lambda: set())
    monkeypatch.setattr(lead, "sync_board", lambda root: calls.append("board") or [])
    monkeypatch.setattr(lead, "summary", lambda: calls.append("summary"))

    lead.cmd("finish")

    assert calls == ["board", "summary"]
    assert "Session tidy-up complete" in capsys.readouterr().out
