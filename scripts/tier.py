#!/usr/bin/env python3
"""Classify a PR into a risk tier, and merge Tier A PRs for the lead.

  python3 scripts/tier.py classify 70            # print the tier and why
  python3 scripts/tier.py classify 70 --label    # ...and set the tier:A/B/C label
  python3 scripts/tier.py merge 70 --dry-run     # check the merge gate only
  python3 scripts/tier.py merge 70               # check the gate, then merge

Tiers (see CLAUDE.md, "Risk tiers"):

* Tier C: full human review. Any change to a contract, an ADR, the evaluation
  expectations, curated sources or interests, infrastructure or the automation
  itself (.github/, .claude/, scripts/), or a needs-human PR/issue.
* Tier A: the lead merges. A change confined to docs, tests, eval results and at
  most one stage package, below the size limits. A `feat` qualifies only for small
  agent-ready issues, and the gate then also needs the worker's "Deviations: none",
  the reviewer's "Tier A feature: confirmed" and a review that found nothing.
* Tier B: everything else. The human merges.

The tier comes from the diff, not from anyone's judgement, and this script is in
Tier C itself, so agents can't relax the rules without the human merging that.

`merge` is the only way an agent may merge a PR. That is a rule in CLAUDE.md, not a
technical barrier: `gh pr merge` is denied in .claude/settings.json, but the REST merge
endpoint is reachable through the allowed `gh api` prefix. It refuses unless every condition in
`gate_failures` holds, and pins the merge to the head SHA it checked.

Uses the REST API only (separate allowance from GraphQL; see CLAUDE.md).
Requirements: Python 3.10+ (standard library only), gh.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any

REPO = "4OH4/news-bot"
TIMEOUT = 120
REQUIRED_CHECKS = ("lint", "test", "docker")  # must match the "Protect main" ruleset
TIER_LABELS = ("tier:A", "tier:B", "tier:C")
HOLD_LABEL = "hold"
MERGED_LABEL = "lead-merged"
NEEDS_HUMAN_LABEL = "needs-human"

# The decisions PROJECT_PLAN.md §9.5 keeps for the human, plus the automation
# itself (so agents can't loosen their own rules). Everything else that isn't
# Tier A, such as dependencies or CLAUDE.md, is Tier B: still merged by the human.
TIER_C_PATHS = (
    "backend/src/newsfeed/models.py",
    "backend/src/newsfeed/ports.py",
    "backend/src/newsfeed/fetching/*",  # polite fetching, CLAUDE.md architecture rule 6
    "docs/adr/*",
    "eval/urls.yaml",
    "config/sources.yaml",
    "config/interests.yaml",
    "infra/*",
    ".github/*",
    ".claude/*",
    "scripts/*",
)
TIER_A_PATHS = ("docs/*", "README*", "backend/tests/*", "eval/results/*")
# Test infrastructure that keeps every other test honest (the network guard, the
# fakes, the contract suites): changing it is Tier B, not a routine test change.
TEST_GUARD_PATHS = (
    "backend/tests/conftest.py",
    "backend/tests/fakes.py",
    "backend/tests/test_network_guard.py",
    "backend/tests/contract/*",
)
SRC_PREFIX = "backend/src/newsfeed/"
# Not `eval`: the scorer decides the evaluation results, which the human judges.
TIER_A_PACKAGES = ("ingest", "extract", "rank", "publish", "preview", "api")
TIER_A_TYPES = ("fix", "test", "docs", "refactor", "perf", "chore")
MAX_SRC_LINES = 150
MAX_TOTAL_LINES = 600
# Generated metrics, often thousands of lines: they don't count towards the limits.
GENERATED_PATHS = ("eval/results/*",)

# Small features (#70): only for agent-ready S/M issues, and a new subcommand may
# also touch cli.py. The gate adds attestations that the work went as planned.
FEATURE_TYPE = "feat"
FEATURE_SIZES = ("size:S", "size:M")
FEATURE_EXTRA_FILES = ("backend/src/newsfeed/cli.py",)
MAX_FEATURE_SRC_LINES = 400
MAX_FEATURE_TOTAL_LINES = 1500
AGENT_READY_LABEL = "agent-ready"

CLOSES_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.IGNORECASE)
TYPE_RE = re.compile(r"^(\w+)(?:\([^)]*\))?(!?):")
SECTION_RE = re.compile(r"^##\s+For human review\s*$(.*?)(?=^##\s|\Z)", re.MULTILINE | re.DOTALL)
SECTION_HEADING_RE = re.compile(r"^#+\s+For human review", re.MULTILINE | re.IGNORECASE)


def _marker(word: str) -> re.Pattern[str]:
    """`**Word:**` or `**Word**:`, any case, as reviewers may type either."""
    return re.compile(rf"\*\*\s*{word}\s*(?::\s*\*\*|\*\*\s*:)", re.IGNORECASE)


DEFECT_RE = _marker("defect")
NEEDS_HUMAN_RE = _marker("needs\\s+human")
DEVIATIONS_NONE_RE = re.compile(
    r"\*\*\s*Deviations\s*(?::\s*\*\*|\*\*\s*:)\s*none\b", re.IGNORECASE
)
FEATURE_CONFIRMED_RE = re.compile(r"\bTier A feature:\s*confirmed\b", re.IGNORECASE)
RESOLVED_RE = re.compile(r"\*\*\s*Resolved\s*\*\*\s+in\s+[0-9a-f]{7,40}\b", re.IGNORECASE)
VERDICT_RE = re.compile(r"^\W*verdict\W*\s*merge\b", re.IGNORECASE | re.MULTILINE)


@dataclass
class ChangedFile:
    path: str
    changes: int  # additions + deletions
    previous_path: str | None = None


@dataclass
class Classification:
    tier: str
    reasons: list[str] = field(default_factory=list)
    feature: bool = False  # a Tier A `feat`: the gate needs the feature attestations


def matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(path, p) for p in patterns)


def src_package(path: str) -> str | None:
    """The stage package a source file belongs to, or None for top-level modules."""
    rest = path.removeprefix(SRC_PREFIX)
    return rest.split("/", 1)[0] if "/" in rest else None


def classify(
    title: str, files: list[ChangedFile], needs_human: bool, feature_ready: bool = False
) -> Classification:
    """Work out a PR's tier from its title, changed files and linked issues.

    `feature_ready` means the PR closes at least one issue, and every issue it
    closes is agent-ready and sized S or M.
    """
    # A rename touches both paths, so check the old one too.
    paths = [p for f in files for p in (f.path, f.previous_path) if p]
    c_hits = sorted({p for p in paths if matches(p, TIER_C_PATHS)})
    c_reasons = [f"touches {p}" for p in c_hits]
    if needs_human:
        c_reasons.insert(0, "needs-human PR or issue")
    if c_reasons:
        return Classification("C", c_reasons)

    b_reasons: list[str] = []
    found = TYPE_RE.match(title)
    kind = found.group(1).lower() if found else ""
    feature = kind == FEATURE_TYPE
    if feature and not feature_ready:
        b_reasons.append("feat, but not every closed issue is agent-ready and size:S or size:M")
    elif not feature and kind not in TIER_A_TYPES:
        types = ", ".join((*TIER_A_TYPES, FEATURE_TYPE))
        b_reasons.append(f"title type '{kind or '?'}' is not one of {types}")
    if found and found.group(2):
        b_reasons.append("title marks a breaking change ('!')")

    packages: set[str] = set()
    src_lines = 0
    for f in files:
        for p in (f.path, f.previous_path):
            if not p:
                continue
            if matches(p, TEST_GUARD_PATHS):
                b_reasons.append(f"{p} is test infrastructure")
                continue
            if matches(p, TIER_A_PATHS) or (feature and p in FEATURE_EXTRA_FILES):
                continue
            pkg = src_package(p) if p.startswith(SRC_PREFIX) else None
            if pkg in TIER_A_PACKAGES:
                packages.add(pkg)
            else:
                b_reasons.append(f"{p} is outside docs, tests, eval results and stage packages")
        if f.path.startswith(SRC_PREFIX):
            src_lines += f.changes
    if len(packages) > 1:
        b_reasons.append(f"source spans {len(packages)} packages: {', '.join(sorted(packages))}")
    max_src = MAX_FEATURE_SRC_LINES if feature else MAX_SRC_LINES
    max_total = MAX_FEATURE_TOTAL_LINES if feature else MAX_TOTAL_LINES
    if src_lines > max_src:
        b_reasons.append(f"{src_lines} source lines changed (limit {max_src})")
    total = sum(f.changes for f in files if not matches(f.path, GENERATED_PATHS))
    if total > max_total:
        b_reasons.append(f"{total} lines changed in total (limit {max_total})")
    if not files:
        b_reasons.append("no changed files")

    if b_reasons:
        return Classification("B", b_reasons)
    reasons = [f"{kind} change, {total} lines, within Tier A paths"]
    if feature:
        reasons.append(
            "feature: the merge also needs Deviations: none, the reviewer's "
            "confirmation and a review that found nothing"
        )
    return Classification("A", reasons, feature)


def human_review_section(body: str) -> str | None:
    found = SECTION_RE.search(body)
    return found.group(1) if found else None


def unresolved_defects(section: str) -> list[str]:
    """Defect items without a `**Resolved** in <sha>` marker."""
    return [
        ln for ln in section.splitlines() if DEFECT_RE.search(ln) and not RESOLVED_RE.search(ln)
    ]


def heading(comment: str) -> str:
    """A comment's first line without Markdown heading or bold markers."""
    first = comment.strip().split("\n", 1)[0]
    return first.lstrip("#* ").strip()


