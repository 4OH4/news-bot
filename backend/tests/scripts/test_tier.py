"""Tests for scripts/tier.py: tiers, the merge gate and the test change report (no network)."""

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
EXTRACT_FILE = "backend/src/newsfeed/extract/a.py"

CLEAN_BODY = """Closes #70 · Tier A

## For human review
- Judgement call: kept the old name as an alias. Recommend merging as is.

## Summary
Fixes the thing.
"""


def classify(*files: Any, needs_human: bool = False) -> Any:
    return tier.classify(list(files), needs_human)


# --- classify --------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "backend/src/newsfeed/extract/sanitize.py",
        "backend/src/newsfeed/rank/score.py",
        "backend/src/newsfeed/adapters/firestore.py",
        "backend/src/newsfeed/cli.py",
        "backend/src/newsfeed/wiring.py",
        "backend/tests/rank/test_score.py",
        "client/src/app.ts",
        "config/extraction.yaml",
        "docs/phases/phase-2.md",
        "eval/results/x.json",
        "README.md",
    ],
)
def test_everything_not_listed_is_tier_a(path: str) -> None:
    assert classify(F(path, 1)).tier == "A"


def test_tier_a_spans_packages_and_needs_no_title_type() -> None:
    result = classify(
        F("backend/src/newsfeed/extract/a.py", 5),
        F("backend/src/newsfeed/rank/b.py", 5),
        F("backend/src/newsfeed/cli.py", 5),
    )
    assert result.tier == "A"
    assert result.blocks == []


@pytest.mark.parametrize(
    "path",
    [
        "backend/src/newsfeed/models.py",
        "backend/src/newsfeed/ports.py",
        "docs/adr/0003-ranking.md",
        "docs/feed.schema.json",
        "docs/feed.example.json",
        "backend/pyproject.toml",
        "backend/uv.lock",
        "client/package.json",
        "client/package-lock.json",
        "config/interests.yaml",
        "config/sources.yaml",
        "eval/urls.yaml",
        "eval/boilerplate.txt",
        "backend/src/newsfeed/eval/scorer.py",
        "backend/tests/conftest.py",
        "backend/tests/fakes.py",
        "backend/tests/test_network_guard.py",
        "backend/tests/contract/test_repository_contract.py",
        "REVIEW.md",
        "PROJECT_PLAN.md",
    ],
)
def test_decide_and_record_paths_are_tier_b(path: str) -> None:
    result = classify(F("docs/x.md", 1), F(path, 1))
    assert result.tier == "B"
    assert result.reasons == [f"touches {path}"]


@pytest.mark.parametrize(
    "path",
    [
        ".claude/settings.json",
        ".claude/agents/worker.md",
        ".github/workflows/ci.yml",
        "scripts/tier.py",
        "backend/tests/scripts/test_tier.py",
        "Makefile",
        ".pre-commit-config.yaml",
        ".gitignore",
        ".dockerignore",
        "backend/Dockerfile",
        "AUTOMATION.md",
        "CLAUDE.md",
        "backend/src/newsfeed/fetching/http.py",
        "docs/milestones/phase-2.md",
        "infra/terraform/iam.tf",
        "infra/terraform/billing.tf",
        "infra/terraform/budget.tf",
    ],
)
def test_reserved_paths_are_tier_c(path: str) -> None:
    result = classify(F(path, 1))
    assert result.tier == "C"
    assert f"touches {path}" in result.reasons


def test_tier_c_outranks_tier_b() -> None:
    assert classify(F("PROJECT_PLAN.md", 1), F("scripts/board.py", 1)).tier == "C"


def test_rename_out_of_a_reserved_path_keeps_its_tier() -> None:
    assert classify(F("docs/old.md", 0, previous_path="scripts/x.py")).tier == "C"
    assert classify(F("docs/old.md", 0, previous_path="docs/adr/0001.md")).tier == "B"


def test_needs_human_is_tier_c_whatever_the_diff() -> None:
    result = classify(F("docs/x.md", 1), needs_human=True)
    assert result.tier == "C"
    assert result.reasons[0] == "needs-human PR or issue"


def test_infra_without_iam_or_billing_is_tier_b() -> None:
    patch = '@@ -1 +1 @@\n-  schedule = "0 * * * *"\n+  schedule = "30 * * * *"'
    result = classify(F("infra/terraform/scheduler.tf", 2, patch=patch))
    assert result.tier == "B"


@pytest.mark.parametrize(
    "line",
    [
        '+resource "google_project_iam_member" "deploy" {',
        '+  role = "roles/run.admin"',
        '-resource "google_billing_budget" "cap" {',
        "+gcloud iam service-accounts create deploy",
        "+  amount = var.budget_gbp",
        '+resource "google_service_account_key" "deploy" {',
        '+resource "google_service_account" "runner" {',
        "+    service_account = var.runner_email",
        '+data "google_iam_policy" "open" {',
        "+  members = var.readers",
        '+  member  = "allUsers"',
        "+  policy_data = data.google_iam_policy.open.policy_data",
        '+  permissions = ["storage.objects.get"]',
        "+  binding {",
        '+  predefined_acl = "publicRead"',
        '+resource "google_storage_bucket_acl" "open" {',
        '+  public_access_prevention = "inherited"',
        "+  uniform_bucket_level_access = false",
    ],
)
def test_iam_or_billing_lines_in_any_infra_file_are_tier_c(line: str) -> None:
    result = classify(F("infra/terraform/deploy.tf", 1, patch=f"@@ -1 +1 @@\n{line}"))
    assert result.tier == "C"
    assert (
        "infra/terraform/deploy.tf changes IAM, access, billing, budgets or service accounts"
        in result.reasons
    )


