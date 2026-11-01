#!/usr/bin/env python3
"""Standard start-up and tidy-up for a lead development session.

  python3 scripts/lead.py start     # before starting work
  python3 scripts/lead.py finish    # before ending the session
  python3 scripts/lead.py context   # at a batch close: is it time to checkpoint?

Both commands are safe to re-run. They run from any worktree, but always act on
the main checkout:

* bring the main checkout up to date with origin/main (fast-forward only);
* sync backend dependencies and make sure the shared pre-commit hook points at
  the main checkout's environment, not at an agent worktree (see CLAUDE.md);
* remove agent worktrees under .claude/worktrees/ whose work is merged and
  clean, and delete local branches that are merged or whose PR was merged;
* sync the Project board (closed -> Done, unblocked agent-ready -> Ready);
* print a status summary: open PRs, Ready issues, and worktrees kept back.

Nothing with uncommitted or unpushed work is ever deleted; it is listed instead.
`finish` also warns about anything left that would surprise the next session.

Requirements: Python 3.10+ (standard library only), git, uv, gh (with `project` scope).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = "4OH4/news-bot"
BOARD_MARKER = "--- board ---"  # must match scripts/board.py
TIMEOUT = 180  # seconds; network calls must never hang a session


def run(
    *args: str, cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(args), cwd=cwd, capture_output=True, text=True, encoding="utf-8",
            timeout=TIMEOUT, check=False,
        )
    except subprocess.TimeoutExpired:
        sys.exit(f"timed out after {TIMEOUT}s: {' '.join(args)}")
    if check and result.returncode != 0:
        sys.exit(f"failed: {' '.join(args)}\n{result.stderr.strip()}")
    return result


def out(*args: str, cwd: Path | None = None) -> str:
    return run(*args, cwd=cwd).stdout.strip()


def step(message: str) -> None:
    print(f"\n== {message}")


def main_checkout() -> Path:
    """The first entry of `git worktree list` is always the main working tree."""
    first = out("git", "worktree", "list", "--porcelain").splitlines()[0]
    return Path(first.removeprefix("worktree "))


def update_main(root: Path) -> list[str]:
    warnings: list[str] = []
    run("git", "fetch", "--prune", "origin", cwd=root)
    branch = out("git", "branch", "--show-current", cwd=root)
    dirty = out("git", "status", "--porcelain", "--untracked-files=no", cwd=root)
    if branch != "main":
        warnings.append(f"main checkout is on '{branch}', not main; left as is")
    elif dirty:
        warnings.append("main checkout has uncommitted changes; not updated")
    else:
        run("git", "merge", "--ff-only", "origin/main", cwd=root)
        print(f"main at {out('git', 'log', '--oneline', '-1', cwd=root)}")
    return warnings


def ensure_env_and_hook(root: Path) -> None:
    run("uv", "sync", "--quiet", cwd=root / "backend")
    hook = root / ".git" / "hooks" / "pre-commit"
    expected = f"INSTALL_PYTHON={root / 'backend' / '.venv' / 'bin' / 'python'}"
    if hook.exists() and expected in hook.read_text(encoding="utf-8"):
        print("dependencies synced; pre-commit hook OK")
        return
    run("uv", "run", "--project", "backend", "pre-commit", "install", cwd=root)
    print("dependencies synced; pre-commit hook (re)installed from the main checkout")


def merged_pr_heads() -> set[str]:
    prs = json.loads(out(
        "gh", "pr", "list", "--repo", REPO, "--state", "merged", "--limit", "200",
        "--json", "headRefName",
    ))
    return {pr["headRefName"] for pr in prs}


def is_merged(branch: str, root: Path, merged_heads: set[str]) -> bool:
    """True only if deleting the branch can't lose work.

    The branch must be fully contained in origin/main, and either be the head of
    a merged PR or a placeholder branch created by the agent tooling
    (`worktree-agent-*`). A brand-new branch with no commits is also contained in
    main, so containment alone isn't enough: a worker may be about to use it.
    """
    probe = run("git", "merge-base", "--is-ancestor", branch, "origin/main", cwd=root, check=False)
    if probe.returncode != 0:
        return False
    return branch in merged_heads or branch.startswith("worktree-agent-")


def tidy_worktrees(root: Path, merged_heads: set[str]) -> list[str]:
    kept: list[str] = []
    blocks = out("git", "worktree", "list", "--porcelain", cwd=root).split("\n\n")
    for block in blocks:
        fields = dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
        path = Path(fields.get("worktree", ""))
        if ".claude/worktrees/" not in path.as_posix():
            continue
        branch = fields.get("branch", "").removeprefix("refs/heads/")
        if not path.exists():
            continue  # pruned below
        label = f"{path.name} ({branch or 'detached'})"
        status = run("git", "status", "--porcelain", cwd=path, check=False)
        # A worktree that can't be inspected is never removed.
        problems: list[str] = []
        if status.returncode != 0:
            problems.append(f"git status failed: {status.stderr.strip()}")
        elif status.stdout.strip():
            problems.append("uncommitted changes")
        if branch and not is_merged(branch, root, merged_heads):
            problems.append("not merged yet")
        if "locked" in fields or "locked" in block.split():
            reason = fields.get("locked", "").strip()
            lock = "locked" + (f" ({reason})" if reason else "")
            kept.append(f"{label}: {', '.join([lock, *problems])}")
        elif problems:
            kept.append(f"{label}: {', '.join(problems)}")
        else:
            # Never force: a refusal (e.g. a lock taken since listing) is reported.
            result = run("git", "worktree", "remove", str(path), cwd=root, check=False)
            if result.returncode == 0:
                print(f"removed worktree {path.name}")
            else:
                kept.append(f"{label}: {result.stderr.strip()}")
    run("git", "worktree", "prune", cwd=root)
    return kept


def tidy_branches(root: Path, merged_heads: set[str]) -> list[str]:
    kept: list[str] = []
    in_use = {
        line.removeprefix("branch refs/heads/")
        for line in out("git", "worktree", "list", "--porcelain", cwd=root).splitlines()
        if line.startswith("branch ")
    }
    for branch in out("git", "branch", "--format=%(refname:short)", cwd=root).splitlines():
        if branch == "main" or branch in in_use:
            continue
        if is_merged(branch, root, merged_heads):
            result = run("git", "branch", "-D", branch, cwd=root, check=False)
            if result.returncode == 0:
                print(f"deleted branch {branch}")
            else:
                kept.append(f"branch {branch}: {result.stderr.strip()}")
        else:
            kept.append(f"branch {branch}: not merged")
    return kept


def sync_board(root: Path) -> list[str]:
    """Promote and read the board in one load (one GraphQL query, not two).

    Returns the board listing for `status_board`; promote's own output is printed.
    """
    result = run("python3", "scripts/board.py", "promote", "--show", cwd=root)
    promoted, _, listing = result.stdout.partition(BOARD_MARKER)
    print(promoted.strip())
    if result.stderr.strip():
        print(result.stderr.strip())  # the remaining GraphQL allowance
    return listing.strip().splitlines()


def summary() -> None:
    prs = json.loads(out(
        "gh", "pr", "list", "--repo", REPO, "--state", "open",
        "--json", "number,title,isDraft,headRefName",
    ))
    print("Open PRs:" if prs else "Open PRs: none")
    for pr in prs:
        draft = " (draft)" if pr["isDraft"] else ""
        print(f"  #{pr['number']}{draft} {pr['title']}  [{pr['headRefName']}]")


def status_board(lines: list[str]) -> None:
    shown, current = [], ""
    for line in lines:
        if not line.startswith(" "):
            current = line.rstrip(":")
        if current in ("Ready", "In progress", "Agent review", "Human review"):
            shown.append(line)
    print("\n".join(shown) if shown else "Board: nothing Ready, In progress or in review")


def cmd(kind: str) -> None:
    root = main_checkout()
    warnings: list[str] = []

    step("Update main checkout")
    warnings += update_main(root)

    step("Dependencies and pre-commit hook")
    ensure_env_and_hook(root)

    step("Tidy worktrees and branches")
    merged_heads = merged_pr_heads()
    warnings += tidy_worktrees(root, merged_heads)
    warnings += tidy_branches(root, merged_heads)

    step("Sync board")
    board_lines = sync_board(root)

    step("Status")
    summary()
    status_board(board_lines)

    if warnings:
        step("Kept back / needs attention")
        for w in warnings:
            print(f"  - {w}")
    if kind == "finish":
        print("\nSession tidy-up complete." if not warnings else
              "\nSession tidy-up complete; items above were left in place on purpose.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["start", "finish", "context"])
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if args.command == "context":
        import context

        context.main(main_checkout())
        return
    cmd(args.command)


if __name__ == "__main__":
    main()
