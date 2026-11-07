"""Tests for `tier.py queue`: each open PR's stage and age, from recorded REST data (no network)."""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from .test_tier import CHECKS_LINE, CLEAN_BODY, tier

OLD = "1111111aaaa"  # an earlier head commit
HEAD_SHA = "abc1234def5678"
RESOLVED_BODY = CLEAN_BODY.replace(
    "- Judgement call",
    f"- **Defect:** missing guard. **Resolved** in {HEAD_SHA[:7]}.\n- Judgement call",
)
OPEN_DEFECT_BODY = CLEAN_BODY.replace(
    "- Judgement call", "- **Defect:** missing guard.\n- Judgement call"
)
TIER_B_BODY = CLEAN_BODY.replace(
    "Closes #70 · Tier A\n",
    "Closes #70 · Tier B\n**Confirm later:** Weight 0.25. Reason: x. To undo: change y.\n",
)
NOW = datetime.now(UTC)


def ago(minutes: float) -> str:
    return (NOW - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def review(sha: str, minutes: float = 30) -> dict[str, Any]:
    return {
        "body": f"## Independent review\nNo defects at {sha[:7]}.\n{CHECKS_LINE}",
        "author_association": "OWNER",
        "created_at": ago(minutes),
    }


def delivery(sha: str, minutes: float = 10) -> dict[str, Any]:
    return {
        "body": f"**Delivery review**\n**Verdict:** merge. Checked at {sha[:7]}.",
        "author_association": "OWNER",
        "created_at": ago(minutes),
    }


def commit(sha: str, minutes: float, message: str = "fix: x") -> dict[str, Any]:
    return {
        "sha": sha,
        "commit": {"message": message, "committer": {"date": ago(minutes)}},
        "files": [],
    }


class Recorded:
    """One open PR's recorded GitHub data."""

    def __init__(
        self,
        number: int = 301,
        *,
        body: str = CLEAN_BODY,
        filename: str = "docs/x.md",
        labels: tuple[str, ...] = (),
        draft: bool = False,
        checks: dict[str, tuple[str | None, str]] | None = None,
        comments: list[dict[str, Any]] | None = None,
        commits: list[dict[str, Any]] | None = None,
    ) -> None:
        self.number = number
        self.filename = filename
        self.checks = checks or {n: ("success", "completed") for n in tier.REQUIRED_CHECKS}
        self.comments = [review(HEAD_SHA)] if comments is None else comments
        self.commits = commits or [commit(HEAD_SHA, 60)]
        self.pr: dict[str, Any] = {
            "number": number,
            "title": "fix: x",
            "state": "open",
            "merged": False,
            "draft": draft,
            "base": {"ref": "main"},
            "head": {"sha": HEAD_SHA, "repo": {"full_name": tier.REPO}},
            "labels": [{"name": n} for n in labels],
            "mergeable_state": "clean",
            "body": body,
            "created_at": ago(120),
            "updated_at": ago(1),
        }


def serve(monkeypatch: pytest.MonkeyPatch, *prs: Recorded) -> list[str]:
    """Answer the REST calls `queue` makes from the recorded data; return the paths asked."""
    asked: list[str] = []
    by_number = {p.number: p for p in prs}

    def gh_api(path: str, *extra: str, paginate: bool = False) -> Any:
        asked.append(path)
        if re.fullmatch(r"pulls(\?.*)?", path):
            return [p.pr for p in prs]
        if found := re.match(r"pulls/(\d+)(?:/(\w+))?", path):
            rec = by_number[int(found.group(1))]
            return {
                None: rec.pr,
                "files": [{"filename": rec.filename, "additions": 3, "deletions": 1}],
                "commits": rec.commits,
            }[found.group(2)]
        if found := re.match(r"issues/(\d+)/comments", path):
            return by_number[int(found.group(1))].comments
        if re.fullmatch(r"issues/\d+", path):
            return {"labels": []}
        if found := re.match(r"commits/(\w+)/check-runs", path):
            return {
                "check_runs": [
                    {"name": n, "conclusion": c, "status": s, "started_at": "t1"}
                    for n, (c, s) in by_number_for_sha(found.group(1)).checks.items()
                ]
            }
        if found := re.match(r"commits/(\w+)", path):
            return next(c for p in prs for c in p.commits if c["sha"] == found.group(1))
        return {}

    def by_number_for_sha(sha: str) -> Recorded:
        return next(p for p in prs if p.pr["head"]["sha"] == sha)

    monkeypatch.setattr(tier, "gh_api", gh_api)
    return asked


def run_queue(capsys: pytest.CaptureFixture[str], *flags: str) -> str:
    sys.argv = ["tier.py", "queue", *flags]
    tier.main()
    return capsys.readouterr().out


def line_for(out: str, number: int) -> str:
    lines = [ln for ln in out.splitlines() if re.search(rf"(?<!\d){number}(?!\d)", ln)]
    assert len(lines) == 1, out
    return lines[0]


def stage_of(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], rec: Recorded
) -> str:
    serve(monkeypatch, rec)
    out = line_for(run_queue(capsys), rec.number)
    stages = [
        s
        for s in (
            "held",
            "ci-failing",
            "ci-running",
            "needs-review",
            "rework",
            "ready-for-delivery-review",
            "ready-to-merge",
            "awaiting-owner",
        )
        if re.search(rf"(?<![\w-]){s}(?![\w-])", out)
    ]
    assert len(stages) == 1, out
    return stages[0]