@pytest.mark.parametrize(
    "patch",
    [
        '@@ -1 +1,4 @@\n+  lifecycle_rule {\n+    action {\n+      type = "Delete"\n+    }',
        '@@ -1 +1 @@\n-      type = "SetStorageClass"\n+      type = "Delete"',
        # Only the age changes: the rule shows in the patch's context lines.
        '@@ -1,3 +1,3 @@\n   lifecycle_rule {\n     action { type = "Delete" }\n'
        "-    condition { age = 90 }\n+    condition { age = 7 }",
        # Only the action shows: `lifecycle_rule` is outside the hunk's context.
        '@@ -8,3 +8,3 @@\n     action {\n-      type = "SetStorageClass"\n'
        '+      type = "Delete"\n     }',
        # The same rule as JSON, for `gsutil lifecycle set`.
        '@@ -1 +1 @@\n-  "action": {"type": "SetStorageClass"}\n+  "action": {"type": "Delete"}',
        # Firestore's rule for deleting documents by age.
        '@@ -1 +1,2 @@\n+  ttl_config {}\n resource "google_firestore_field" "seen" {',
    ],
)
def test_a_delete_rule_is_tier_c(patch: str) -> None:
    result = classify(F("infra/terraform/storage.tf", 2, patch=patch))
    assert result.tier == "C"
    assert result.reasons == [
        "infra/terraform/storage.tf changes a delete rule, which can delete content"
    ]


@pytest.mark.parametrize(
    "patch",
    [
        "@@ -1 +1 @@\n-  force_destroy = false\n+  force_destroy = true",
        "@@ -1 +1,2 @@\n name = var.bucket\n+  force_destroy = true",
        "@@ -1 +1,2 @@\n name = var.bucket\n+  force_destroy = var.allow_destroy",
        "@@ -1 +1 @@\n-  deletion_protection = true\n+  deletion_protection = false",
        '@@ -1,2 +1 @@\n name = var.db\n-  delete_protection_state = "DELETE_PROTECTION_ENABLED"',
        '@@ -1 +1,2 @@\n name = var.db\n+  deletion_policy = "DELETE"',
        "@@ -1,2 +1 @@\n name = var.bucket\n-  lifecycle { prevent_destroy = true }",
        "@@ -1,2 +1 @@\n name = var.bucket\n-  retention_policy { retention_period = 90 }",
        "@@ -1,2 +1 @@\n name = var.bucket\n-  soft_delete_policy { retention = 9 }",
        # Removing a store, whether the file stays or goes.
        '@@ -1,4 +1 @@\n a = 1\n-resource "google_storage_bucket" "articles" {\n-  name = "a"\n-}',
        '@@ -1,2 +0,0 @@\n-resource "google_firestore_database" "main" {\n-}',
        '@@ -1,2 +0,0 @@\n-resource "google_firestore_index" "by_date" {\n-}',
        '@@ -1,2 +0,0 @@\n-resource "google_project" "dev" {\n-}',
        '@@ -1,2 +0,0 @@\n-module "storage" {\n-}',
        # A new label replaces the resource, and these take it away without removing it.
        '@@ -1 +1 @@\n-resource "google_storage_bucket" "a"\n+resource "google_storage_bucket" "b"',
        '@@ -1,2 +1,3 @@\n resource "google_storage_bucket" "a" {\n+  count = 0\n   name = "a"',
        '@@ -1,2 +1,2 @@\n resource "google_firestore_database" "a"\n+  for_each = b',
        "@@ -1 +1,2 @@\n a = 1\n+removed {\n",
        # The same, from a shell script.
        "@@ -1 +1,2 @@\n set -e\n+gsutil -m rm -r gs://news-bot-articles",
        "@@ -1 +1,2 @@\n set -e\n+gcloud storage rm --recursive gs://news-bot-articles",
        "@@ -1 +1,2 @@\n set -e\n+gcloud firestore databases delete --database=main",
        "@@ -1 +1,2 @@\n set -e\n+terraform destroy -auto-approve",
    ],
)
def test_a_change_that_can_destroy_stored_data_is_tier_c(patch: str) -> None:
    result = classify(F("infra/terraform/storage.tf", 2, patch=patch))
    assert result.tier == "C"
    assert result.reasons == ["infra/terraform/storage.tf can destroy stored data or the project"]


