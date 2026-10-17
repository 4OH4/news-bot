"""Tests for scripts/tier.py: tier classification and the lead's merge gate (no network)."""

from __future__ import annotations

import importlib.util
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


tier = load("tier")
F = tier.ChangedFile
HEAD = "abc1234def5678"

CLEAN_BODY = """Closes #70 · Tier A

## For human review
- Judgement call: kept the old name as an alias. Recommend merging as is.

## Summary
Fixes the thing.
"""


def classify(title: str, *files: Any, needs_human: bool = False) -> Any:
    return tier.classify(title, list(files), needs_human)


# --- classify --------------------------------------------------------------------


def test_small_fix_in_one_stage_package_is_tier_a() -> None:
    result = classify(
        "fix(extract): strip zero-width characters",
        F("backend/src/newsfeed/extract/sanitize.py", 12),
        F("backend/tests/extract/test_sanitize.py", 40),
    )
    assert result.tier == "A"


@pytest.mark.parametrize(
    "title",
    ["docs: record blocked publishers", "test: cover Atom dates", "chore(eval): add results"],
)
def test_docs_tests_and_results_only_are_tier_a(title: str) -> None:
    result = classify(
        title,
        F("docs/blocked-publishers.md", 70),
        F("backend/tests/ingest/test_rss.py", 30),
        F("eval/results/x.json", 5),
    )
    assert result.tier == "A"


@pytest.mark.parametrize(
    "path",
    [
        "backend/src/newsfeed/models.py",
        "backend/src/newsfeed/ports.py",
        "backend/src/newsfeed/fetching/http.py",
        "docs/adr/0003-ranking.md",
        "eval/urls.yaml",
        "config/sources.yaml",
        "config/interests.yaml",
        "infra/main.tf",
        ".github/workflows/ci.yml",
        ".claude/settings.json",
        "scripts/tier.py",
    ],
)
def test_reserved_paths_are_tier_c(path: str) -> None:
    result = classify("fix: tweak", F(path, 1))
    assert result.tier == "C"
    assert f"touches {path}" in result.reasons


def test_rename_out_of_a_reserved_path_is_tier_c() -> None:
    result = classify("refactor: move", F("docs/old.md", 0, previous_path="docs/adr/0001.md"))
    assert result.tier == "C"


def test_needs_human_is_tier_c_whatever_the_diff() -> None:
    result = classify("docs: typo", F("docs/x.md", 1), needs_human=True)
    assert result.tier == "C"
    assert result.reasons[0] == "needs-human PR or issue"


def test_feat_is_tier_b() -> None:
    result = classify("feat(rank): boost", F("backend/src/newsfeed/rank/score.py", 10))
    assert result.tier == "B"
    assert "title type 'feat'" in result.reasons[0]


@pytest.mark.parametrize(
    "path",
    [
        "backend/src/newsfeed/cli.py",
        "backend/src/newsfeed/adapters/firestore.py",
        "backend/src/newsfeed/eval/scorer.py",
        "backend/tests/conftest.py",
        "backend/tests/fakes.py",
        "backend/tests/test_network_guard.py",
        "backend/tests/contract/test_repository_contract.py",
        "backend/pyproject.toml",
        "backend/uv.lock",
        "backend/Dockerfile",
        "CLAUDE.md",
        "PROJECT_PLAN.md",
        "config/extraction.yaml",
    ],
)
def test_paths_outside_tier_a_make_it_tier_b(path: str) -> None:
    assert classify("fix: tweak", F(path, 1)).tier == "B"


def test_source_in_two_packages_is_tier_b() -> None:
    result = classify(
        "fix: both",
        F("backend/src/newsfeed/extract/a.py", 5),
        F("backend/src/newsfeed/ingest/b.py", 5),
    )
    assert result.tier == "B"
    assert "source spans 2 packages: extract, ingest" in result.reasons


def test_size_limits() -> None:
    src = classify("fix: big", F("backend/src/newsfeed/extract/a.py", tier.MAX_SRC_LINES + 1))
    assert src.tier == "B"
    total = classify("test: big", F("backend/tests/a.py", tier.MAX_TOTAL_LINES + 1))
    assert total.tier == "B"
    at_limit = classify(
        "fix: ok",
        F("backend/src/newsfeed/extract/a.py", tier.MAX_SRC_LINES),
        F("backend/tests/a.py", tier.MAX_TOTAL_LINES - tier.MAX_SRC_LINES),
    )
    assert at_limit.tier == "A"