# --- one case per stage ------------------------------------------------------------


def test_hold_label_is_held_whatever_else_is_true(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rec = Recorded(labels=("hold",), checks={"lint": ("failure", "completed")})
    assert stage_of(monkeypatch, capsys, rec) == "held"


def test_failed_required_check_is_ci_failing_whatever_the_reviews_say(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checks = {"lint": ("success", "completed"), "test": ("failure", "completed")}
    rec = Recorded(checks=checks, comments=[review(HEAD_SHA), delivery(HEAD_SHA)])
    assert stage_of(monkeypatch, capsys, rec) == "ci-failing"


def test_pending_required_checks_are_ci_running(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checks = {n: ("success", "completed") for n in ("lint", "docker")}
    checks["test"] = (None, "in_progress")
    assert stage_of(monkeypatch, capsys, Recorded(checks=checks)) == "ci-running"


def test_no_review_is_needs_review(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert stage_of(monkeypatch, capsys, Recorded(comments=[])) == "needs-review"


def test_review_of_an_older_commit_is_needs_review(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rec = Recorded(
        comments=[review(OLD, 50)], commits=[commit(OLD, 60), commit(HEAD_SHA, 20, "feat: more")]
    )
    assert stage_of(monkeypatch, capsys, rec) == "needs-review"


def test_review_of_an_older_commit_covers_head_when_later_commits_are_resolved_rework(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rec = Recorded(
        body=RESOLVED_BODY,
        comments=[review(OLD, 50)],
        commits=[commit(OLD, 60), commit(HEAD_SHA, 20, "fix: address review")],
    )
    assert stage_of(monkeypatch, capsys, rec) == "ready-for-delivery-review"


def test_unresolved_defect_is_rework(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert stage_of(monkeypatch, capsys, Recorded(body=OPEN_DEFECT_BODY)) == "rework"


def test_unresolved_needs_human_is_rework(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    body = CLEAN_BODY.replace("- Judgement call", "- **Needs human:** pick one.\n- Judgement call")
    assert stage_of(monkeypatch, capsys, Recorded(body=body)) == "rework"


def test_clean_review_green_ci_and_no_delivery_review_is_ready_for_delivery_review(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert stage_of(monkeypatch, capsys, Recorded()) == "ready-for-delivery-review"


def test_delivery_review_naming_an_older_sha_is_still_ready_for_delivery_review(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rec = Recorded(comments=[review(HEAD_SHA), delivery(OLD)])
    assert stage_of(monkeypatch, capsys, rec) == "ready-for-delivery-review"


def test_pr_the_merge_gate_accepts_is_ready_to_merge(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rec = Recorded(comments=[review(HEAD_SHA), delivery(HEAD_SHA)])
    assert stage_of(monkeypatch, capsys, rec) == "ready-to-merge"


def test_tier_b_without_confirm_later_is_not_ready_to_merge(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rec = Recorded(filename="PROJECT_PLAN.md", comments=[review(HEAD_SHA), delivery(HEAD_SHA)])
    assert stage_of(monkeypatch, capsys, rec) != "ready-to-merge"


def test_tier_b_with_confirm_later_is_ready_to_merge(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rec = Recorded(
        body=TIER_B_BODY,
        filename="PROJECT_PLAN.md",
        comments=[review(HEAD_SHA), delivery(HEAD_SHA)],
    )
    assert stage_of(monkeypatch, capsys, rec) == "ready-to-merge"


def test_tier_c_with_a_valid_delivery_review_is_awaiting_owner(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rec = Recorded(filename="scripts/x.py", comments=[review(HEAD_SHA), delivery(HEAD_SHA)])
    assert stage_of(monkeypatch, capsys, rec) == "awaiting-owner"


def test_tier_c_draft_with_a_clean_review_is_ready_for_delivery_review(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rec = Recorded(filename="scripts/x.py", draft=True)
    assert stage_of(monkeypatch, capsys, rec) == "ready-for-delivery-review"


# --- listing, age and output ---------------------------------------------------------


def test_line_names_the_issue_the_pr_closes_and_its_tier(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    serve(monkeypatch, Recorded(301))
    line = line_for(run_queue(capsys), 301)
    assert "#70" in line or re.search(r"(?<!\d)70(?!\d)", line)
    assert re.search(r"(?<![\w])A(?![\w])", line)


def test_age_is_time_since_the_review_for_ready_for_delivery_review(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rec = Recorded(comments=[review(HEAD_SHA, 25)], commits=[commit(HEAD_SHA, 90)])
    serve(monkeypatch, rec)
    assert re.search(r"\b2[45]\s*(m\b|min)", line_for(run_queue(capsys), 301))


def test_age_runs_from_the_head_commit_when_it_is_newer_than_the_review(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rec = Recorded(comments=[review(HEAD_SHA, 50)], commits=[commit(HEAD_SHA, 12)])
    serve(monkeypatch, rec)
    assert re.search(r"\b1[123]\s*(m\b|min)", line_for(run_queue(capsys), 301))


def test_oldest_stage_is_listed_first(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    newer = Recorded(301, comments=[review(HEAD_SHA, 5)])
    older = Recorded(302, comments=[review(HEAD_SHA, 40)])
    serve(monkeypatch, newer, older)
    out = run_queue(capsys)
    assert out.index("302") < out.index("301")


def test_json_flag_prints_one_record_per_open_pr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    serve(monkeypatch, Recorded(301), Recorded(302, comments=[]))
    records = json.loads(run_queue(capsys, "--json"))
    stages = {r["pr"]: r["stage"] for r in records}
    assert stages == {301: "ready-for-delivery-review", 302: "needs-review"}


def test_queue_is_read_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []
    rec = Recorded(comments=[review(HEAD_SHA), delivery(HEAD_SHA)])
    serve(monkeypatch, rec)
    inner = tier.gh_api

    def spy(path: str, *extra: str, paginate: bool = False) -> Any:
        calls.append((path, extra))
        return inner(path, *extra, paginate=paginate)

    monkeypatch.setattr(tier, "gh_api", spy)
    run_queue(capsys)
    assert not [c for c in calls if "-X" in c[1]]


# --- real reviews name no SHA; conflicts ----------------------------------------------


def unnamed_review(minutes: float) -> dict[str, Any]:
    """A review as the reviewer writes it: no commit SHA anywhere."""
    return {
        "body": f"## Independent review\nNo defects.\n{CHECKS_LINE}",
        "author_association": "OWNER",
        "created_at": ago(minutes),
    }


def test_review_naming_no_sha_posted_after_the_head_commit_covers_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rec = Recorded(comments=[unnamed_review(30)], commits=[commit(HEAD_SHA, 60)])
    assert stage_of(monkeypatch, capsys, rec) == "ready-for-delivery-review"


def test_review_naming_no_sha_posted_before_the_head_commit_is_needs_review(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rec = Recorded(comments=[unnamed_review(60)], commits=[commit(HEAD_SHA, 20)])
    assert stage_of(monkeypatch, capsys, rec) == "needs-review"


def test_conflicting_pr_is_rework(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rec = Recorded(checks={})
    rec.pr["mergeable_state"] = "dirty"
    assert stage_of(monkeypatch, capsys, rec) == "rework"