@pytest.mark.parametrize(
    "patch",
    [
        "@@ -1 +1,2 @@\n name = var.bucket\n+  force_destroy = false",
        # A protection flag that doesn't change isn't a reason to involve the human.
        "@@ -1,3 +1,3 @@\n  deletion_protection = true\n-  labels = {}\n+  labels = { a = 1 }",
        "@@ -1,3 +1,3 @@\n  force_destroy = true\n-  labels = {}\n+  labels = { a = 1 }",
        # Another bucket setting, a new store, and resources that hold no data.
        '@@ -1,2 +1,2 @@\n resource "google_storage_bucket" "a"\n-  location = "EU"',
        '@@ -0,0 +1,2 @@\n+resource "google_storage_bucket" "articles" {\n+}',
        '@@ -1,2 +0,0 @@\n-resource "google_cloud_run_v2_job" "ingest" {\n-}',
        '@@ -1,2 +0,0 @@\n-resource "google_storage_bucket_object" "seed" {\n-}',
        '@@ -1,2 +1,2 @@\n resource "google_cloud_run_v2_job" "ingest"\n-  count = 1\n+  count = 2',
    ],
)
def test_changes_that_destroy_no_data_stay_tier_b(patch: str) -> None:
    assert classify(F("infra/terraform/storage.tf", 2, patch=patch)).tier == "B"


def test_a_member_change_inside_an_iam_resource_is_tier_c() -> None:
    patch = (
        '@@ -1,4 +1,4 @@\n resource "google_cloud_run_v2_job_iam_member" "deploy_update" {\n'
        '   role     = "roles/run.developer"\n'
        '-  member   = "serviceAccount:${google_service_account.deploy.email}"\n'
        '+  member   = "allUsers"\n }'
    )
    assert classify(F("infra/terraform/deploy.tf", 2, patch=patch)).tier == "C"


def test_a_member_change_with_no_iam_in_its_context_is_tier_c() -> None:
    # The resource line and role are further up than the patch's three lines of context.
    patch = '@@ -9,3 +9,3 @@\n   name = "x"\n-  member = "a"\n+  member = "b"\n }'
    assert classify(F("infra/terraform/deploy.tf", 2, patch=patch)).tier == "C"


def test_any_change_next_to_iam_counts() -> None:
    patch = '@@ -1,2 +1,2 @@\n role = "roles/run.admin"\n-a = 1\n+a = 2'
    assert classify(F("infra/terraform/run_job.tf", 2, patch=patch)).tier == "C"


@pytest.mark.parametrize("word", ["miami", "liam", "diagram", "oracle", "miracle"])
def test_words_containing_iam_or_acl_do_not_count(word: str) -> None:
    patch = f'@@ -1 +1 @@\n-  region = "a"\n+  region = "{word}"'
    assert classify(F("infra/terraform/run_job.tf", 2, patch=patch)).tier == "B"


@pytest.mark.parametrize("path", ["deploy/run.tf", "backend/job.tf.json", "ops/dev.tfvars"])
def test_terraform_outside_infra_gets_the_same_checks(path: str) -> None:
    assert classify(F(path, 1, patch='@@ -1 +1 @@\n-a = 1\n+a = "2"')).tier == "B"
    assert classify(F(path, 1, patch='@@ -1 +1 @@\n+role = "roles/owner"')).tier == "C"


def test_moving_a_file_out_of_infra_keeps_its_checks() -> None:
    moved = F("docs/old.txt", 1, previous_path="infra/terraform/deploy.tf", patch="+roles/owner")
    assert classify(moved).tier == "C"


def test_infra_change_with_no_readable_diff_is_tier_c() -> None:
    assert classify(F("infra/terraform/large.tf", 5000)).tier == "C"


def test_pure_infra_rename_has_no_patch_and_stays_tier_b() -> None:
    assert classify(F("infra/b.tf", 0, previous_path="infra/a.tf")).tier == "B"


def test_empty_diff_is_tier_b() -> None:
    assert tier.classify([], False).reasons == ["no changed files"]


# --- size limit and other blocks ---------------------------------------------------


def test_one_size_limit_applies_to_every_tier() -> None:
    for path in (EXTRACT_FILE, "backend/src/newsfeed/models.py"):
        assert classify(F(path, tier.MAX_SRC_LINES + 1)).blocks == [
            f"{tier.MAX_SRC_LINES + 1} source lines changed (limit 400): split it"
        ]
    assert classify(F("backend/tests/a.py", tier.MAX_TOTAL_LINES + 1)).blocks == [
        f"{tier.MAX_TOTAL_LINES + 1} lines changed in total (limit 1500): split it"
    ]


def test_size_at_the_limit_is_fine() -> None:
    result = classify(
        F(EXTRACT_FILE, tier.MAX_SRC_LINES),
        F("backend/tests/a.py", tier.MAX_TOTAL_LINES - tier.MAX_SRC_LINES),
    )
    assert result.blocks == []


def test_client_source_counts_towards_the_source_limit() -> None:
    assert classify(F("client/src/app.ts", tier.MAX_SRC_LINES + 1)).blocks


def test_generated_files_do_not_count_towards_the_limits() -> None:
    result = classify(
        F("eval/results/2026-10-17.json", 4000),
        F("backend/uv.lock", 3000),
        F("client/package-lock.json", 3000),
    )
    assert result.blocks == []


@pytest.mark.parametrize(
    "path",
    ["eval/urls.yaml", "eval/boilerplate.txt", "backend/src/newsfeed/eval/scorer.py"],
)
def test_evaluation_rules_cannot_change_with_extraction_code(path: str) -> None:
    result = classify(F(EXTRACT_FILE, 1), F(path, 1))
    assert result.blocks == ["changes the evaluation expectations or scorer alongside extract/"]
    assert classify(F(path, 1)).blocks == []