def test_breaking_change_title_is_tier_b() -> None:
    result = classify(
        "fix(extract)!: drop the old field", F("backend/src/newsfeed/extract/a.py", 3)
    )
    assert result.reasons == ["title marks a breaking change ('!')"]


def test_untyped_title_is_tier_b() -> None:
    assert classify("Update docs", F("docs/x.md", 1)).tier == "B"


# --- gate_failures ---------------------------------------------------------------


def pr(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "number": 70,
        "title": "fix: x",
        "state": "open",
        "merged": False,
        "draft": False,
        "base": {"ref": "main"},
        "head": {"sha": HEAD, "repo": {"full_name": tier.REPO}},
        "labels": [],
        "mergeable_state": "clean",
        "body": CLEAN_BODY,
    }
    data.update(overrides)
    return data


GREEN = {"lint": "success", "test": "success", "docker": "success"}


def owned(*bodies: str, association: str = "OWNER") -> list[dict[str, Any]]:
    return [{"body": b, "author_association": association} for b in bodies]


REVIEWED = owned(
    "## Independent review\nNo defects.",
    f"**Lead review**\n**Verdict:** merge (Tier A). Checked at {HEAD[:7]}.",
)
TIER_A = tier.Classification("A")


def test_gate_passes_when_everything_holds() -> None:
    assert tier.gate_failures(pr(), TIER_A, GREEN, REVIEWED) == []


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"draft": True}, "PR is a draft"),
        ({"state": "closed"}, "PR is not open"),
        ({"base": {"ref": "issue-1-x"}}, "base is issue-1-x, not main"),
        ({"labels": [{"name": "hold"}]}, "'hold' label set"),
        ({"mergeable_state": "blocked"}, "mergeable_state is 'blocked', not 'clean'"),
        ({"body": "## For human review\nNone\n"}, "body has no 'Closes #N'"),
        ({"body": "Closes #70\n"}, "body has no '## For human review' section"),
        (
            {"body": "Closes #70\n## For human review\nPending independent review\n"},
            "'For human review' is still pending",
        ),
    ],
)
def test_gate_refuses(overrides: dict[str, Any], expected: str) -> None:
    assert expected in tier.gate_failures(pr(**overrides), TIER_A, GREEN, REVIEWED)


def test_gate_refuses_other_tiers() -> None:
    failures = tier.gate_failures(pr(), tier.Classification("B"), GREEN, REVIEWED)
    assert failures == ["Tier B, not A"]


def test_gate_needs_every_required_check_green() -> None:
    failures = tier.gate_failures(pr(), TIER_A, {"lint": "success", "test": "failure"}, REVIEWED)
    assert failures == ["check 'test' is failure", "check 'docker' is missing"]


def test_unresolved_defect_blocks_but_resolved_one_does_not() -> None:
    body = CLEAN_BODY.replace(
        "- Judgement call",
        "- **Defect:** crashes on empty input.\n"
        "- **Resolved** in 1a2b3c4: **Defect:** wrong log field.\n- Judgement call",
    )
    assert tier.gate_failures(pr(body=body), TIER_A, GREEN, REVIEWED) == [
        "1 unresolved **Defect:** item(s)"
    ]


def test_defect_outside_the_section_is_ignored() -> None:
    body = CLEAN_BODY + "\n## Design notes\n- **Defect:** an example in prose\n"
    assert tier.gate_failures(pr(body=body), TIER_A, GREEN, REVIEWED) == []


def test_reviewer_can_send_a_tier_a_pr_to_the_human() -> None:
    body = CLEAN_BODY.replace("- Judgement call", "- **Needs human:** changes ranking.\n- J")
    assert tier.gate_failures(pr(body=body), TIER_A, GREEN, REVIEWED) == [
        "reviewer marked an item **Needs human:**"
    ]


LEAD_MISSING = f"no 'Lead review' with 'Verdict: merge' naming the head SHA {HEAD[:7]}"


@pytest.mark.parametrize(
    "lead",
    [
        "Lead review: Verdict: merge. Checked at 9999999",  # an older head
        f"Lead review: Verdict: not ready. Checked at {HEAD[:7]}",
        f"Lead review: checked {HEAD[:7]}",  # no verdict
    ],
)
def test_lead_review_must_say_merge_at_the_current_head(lead: str) -> None:
    comments = owned("Independent review: fine", lead)
    assert tier.gate_failures(pr(), TIER_A, GREEN, comments) == [LEAD_MISSING]


def test_independent_review_comment_is_required() -> None:
    assert tier.gate_failures(pr(), TIER_A, GREEN, REVIEWED[1:]) == [
        "no 'Independent review' comment from the owner account"
    ]