def gate_failures(
    pr: dict[str, Any],
    classification: Classification,
    checks: dict[str, str],
    comments: list[dict[str, Any]],
) -> list[str]:
    """Every reason the lead may not merge this PR. Empty means it may.

    `comments` are REST issue comments. Only the owner's count: agents post as the
    owner, and anyone can comment on a public repo.
    """
    failures: list[str] = []
    head = pr["head"]["sha"]
    labels = {label["name"] for label in pr["labels"]}

    if classification.tier != "A":
        failures.append(f"Tier {classification.tier}, not A")
    if pr["state"] != "open" or pr.get("merged"):
        failures.append("PR is not open")
    if pr.get("draft"):
        failures.append("PR is a draft")
    if (pr["head"].get("repo") or {}).get("full_name") != REPO:
        failures.append("head branch is not in this repository")
    if pr["base"]["ref"] != "main":
        failures.append(f"base is {pr['base']['ref']}, not main")
    if HOLD_LABEL in labels:
        failures.append(f"'{HOLD_LABEL}' label set")
    if pr.get("mergeable_state") != "clean":
        failures.append(f"mergeable_state is {pr.get('mergeable_state')!r}, not 'clean'")

    for name in REQUIRED_CHECKS:
        if checks.get(name) != "success":
            failures.append(f"check '{name}' is {checks.get(name, 'missing')}")

    body = pr.get("body") or ""
    if not CLOSES_RE.search(body):
        failures.append("body has no 'Closes #N'")
    section = human_review_section(body)
    if len(SECTION_HEADING_RE.findall(body)) > 1:
        failures.append("body has more than one 'For human review' section")
    if section is None:
        failures.append("body has no '## For human review' section")
    else:
        if "pending independent review" in section.lower():
            failures.append("'For human review' is still pending")
        if NEEDS_HUMAN_RE.search(section):
            failures.append("reviewer marked an item **Needs human:**")
        open_defects = unresolved_defects(section)
        if open_defects:
            failures.append(f"{len(open_defects)} unresolved **Defect:** item(s)")

    owned = [c.get("body") or "" for c in comments if c.get("author_association") == "OWNER"]
    if not any(heading(c).lower().startswith("independent review") for c in owned):
        failures.append("no 'Independent review' comment from the owner account")
    lead = [c for c in owned if heading(c).lower().startswith("lead review")]
    if not any(head[:7] in c and VERDICT_RE.search(c) for c in lead):
        failures.append(f"no 'Lead review' with 'Verdict: merge' naming the head SHA {head[:7]}")
    if classification.feature:
        failures += feature_failures(body, section, owned)
    return failures