def test_touching_extract_is_noted_for_the_gate() -> None:
    assert classify(F(EXTRACT_FILE, 1)).touches_extract
    assert classify(F("docs/x.py", 1, previous_path=EXTRACT_FILE)).touches_extract
    assert not classify(F("backend/src/newsfeed/rank/a.py", 1)).touches_extract


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
CHECKS_LINE = "Checks done: red confirmed, mutation, test change report, REVIEW.md"


def owned(*bodies: str, association: str = "OWNER") -> list[dict[str, Any]]:
    return [{"body": b, "author_association": association} for b in bodies]


REVIEWED = owned(
    f"## Independent review\nNo defects.\n{CHECKS_LINE}",
    f"**Delivery review**\n**Verdict:** merge. Checked at {HEAD[:7]}.",
)
TIER_A = tier.Classification("A")
TIER_B = tier.Classification("B")
TIER_B_BODY = CLEAN_BODY.replace(
    "Closes #70 · Tier A\n",
    "Closes #70 · Tier B\n**Confirm later:** Weight 0.25. Reason: x. To undo: change y.\n",
)


def test_gate_passes_when_everything_holds() -> None:
    assert tier.gate_failures(pr(), TIER_A, GREEN, REVIEWED) == []


def test_gate_passes_tier_b_with_a_confirm_later_entry() -> None:
    assert tier.gate_failures(pr(body=TIER_B_BODY), TIER_B, GREEN, REVIEWED) == []


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


def test_gate_refuses_tier_c() -> None:
    failures = tier.gate_failures(pr(), tier.Classification("C"), GREEN, REVIEWED)
    assert failures == ["Tier C: the human merges it"]


def test_gate_refuses_anything_classify_blocked() -> None:
    blocked = tier.Classification(
        "A", blocks=["1600 lines changed in total (limit 1500): split it"]
    )
    assert tier.gate_failures(pr(), blocked, GREEN, REVIEWED) == blocked.blocks


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


def test_a_feature_with_only_resolved_defects_can_merge() -> None:
    body = CLEAN_BODY.replace("- Judgement call", "- **Resolved** in 1a2b3c4: **Defect:** x\n- J")
    comments = [*REVIEWED, *owned(f"## Independent review (second)\nClean.\n{CHECKS_LINE}")]
    data = pr(title="feat(rank): x", body=body)
    assert tier.gate_failures(data, TIER_A, GREEN, comments) == []


def test_defect_outside_the_section_is_ignored() -> None:
    body = CLEAN_BODY + "\n## Design notes\n- **Defect:** an example in prose\n"
    assert tier.gate_failures(pr(body=body), TIER_A, GREEN, REVIEWED) == []


def test_reviewer_can_stop_a_pr_with_needs_human() -> None:
    body = CLEAN_BODY.replace("- Judgement call", "- **Needs human:** changes ranking.\n- J")
    assert tier.gate_failures(pr(body=body), TIER_A, GREEN, REVIEWED) == [
        "reviewer marked an item **Needs human:**"
    ]


def test_needs_human_the_lead_decided_and_resolved_does_not_block() -> None:
    body = CLEAN_BODY.replace(
        "- Judgement call",
        "- **Resolved** in 1a2b3c4: **Needs human:** decided, see Confirm later.\n- J",
    )
    assert tier.gate_failures(pr(body=body), TIER_A, GREEN, REVIEWED) == []


DELIVERY_MISSING = f"no 'Delivery review' with 'Verdict: merge' naming the head SHA {HEAD[:7]}"


@pytest.mark.parametrize(
    "delivery",
    [
        "Delivery review: Verdict: merge. Checked at 9999999",  # an older head
        f"Delivery review: Verdict: not ready. Checked at {HEAD[:7]}",
        f"Delivery review: checked {HEAD[:7]}",  # no verdict
        f"Lead review\nVerdict: merge. Checked at {HEAD[:7]}",  # the old heading
        f"Delivery review\nVerdict:\nmerge the other one first. Checked at {HEAD[:7]}",
    ],
)
def test_delivery_review_must_say_merge_at_the_current_head(delivery: str) -> None:
    comments = owned(REVIEWED[0]["body"], delivery)
    assert tier.gate_failures(pr(), TIER_A, GREEN, comments) == [DELIVERY_MISSING]


def test_independent_review_comment_is_required() -> None:
    assert tier.gate_failures(pr(), TIER_A, GREEN, REVIEWED[1:]) == [
        "no 'Independent review' comment from the owner account"
    ]


def test_review_comments_from_other_accounts_do_not_count() -> None:
    comments = [{**c, "author_association": "NONE"} for c in REVIEWED]
    assert tier.gate_failures(pr(), TIER_A, GREEN, comments) == [
        "no 'Independent review' comment from the owner account",
        DELIVERY_MISSING,
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


# --- gate_failures: statement of the review checks ---------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("", "the latest Independent review has no 'Checks done:' line"),
        (
            "Checks done: yes",
            "'Checks done:' doesn't mention red, mutation, test change report, REVIEW.md",
        ),
        (
            "Checks done: red, test change report, REVIEW.md",
            "'Checks done:' doesn't mention mutation",
        ),
        (
            "Checks done: red, mutation, test change report",
            "'Checks done:' doesn't mention REVIEW.md",
        ),
        (
            "Checks done: red, mutation not done, test change report, REVIEW.md",
            "'Checks done:' says a check was not done",
        ),
        (
            "Checks done: red, mutation skipped, test change report, REVIEW.md",
            "'Checks done:' says a check was not done",
        ),
    ],
)
def test_review_must_state_the_checks(line: str, expected: str) -> None:
    comments = owned(f"## Independent review\nFine.\n{line}", REVIEWED[1]["body"])
    assert tier.gate_failures(pr(), TIER_A, GREEN, comments) == [expected]