def test_review_comments_from_other_accounts_do_not_count() -> None:
    comments = [{**c, "author_association": "NONE"} for c in REVIEWED]
    assert tier.gate_failures(pr(), TIER_A, GREEN, comments) == [
        "no 'Independent review' comment from the owner account",
        LEAD_MISSING,
    ]


def test_fork_prs_are_refused() -> None:
    head = {"sha": HEAD, "repo": {"full_name": "someone/news-bot"}}
    assert tier.gate_failures(pr(head=head), TIER_A, GREEN, REVIEWED) == [
        "head branch is not in this repository"
    ]


@pytest.mark.parametrize(
    "item",
    [
        "- **Defect**: colon outside the bold",
        "- **defect:** lower case",
        "- **Defect:** the cap is not **Resolved** by this change",  # no "in <sha>"
    ],
)
def test_defect_marker_variants_block(item: str) -> None:
    body = CLEAN_BODY.replace("- Judgement call", f"{item}\n- Judgement call")
    assert tier.gate_failures(pr(body=body), TIER_A, GREEN, REVIEWED) == [
        "1 unresolved **Defect:** item(s)"
    ]


@pytest.mark.parametrize("item", ["- **Needs human**: wording", "- **needs  human:** x"])
def test_needs_human_marker_variants_block(item: str) -> None:
    body = CLEAN_BODY.replace("- Judgement call", f"{item}\n- Judgement call")
    assert tier.gate_failures(pr(body=body), TIER_A, GREEN, REVIEWED) == [
        "reviewer marked an item **Needs human:**"
    ]


def test_a_second_human_review_section_is_refused() -> None:
    body = CLEAN_BODY + "\n## For human review\n- **Defect:** hidden down here\n"
    assert "body has more than one 'For human review' section" in tier.gate_failures(
        pr(body=body), TIER_A, GREEN, REVIEWED
    )


# --- merge command ---------------------------------------------------------------


def fake_github(
    monkeypatch: pytest.MonkeyPatch, pr_data: dict[str, Any], comments: list[dict[str, Any]]
) -> list[tuple[str, tuple[str, ...]]]:
    calls: list[tuple[str, tuple[str, ...]]] = []

    def gh_api(path: str, *extra: str, paginate: bool = False) -> Any:
        calls.append((path, extra))
        if path == "pulls/70":
            return pr_data
        if path.startswith("pulls/70/files"):
            return [{"filename": "docs/x.md", "additions": 3, "deletions": 1}]
        if path == "issues/70":
            return {"labels": []}
        if path.startswith(f"commits/{HEAD}/check-runs"):
            return {
                "check_runs": [
                    {"name": n, "conclusion": "success", "status": "completed", "started_at": "t1"}
                    for n in tier.REQUIRED_CHECKS
                ]
            }
        if path.startswith("issues/70/comments"):
            return comments
        return {}

    monkeypatch.setattr(tier, "gh_api", gh_api)
    return calls


def test_merge_pins_the_checked_head_sha_and_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_github(monkeypatch, pr(title="docs: x"), REVIEWED)
    tier.cmd_merge(70, dry_run=False)
    merge = [extra for path, extra in calls if path == "pulls/70/merge"]
    assert merge == [("-X", "PUT", "-f", "merge_method=merge", "-f", f"sha={HEAD}")]
    assert ("issues/70/labels", ("-X", "POST", "-f", "labels[]=lead-merged")) in calls


def test_merge_dry_run_merges_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_github(monkeypatch, pr(title="docs: x"), REVIEWED)
    tier.cmd_merge(70, dry_run=True)
    assert not [p for p, _ in calls if p.endswith("/merge") or p.endswith("/labels")]


def test_merge_refuses_and_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = fake_github(monkeypatch, pr(title="feat: x"), REVIEWED)
    with pytest.raises(SystemExit) as exit_info:
        tier.cmd_merge(70, dry_run=False)
    assert exit_info.value.code == 1
    assert "Tier B, not A" in capsys.readouterr().out
    assert not [p for p, _ in calls if p.endswith("/merge")]


def test_latest_check_run_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    runs = [
        {"name": "test", "conclusion": "failure", "status": "completed", "started_at": "2026-1"},
        {"name": "test", "conclusion": "success", "status": "completed", "started_at": "2026-2"},
        {"name": "lint", "conclusion": None, "status": "in_progress", "started_at": "2026-2"},
    ]
    monkeypatch.setattr(tier, "gh_api", lambda path, *a, **k: {"check_runs": runs})
    assert tier.fetch_checks(HEAD) == {"test": "success", "lint": "in_progress"}