def feature_failures(body: str, section: str | None, owned: list[str]) -> list[str]:
    """The extra conditions for a Tier A `feat`: built as planned, with nothing found.

    Each fails closed: a missing attestation sends the PR to the human.
    """
    failures: list[str] = []
    if not DEVIATIONS_NONE_RE.search(body):
        failures.append("feature: the header doesn't say **Deviations:** none")
    reviews = [c for c in owned if heading(c).lower().startswith("independent review")]
    if len(reviews) > 1:
        failures.append("feature: reviewed more than once")
    if not any(FEATURE_CONFIRMED_RE.search(c) for c in reviews):
        failures.append("feature: no 'Tier A feature: confirmed' in the Independent review")
    if section is not None:
        items = [ln for ln in section.splitlines() if ln.lstrip().startswith(("-", "*"))]
        if any(DEFECT_RE.search(ln) for ln in items):
            failures.append("feature: the review found a defect")
        elif any(not RESOLVED_RE.search(ln) for ln in items):
            failures.append("feature: 'For human review' has items for the human")
    return failures


# --- GitHub (REST) -------------------------------------------------------------


def gh_api(path: str, *extra: str, paginate: bool = False) -> Any:
    args = ["gh", "api", f"repos/{REPO}/{path}", *extra]
    if paginate:
        args[2:2] = ["--paginate", "--slurp"]
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT, check=False
        )
    except subprocess.TimeoutExpired:
        sys.exit(f"timed out after {TIMEOUT}s: {' '.join(args)}")
    if result.returncode != 0:
        sys.exit(f"failed: {' '.join(args)}\n{result.stderr.strip()}")
    data = json.loads(result.stdout) if result.stdout.strip() else None
    if paginate and isinstance(data, list):
        return [item for page in data for item in page]
    return data