def test_checks_that_do_not_apply_can_say_so() -> None:
    line = "**Checks done:** red n/a (docs only), mutation n/a, test change report, REVIEW.md"
    comments = owned(f"## Independent review\n{line}", REVIEWED[1]["body"])
    assert tier.gate_failures(pr(), TIER_A, GREEN, comments) == []


def test_checks_must_be_on_the_latest_review() -> None:
    comments = [*REVIEWED, *owned("## Independent review (second)\nFine.")]
    assert tier.gate_failures(pr(), TIER_A, GREEN, comments) == [
        "the latest Independent review has no 'Checks done:' line"
    ]


# --- gate_failures: Confirm later ---------------------------------------------------

NO_CONFIRM = "Tier B, but the header has no **Confirm later:** entry"


@pytest.mark.parametrize(
    "header",
    [
        "Closes #70 · Tier B",
        "Closes #70 · Tier B\n**Confirm later:** none",
        "Closes #70 · Tier B\n**Confirm later:** None.",
        "Closes #70 · Tier B\n**Confirm later:** n/a",
        "Closes #70 · Tier B\n**Confirm later:** N/A.",
        "Closes #70 · Tier B\n**Confirm later:** _(none)_",
        "Closes #70 · Tier B\n**Confirm later:** TBD",
        "Closes #70 · Tier B\n**Confirm later:** -",
        "Closes #70 · Tier B\n**Confirm later:**",
        "Closes #70 · Tier B\n**Confirm later:**   \nThe next line is not an entry",
        "**Confirm later:** n/a · Closes #70 · Tier B",
        "Closes #70 · Tier B\n**Confirm later:** <!-- each decision: what, reason, how to undo -->",
    ],
)
def test_tier_b_needs_a_real_confirm_later_entry(header: str) -> None:
    body = CLEAN_BODY.replace("Closes #70 · Tier A", header)
    assert tier.gate_failures(pr(body=body), TIER_B, GREEN, REVIEWED) == [NO_CONFIRM]


def test_confirm_later_below_the_header_does_not_count() -> None:
    body = CLEAN_BODY + "\n## Notes\n**Confirm later:** an example in prose\n"
    assert tier.gate_failures(pr(body=body), TIER_B, GREEN, REVIEWED) == [NO_CONFIRM]


def test_tier_a_needs_no_confirm_later_entry() -> None:
    assert tier.gate_failures(pr(), TIER_A, GREEN, REVIEWED) == []


# --- gate_failures: evaluation scores ------------------------------------------------

EXTRACT = tier.Classification("A", touches_extract=True)


def scores(before: str = "98.9", after: str = "99.0", extra: str = "") -> str:
    return f"## Evaluation scores\nBefore: {before}%\nAfter: {after}%\n{extra}\nAt {HEAD[:7]}"


def test_extract_pr_with_a_steady_or_better_score_passes() -> None:
    for comment in (scores(), scores(after="98.9")):
        comments = [*REVIEWED, *owned(comment)]
        assert tier.gate_failures(pr(), EXTRACT, GREEN, comments) == []


def test_extract_pr_needs_scores() -> None:
    assert tier.gate_failures(pr(), EXTRACT, GREEN, REVIEWED) == [
        f"touches extract/, but no 'Evaluation scores' comment names the head SHA {HEAD[:7]}"
    ]


def test_scores_must_name_the_head() -> None:
    comments = [*REVIEWED, *owned(scores().replace(HEAD[:7], "9999999"))]
    assert tier.gate_failures(pr(), EXTRACT, GREEN, comments) == [
        f"touches extract/, but no 'Evaluation scores' comment names the head SHA {HEAD[:7]}"
    ]


def test_scores_from_other_accounts_do_not_count() -> None:
    comments = [*REVIEWED, *owned(scores(), association="NONE")]
    assert tier.gate_failures(pr(), EXTRACT, GREEN, comments)


def test_score_drop_blocks_unless_a_reason_is_given() -> None:
    dropped = [*REVIEWED, *owned(scores(after="97.8"))]
    assert tier.gate_failures(pr(), EXTRACT, GREEN, dropped) == [
        "overall score fell from 98.9 to 97.8 with no 'Reason:'"
    ]
    explained = [*REVIEWED, *owned(scores(after="97.8", extra="Reason: a site changed layout"))]
    assert tier.gate_failures(pr(), EXTRACT, GREEN, explained) == []


@pytest.mark.parametrize(
    "reason",
    ["Reason:", "Reason: ", "**Reason:** ", "Reason: n/a", "**Reason:** none."],
)
def test_score_drop_with_an_empty_reason_blocks(reason: str) -> None:
    # The next line must not stand in for the empty field: `scores` puts text after it.
    comments = [*REVIEWED, *owned(scores(after="97", extra=reason))]
    assert tier.gate_failures(pr(), EXTRACT, GREEN, comments) == [
        "overall score fell from 98.9 to 97 with no 'Reason:'"
    ]


def test_a_bold_reason_counts() -> None:
    comments = [*REVIEWED, *owned(scores(after="97", extra="**Reason:** a site changed layout"))]
    assert tier.gate_failures(pr(), EXTRACT, GREEN, comments) == []


def test_a_score_must_be_on_its_own_line() -> None:
    body = f"## Evaluation scores\nBefore:\n98.9\nAfter: 99\nAt {HEAD[:7]}"
    assert tier.gate_failures(pr(), EXTRACT, GREEN, [*REVIEWED, *owned(body)]) == [
        "'Evaluation scores' has no 'Before:' and 'After:' lines"
    ]


def test_latest_scores_at_the_head_win() -> None:
    comments = [*REVIEWED, *owned(scores(), scores(after="90"))]
    assert tier.gate_failures(pr(), EXTRACT, GREEN, comments) == [
        "overall score fell from 98.9 to 90 with no 'Reason:'"
    ]


def test_scores_need_both_numbers() -> None:
    comments = [*REVIEWED, *owned(f"## Evaluation scores\nFine at {HEAD[:7]}")]
    assert tier.gate_failures(pr(), EXTRACT, GREEN, comments) == [
        "'Evaluation scores' has no 'Before:' and 'After:' lines"
    ]


# --- test change report --------------------------------------------------------------


def test_deleted_assertion_is_reported() -> None:
    patch = "@@ -1,3 +1,2 @@\n def test_x():\n-    assert total == 3\n     run()"
    assert tier.assertion_changes("t.py", patch) == ["t.py: deleted: assert total == 3"]


def test_changed_assertion_is_reported() -> None:
    patch = "@@ -1 +1 @@\n-    assert total == 3\n+    assert total >= 1"
    assert tier.assertion_changes("t.py", patch) == ["t.py: changed: assert total == 3"]


def test_new_tests_report_nothing() -> None:
    patch = "@@ -0,0 +1,2 @@\n+def test_x():\n+    assert total == 3"
    assert tier.assertion_changes("t.py", patch) == []


def test_reindented_assertion_reports_nothing() -> None:
    patch = "@@ -1,2 +1,2 @@\n-assert a\n+    assert a"
    assert tier.assertion_changes("t.py", patch) == []


@pytest.mark.parametrize(
    "line",
    [
        "with pytest.raises(ValueError):",
        "fake.assert_called_once_with(1)",
        "def test_old_behaviour() -> None:",
        "async def test_old() -> None:",
    ],
)
def test_other_kinds_of_check_count_as_assertions(line: str) -> None:
    assert tier.assertion_changes("t.py", f"@@ -1 +0,0 @@\n-    {line}")


@pytest.mark.parametrize(
    "line",
    [
        "@pytest.mark.skip",
        '@pytest.mark.xfail(reason="x")',
        "@pytest.mark.live",
        "pytestmark = pytest.mark.live",
        "@mark.live",
        '@unittest.skip("x")',
        'pytest.importorskip("x")',
    ],
)
def test_added_skip_or_live_marker_is_reported(line: str) -> None:
    patch = f"@@ -1 +1,2 @@\n+{line}\n def test_x(): ..."
    assert tier.assertion_changes("t.py", patch) == [f"t.py: skip or live marker added: {line}"]


def test_moved_live_marker_reports_nothing() -> None:
    patch = "@@ -1,2 +1,2 @@\n-@pytest.mark.live\n+  @pytest.mark.live\n def test_x(): ..."
    assert tier.assertion_changes("t.py", patch) == []


@pytest.mark.parametrize(
    "path",
    [
        "client/src/feed.test.ts",
        "client/src/feed.spec.tsx",
        "client/src/__tests__/feed.ts",
        "client/tests/feed.ts",
        "client/test/feed.js",
    ],
)
def test_client_tests_are_scanned(path: str) -> None:
    patch = "@@ -1 +0,0 @@\n-  expect(items).toHaveLength(2);"
    assert tier.diff_findings([F(path, 1, patch=patch)]) == [
        f"{path}: deleted: expect(items).toHaveLength(2);"
    ]


def test_client_source_is_not_scanned_as_tests() -> None:
    patch = "@@ -1 +0,0 @@\n-  expect(items).toHaveLength(2);"
    assert tier.diff_findings([F("client/src/app.ts", 1, patch=patch)]) == []


@pytest.mark.parametrize(
    "line",
    ['it.skip("shows", () => {', 'test.only("shows", () => {', 'describe.skip("x", () => {'],
)
def test_client_skips_are_reported(line: str) -> None:
    patch = f"@@ -1 +1 @@\n+{line}"
    assert tier.assertion_changes("client/a.test.ts", patch) == [
        f"client/a.test.ts: skip or live marker added: {line}"
    ]


def test_deleted_client_test_is_reported() -> None:
    patch = '@@ -1 +0,0 @@\n-it("shows the feed", () => {'
    assert tier.assertion_changes("client/a.test.ts", patch) == [
        'client/a.test.ts: deleted: it("shows the feed", () => {'
    ]