def fetch_files(number: int) -> list[ChangedFile]:
    return [
        ChangedFile(f["filename"], f["additions"] + f["deletions"], f.get("previous_filename"))
        for f in gh_api(f"pulls/{number}/files?per_page=100", paginate=True)
    ]


def linked_issue_labels(body: str) -> list[set[str]]:
    """The label names of each issue the PR body closes."""
    return [
        {label["name"] for label in gh_api(f"issues/{number}")["labels"]}
        for number in sorted({int(n) for n in CLOSES_RE.findall(body)})
    ]


def classify_pr(pr: dict[str, Any]) -> Classification:
    labels = {label["name"] for label in pr["labels"]}
    issues = linked_issue_labels(pr.get("body") or "")
    needs_human = NEEDS_HUMAN_LABEL in labels or any(NEEDS_HUMAN_LABEL in i for i in issues)
    feature_ready = bool(issues) and all(
        AGENT_READY_LABEL in i and i & set(FEATURE_SIZES) for i in issues
    )
    return classify(pr["title"], fetch_files(pr["number"]), needs_human, feature_ready)


def fetch_checks(sha: str) -> dict[str, str]:
    runs = gh_api(f"commits/{sha}/check-runs?per_page=100")["check_runs"]
    latest: dict[str, dict[str, Any]] = {}
    for run in runs:  # a re-run leaves the older run in the list; keep the newest
        name = run["name"]
        if name not in latest or run["started_at"] > latest[name]["started_at"]:
            latest[name] = run
    return {name: run["conclusion"] or run["status"] for name, run in latest.items()}


def fetch_pr(number: int) -> dict[str, Any]:
    pr: dict[str, Any] = gh_api(f"pulls/{number}")
    # GitHub computes mergeable_state lazily; "unknown" just means "ask again".
    if pr.get("mergeable_state") == "unknown":
        time.sleep(3)
        pr = gh_api(f"pulls/{number}")
    return pr


def set_tier_label(number: int, tier: str, current: set[str]) -> None:
    wanted = f"tier:{tier}"
    for label in (current & set(TIER_LABELS)) - {wanted}:
        gh_api(f"issues/{number}/labels/{label.replace(':', '%3A')}", "-X", "DELETE")
    if wanted not in current:
        gh_api(f"issues/{number}/labels", "-X", "POST", "-f", f"labels[]={wanted}")


def cmd_classify(number: int, label: bool) -> None:
    pr = fetch_pr(number)
    result = classify_pr(pr)
    print(f"PR #{number}: Tier {result.tier}")
    for reason in result.reasons:
        print(f"  - {reason}")
    if label:
        set_tier_label(number, result.tier, {lb["name"] for lb in pr["labels"]})
        print(f"label tier:{result.tier} set")


def cmd_merge(number: int, dry_run: bool) -> None:
    pr = fetch_pr(number)
    result = classify_pr(pr)
    comments = gh_api(f"issues/{number}/comments?per_page=100", paginate=True)
    failures = gate_failures(pr, result, fetch_checks(pr["head"]["sha"]), comments)
    if failures:
        print(f"PR #{number}: not merging")
        for reason in failures:
            print(f"  - {reason}")
        sys.exit(1)
    sha = pr["head"]["sha"]
    if dry_run:
        print(f"PR #{number}: gate passes at {sha[:7]} (dry run, not merged)")
        return
    gh_api(f"pulls/{number}/merge", "-X", "PUT", "-f", "merge_method=merge", "-f", f"sha={sha}")
    gh_api(f"issues/{number}/labels", "-X", "POST", "-f", f"labels[]={MERGED_LABEL}")
    print(f"PR #{number}: merged at {sha[:7]} and labelled {MERGED_LABEL}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_classify = sub.add_parser("classify", help="print a PR's tier and the reasons")
    p_classify.add_argument("pr", type=int)
    p_classify.add_argument("--label", action="store_true", help="set the tier:X label")
    p_merge = sub.add_parser("merge", help="merge a Tier A PR if the gate passes")
    p_merge.add_argument("pr", type=int)
    p_merge.add_argument("--dry-run", action="store_true", help="check the gate only")
    args = parser.parse_args()
    if args.command == "classify":
        cmd_classify(args.pr, args.label)
    else:
        cmd_merge(args.pr, args.dry_run)


if __name__ == "__main__":
    main()