def test_diff_findings_only_read_test_files_and_flag_missing_diffs() -> None:
    assert tier.diff_findings(
        [
            F("backend/src/newsfeed/rank/a.py", 1, patch="@@ -1 +0,0 @@\n-assert x"),
            F("backend/tests/a.py", 1, patch="@@ -1 +0,0 @@\n-assert x"),
            F("backend/tests/big.py", 9000),
            F("backend/tests/renamed.py", 0, previous_path="backend/tests/old.py"),
        ]
    ) == [
        "backend/tests/a.py: deleted: assert x",
        "backend/tests/big.py: no diff available, so check it by hand",
    ]


def commit(sha: str, subject: str, *files: dict[str, Any]) -> dict[str, Any]:
    return {"sha": sha, "commit": {"message": f"{subject}\n\nBody."}, "files": list(files)}


def authored(sha: str, subject: str, *files: dict[str, Any]) -> dict[str, Any]:
    """A commit as the test author makes it, with the trailer among the others."""
    message = f"{subject}\n\nBody.\n\nTest-Author: yes\nCo-Authored-By: An Agent <a@example.com>\n"
    return {"sha": sha, "commit": {"message": message}, "files": list(files)}


def touched(name: str, deletions: int = 0, patch: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"filename": name, "deletions": deletions}
    if patch is not None:
        data["patch"] = patch
    return data


def test_worker_edit_of_a_test_authors_test_is_reported() -> None:
    commits = [
        authored("a" * 40, "test(rank): add failing tests", touched("t.py")),
        commit("b" * 40, "feat(rank): boost", touched("t.py", 1, "@@ -1 +1 @@\n-x = 1\n+x = 2")),
    ]
    assert tier.test_author_edits(commits) == ["bbbbbbb t.py: x = 1"]


def test_additions_and_other_files_are_not_edits() -> None:
    commits = [
        authored("a" * 40, "test: add failing tests", touched("t.py")),
        commit("b" * 40, "feat: x", touched("t.py", 0, "@@ -1 +1,2 @@\n+x = 1")),
        commit("c" * 40, "fix: y", touched("other.py", 1, "@@ -1 +0,0 @@\n-x = 1")),
    ]
    assert tier.test_author_edits(commits) == []


def test_only_leading_test_commits_are_the_test_authors() -> None:
    commits = [
        commit("a" * 40, "feat: x", touched("t.py")),
        commit("b" * 40, "test: more", touched("t.py", 1, "@@ -1 +0,0 @@\n-x = 1")),
    ]
    assert tier.test_author_edits(commits) == []


def test_later_test_commit_editing_their_file_is_reported() -> None:
    commits = [
        authored("a" * 40, "test: add failing tests", touched("t.py")),
        commit("b" * 40, "feat: x"),
        commit("c" * 40, "test: relax it", touched("t.py", 1, "@@ -1 +0,0 @@\n-x = 1")),
    ]
    assert tier.test_author_edits(commits) == ["ccccccc t.py: x = 1"]


def test_a_workers_own_first_test_commit_is_not_the_test_authors() -> None:
    commits = [
        commit("a" * 40, "test: first tests", touched("t.py")),
        commit("b" * 40, "feat: x", touched("t.py", 1, "@@ -1 +0,0 @@\n-x = 1")),
    ]
    assert tier.test_author_edits(commits) == []  # nothing of the test author's to edit
    assert tier.authorship_notes(commits) == [
        "aaaaaaa: a leading test: commit with no Test-Author trailer. Counted as the worker's, "
        "so the report can't tell whether it holds a test author's tests"
    ]


def test_a_workers_test_commit_after_the_test_authors_is_flagged_and_its_edits_count() -> None:
    commits = [
        authored("a" * 40, "test: add", touched("t.py")),
        commit("b" * 40, "test: relax it", touched("t.py", 1, "@@ -1 +0,0 @@\n-x = 1")),
    ]
    assert tier.test_author_edits(commits) == ["bbbbbbb t.py: x = 1"]
    assert [note[:8] for note in tier.authorship_notes(commits)] == ["bbbbbbb:"]


def test_the_trailer_only_counts_on_a_leading_test_commit() -> None:
    commits = [
        authored("a" * 40, "test: add", touched("t.py")),
        commit("b" * 40, "feat: x"),
        authored("c" * 40, "test: relax it", touched("t.py", 1, "@@ -1 +0,0 @@\n-x = 1")),
        authored("d" * 40, "feat: not a test commit"),
    ]
    assert tier.test_author_edits(commits) == ["ccccccc t.py: x = 1"]
    assert [note[:7] for note in tier.authorship_notes(commits)] == ["ccccccc", "ddddddd"]


@pytest.mark.parametrize(
    "message",
    [
        "test: add\n\nTest-Author: yes\nreads like a trailer, but the body goes on\n\nBody.",
        "test: add\n\nBody.\n\nTest-Author:\nCo-Authored-By: An Agent <a@example.com>",
    ],
)
def test_a_trailer_must_be_in_the_last_paragraph_and_have_a_value(message: str) -> None:
    commits = [{"sha": "a" * 40, "commit": {"message": message}, "files": []}]
    assert tier.test_author_commits(commits) == []


def test_marked_test_commits_need_no_note() -> None:
    commits = [
        authored("a" * 40, "test: add", touched("t.py")),
        commit("b" * 40, "feat: x"),
    ]
    assert tier.authorship_notes(commits) == []


def test_edit_with_no_diff_is_flagged() -> None:
    commits = [
        authored("a" * 40, "test: add", touched("t.py")),
        commit("b" * 40, "feat: x", touched("t.py", 9)),
    ]
    assert tier.test_author_edits(commits) == [
        "bbbbbbb t.py: no diff available, so check it by hand"
    ]


def test_tests_command_prints_both_parts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def gh_api(path: str, *extra: str, paginate: bool = False) -> Any:
        if path.startswith("pulls/70/files"):
            return [
                {
                    "filename": "backend/tests/a.py",
                    "additions": 0,
                    "deletions": 1,
                    "patch": "@@ -1 +0,0 @@\n-assert x",
                }
            ]
        if path.startswith("pulls/70/commits"):
            return [{"sha": "a" * 40}, {"sha": "b" * 40}]
        if path == f"commits/{'a' * 40}":
            return authored("a" * 40, "test: add", touched("backend/tests/a.py"))
        return commit(
            "b" * 40, "feat: x", touched("backend/tests/a.py", 1, "@@ -1 +0,0 @@\n-assert x")
        )

    monkeypatch.setattr(tier, "gh_api", gh_api)
    tier.cmd_tests(70)
    out = capsys.readouterr().out
    assert "  - backend/tests/a.py: deleted: assert x" in out
    assert "  - bbbbbbb backend/tests/a.py: assert x" in out
    assert "Check by hand" not in out


def test_tests_command_prints_authorship_notes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(tier, "fetch_files", lambda number: [])
    monkeypatch.setattr(tier, "fetch_commits", lambda number: [commit("a" * 40, "test: add")])
    tier.cmd_tests(70)
    out = capsys.readouterr().out
    assert "Check by hand:\n  - aaaaaaa: a leading test: commit" in out


# --- merge command ---------------------------------------------------------------


def fake_github(
    monkeypatch: pytest.MonkeyPatch,
    pr_data: dict[str, Any],
    comments: list[dict[str, Any]],
    filename: str = "docs/x.md",
) -> list[tuple[str, tuple[str, ...]]]:
    calls: list[tuple[str, tuple[str, ...]]] = []

    def gh_api(path: str, *extra: str, paginate: bool = False) -> Any:
        calls.append((path, extra))
        if path == "pulls/70":
            return pr_data
        if path.startswith("pulls/70/files"):
            return [{"filename": filename, "additions": 3, "deletions": 1}]
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
    assert ("issues/70/labels", ("-X", "POST", "-f", "labels[]=agent-merged")) in calls


def test_merge_merges_tier_b_with_its_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_github(monkeypatch, pr(body=TIER_B_BODY), REVIEWED, filename="PROJECT_PLAN.md")
    tier.cmd_merge(70, dry_run=False)
    assert [p for p, _ in calls if p == "pulls/70/merge"]


def test_merge_dry_run_merges_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_github(monkeypatch, pr(title="docs: x"), REVIEWED)
    tier.cmd_merge(70, dry_run=True)
    assert not [p for p, _ in calls if p.endswith("/merge") or p.endswith("/labels")]


def test_merge_refuses_and_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = fake_github(monkeypatch, pr(title="feat: x"), REVIEWED, filename="scripts/x.py")
    with pytest.raises(SystemExit) as exit_info:
        tier.cmd_merge(70, dry_run=False)
    assert exit_info.value.code == 1
    assert "Tier C: the human merges it" in capsys.readouterr().out
    assert not [p for p, _ in calls if p.endswith("/merge")]


def test_latest_check_run_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    runs = [
        {"name": "test", "conclusion": "failure", "status": "completed", "started_at": "2026-1"},
        {"name": "test", "conclusion": "success", "status": "completed", "started_at": "2026-2"},
        {"name": "lint", "conclusion": None, "status": "in_progress", "started_at": "2026-2"},
    ]
    monkeypatch.setattr(tier, "gh_api", lambda path, *a, **k: {"check_runs": runs})
    assert tier.fetch_checks(HEAD) == {"test": "success", "lint": "in_progress"}


def test_a_closed_needs_human_issue_makes_the_pr_tier_c(monkeypatch: pytest.MonkeyPatch) -> None:
    def gh_api(path: str, *extra: str, paginate: bool = False) -> Any:
        if path.startswith("pulls/80/files"):
            return [{"filename": "docs/x.md", "additions": 9, "deletions": 0}]
        return {"labels": [{"name": "needs-human"}]}

    monkeypatch.setattr(tier, "gh_api", gh_api)
    assert tier.classify_pr(pr(number=80, body="Closes #71")).tier == "C"


def test_patches_reach_the_classifier(monkeypatch: pytest.MonkeyPatch) -> None:
    def gh_api(path: str, *extra: str, paginate: bool = False) -> Any:
        if path.startswith("pulls/80/files"):
            return [
                {
                    "filename": "infra/terraform/deploy.tf",
                    "additions": 1,
                    "deletions": 0,
                    "patch": '@@ -0,0 +1 @@\n+role = "roles/owner"',
                }
            ]
        return {"labels": []}

    monkeypatch.setattr(tier, "gh_api", gh_api)
    result = tier.classify_pr(pr(number=80, body="Closes #71"))
    assert result.reasons == [
        "infra/terraform/deploy.tf changes IAM, access, billing, budgets or service accounts"
    ]
