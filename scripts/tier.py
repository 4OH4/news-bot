#!/usr/bin/env python3
"""Classify a PR into a risk tier, report its test changes, and merge it for agents.

  python3 scripts/tier.py classify 70            # print the tier and why
  python3 scripts/tier.py classify 70 --label    # ...and set the tier:A/B/C label
  python3 scripts/tier.py tests 70               # print the test change report
  python3 scripts/tier.py merge 70 --dry-run     # check the merge gate only
  python3 scripts/tier.py merge 70               # check the gate, then merge

Tiers (AUTOMATION.md §9):

* Tier C: the human merges. The automation itself (.claude/, .github/, scripts/ and
  their tests, AUTOMATION.md, CLAUDE.md, the build files in TIER_C_PATHS), the files that
  define the checks (CHECK_CONFIG_NAMES, and [tool.*] tables in pyproject.toml), fetching/, milestone reports, infrastructure that touches IAM or other access,
  billing, budgets, service accounts or delete rules, or that can destroy stored data,
  and any needs-human PR or issue.
* Tier B: the delivery manager merges, with a `Confirm later` entry. Decide-and-record
  changes: contracts (including the feed schema), ADRs, dependencies, curated config,
  the evaluation expectations and scorer, test infrastructure, REVIEW.md, PROJECT_PLAN.md,
  and other infra/ files.
* Tier A: the delivery manager merges. Everything else.

The tier comes from the diff, not from anyone's judgement, and this script is in
Tier C itself, so agents can't relax the rules without the human merging that.

`merge` is the only way an agent may merge a PR. That is a rule in CLAUDE.md, not a
technical barrier: `gh pr merge` is denied in .claude/settings.json, but the REST merge
endpoint is reachable through the allowed `gh api` prefix. It refuses unless every condition in
`gate_failures` holds, and pins the merge to the head SHA it checked.

The PRs that were open when these rules arrived (#123, #124, #125, #126, #129, #130) have
"Lead review" comments and no statement of the review checks, so the gate refuses them.
That's safe: the human merges them, under the rules they were written for (AUTOMATION.md §12).

Uses the REST API only (separate allowance from GraphQL; see CLAUDE.md).
Requirements: Python 3.10+ (standard library only), gh.
"""

from __future__ import annotations

import argparse
import base64
import fnmatch
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from itertools import takewhile
from typing import Any

REPO = "4OH4/news-bot"
TIMEOUT = 120
REQUIRED_CHECKS = ("lint", "test", "docker")  # must match the "Protect main" ruleset
TIER_LABELS = ("tier:A", "tier:B", "tier:C")
HOLD_LABEL = "hold"
MERGED_LABEL = "agent-merged"
NEEDS_HUMAN_LABEL = "needs-human"

# What can't be reversed cheaply, plus the automation itself (so agents can't loosen
# their own rules).
TIER_C_PATHS = (
    ".claude/*",
    ".github/*",
    "scripts/*",
    "backend/tests/scripts/*",  # the tests of this gate
    # What goes into the image and the repo. The checks themselves are CHECK_CONFIG_NAMES.
    ".gitignore",
    ".dockerignore",
    "backend/Dockerfile",
    "AUTOMATION.md",
    "CLAUDE.md",
    "backend/src/newsfeed/fetching/*",  # polite fetching, CLAUDE.md architecture rule 6
    "docs/milestones/*",
    "infra/*iam*",
    "infra/*billing*",
    "infra/*budget*",
)
# The files that define the checks, by name in any directory, because each tool takes the
# nearest one it finds: a two-line pytest.ini replaces the settings in pyproject.toml, and a
# ruff.toml or pyproject.toml lower down replaces the lint rules for that directory.
CHECK_CONFIG_NAMES = (
    "Makefile",
    "makefile",
    "GNUmakefile",
    ".pre-commit-config.yaml",
    "conftest.py",
    "pyproject.toml",
    "pytest.ini",
    ".pytest.ini",
    "pytest.toml",
    ".pytest.toml",
    "tox.ini",
    "setup.cfg",
    "ruff.toml",
    ".ruff.toml",
    "mypy.ini",
    ".mypy.ini",
    "pyrightconfig.json",
    ".coveragerc",
    # The client's equivalents, for Phase 3.
    "tsconfig*.json",
    ".eslintrc*",
    "eslint.config.*",
    ".prettierrc*",
    "prettier.config.*",
    "jest.config.*",
    "vitest.config.*",
    "playwright.config.*",
)
# The two of those that aren't Tier C. Test infrastructure is Tier B, and most of
# backend/pyproject.toml is too, so that dependencies can be: `tool_settings_changed`
# picks out the parts of it that define the checks.
PYPROJECT = "backend/pyproject.toml"
TESTS_PREFIX = "backend/tests/"

# Terraform is infrastructure wherever the file sits.
INFRA_PATHS = ("infra/*", "*.tf", "*.tfvars", "*.tf.json")
# Terraform reads the files by name in one directory, and a rename's patch shows only its edits.
TF_CONFIG_PATHS = ("*.tf", "*.tf.json")
# Who can reach what, and who pays, can sit in any infra file, and a one-line change often
# names neither: `member = "allUsers"` shows as IAM only through the resource around it. So
# these two are matched against the whole patch, context lines included.
INFRA_RISK_RE = re.compile(
    r"(?<![a-z0-9])iam(?![a-z0-9])"  # `_` and `-` count as boundaries: google_iam_policy
    r"|roles/|\bmembers?\s*=|\bpolicy_data\b|\bpermissions\s*=|\bbinding\s*\{"
    r"|allUsers|allAuthenticatedUsers|service_account"
    r"|(?<![a-z0-9])acl(?![a-z0-9])|google_storage_\w*access_control"
    r"|public_access_prevention|uniform_bucket_level_access"
    r"|billing|budget",
    re.IGNORECASE,
)
# Retention sets when a delete rule fires, and a shorter `default` under
# `variable "content_retention_days"` names it only on a context line.
DELETE_RULE_RE = re.compile(
    r'lifecycle_rule|ttl_config|retention|type"?\s*[=:]\s*"delete"', re.IGNORECASE
)
# These only matter when they change, so they're matched against changed lines. A protection
# flag counts whichever way it moves, and `force_destroy` unless it is plainly off.
DATA_LOSS_RE = re.compile(
    r"force_destroy\s*=(?!\s*false\b)"
    r"|deletion_protection|delete_protection_state|deletion_policy|prevent_destroy"
    r"|soft_delete_policy"
    r"|^\s*removed\s*\{"  # Terraform's block for dropping a resource from a configuration
    r"|\bgsutil\b.*\b(?:rm|rb)\b|\bgcloud\b.*\b(?:rm|delete)\b|\bterraform\s+destroy\b",
    re.IGNORECASE,
)
# What holds data or the project itself. Removing the block destroys it, as does taking its
# `count` or `for_each` to nothing, and a module can hold any of them.
STORE_RESOURCE_RE = re.compile(
    r'^[ +-]?\s*(?:module\s+"|resource\s+"google_(?:storage_bucket|firestore_\w+|project)")',
    re.IGNORECASE | re.MULTILINE,
)
COUNT_RE = re.compile(r"^\s*(?:count|for_each)\s*=")

# Decide-and-record: agents choose, and the human confirms at the milestone.
EVAL_DECISION_PATHS = ("eval/urls.yaml", "eval/boilerplate.txt", "backend/src/newsfeed/eval/*")
TIER_B_PATHS = (
    "backend/src/newsfeed/models.py",
    "backend/src/newsfeed/ports.py",
    "docs/adr/*",
    "docs/feed.schema.json",  # the client contract, ADR-0006
    "docs/feed.example.json",
    "backend/pyproject.toml",
    "backend/uv.lock",
    "client/package.json",
    "client/package-lock.json",
    "config/interests.yaml",
    "config/sources.yaml",
    *EVAL_DECISION_PATHS,
    # Test infrastructure keeps every other test honest.
    "backend/tests/conftest.py",
    "backend/tests/*/conftest.py",
    "backend/tests/fakes.py",
    "backend/tests/*/fakes.py",
    "backend/tests/test_network_guard.py",
    "backend/tests/contract/*",
    "REVIEW.md",
    "PROJECT_PLAN.md",
    *INFRA_PATHS,
)

# One size limit for every PR. Oversized PRs go back to a worker to split.
MAX_SRC_LINES = 400
MAX_TOTAL_LINES = 1500
SRC_PATHS = ("backend/src/*", "client/*")
# Generated, often thousands of lines: they don't count towards the limits.
GENERATED_PATHS = ("eval/results/*", "backend/uv.lock", "client/package-lock.json")
EXTRACT_PREFIX = "backend/src/newsfeed/extract/"

HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)")
TOML_TABLE_RE = re.compile(r"^\s*\[")
TOML_TOOL_TABLE_RE = re.compile(r"""^\s*\[\[?\s*["']?tool\b""")
# The same settings without a [tool.*] header: `tool.ruff.line-length = 100`.
TOML_TOOL_KEY_RE = re.compile(r"""^\s*["']?tool["']?\s*[.=]""")

TEST_PATHS = (
    "backend/tests/*",
    "client/*.test.*",
    "client/*.spec.*",
    "client/*__tests__/*",
    "client/test/*",
    "client/tests/*",
)
# One line of a test, in Python or in the client's JavaScript or TypeScript. A multi-line
# assert shows only where its first line changes.
ASSERTION_RE = re.compile(
    r"^\s*(?:assert\b|with pytest\.raises\b|pytest\.(?:raises|fail)\(|(?:async\s+)?def test_)"
    r"|\.assert_\w*\("
    r"|^\s*(?:it|test)(?:\.\w+)*\(|\bexpect\("
)
# Anything that stops a test running by default. `live` tests are deselected in CI.
SKIP_RE = re.compile(
    r"pytest\.(?:skip|xfail|importorskip)\b|\bmark\.(?:skip|skipif|xfail|live)\b"
    r"|\bunittest\.skip"
    r"|\b(?:it|test|describe)\.(?:skip|only|todo|fixme)\b|\bx(?:it|describe)\("
)
TEST_COMMIT_RE = re.compile(r"^test(?:\([^)]*\))?:")
# The test author ends its commits with this trailer (.claude/agents/test-author.md),
# because a `test:` subject alone is what a worker's own first commit looks like too.
TEST_AUTHOR_TRAILER_RE = re.compile(r"^Test-Author:[^\S\n]*\S", re.IGNORECASE | re.MULTILINE)

CLOSES_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.IGNORECASE)
SECTION_RE = re.compile(r"^##\s+For human review\s*$(.*?)(?=^##\s|\Z)", re.MULTILINE | re.DOTALL)
SECTION_HEADING_RE = re.compile(r"^#+\s+For human review", re.MULTILINE | re.IGNORECASE)
FIRST_SECTION_RE = re.compile(r"^##\s", re.MULTILINE)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _marker(word: str) -> re.Pattern[str]:
    """`**Word:**` or `**Word**:`, any case, as reviewers may type either."""
    return re.compile(rf"\*\*\s*{word}\s*(?::\s*\*\*|\*\*\s*:)", re.IGNORECASE)


DEFECT_RE = _marker("defect")
NEEDS_HUMAN_RE = _marker("needs\\s+human")
CONFIRM_LATER_RE = _marker("confirm\\s+later")
RESOLVED_RE = re.compile(r"\*\*\s*Resolved\s*\*\*\s+in\s+[0-9a-f]{7,40}\b", re.IGNORECASE)
# `[^\w\n]`, not `\W`, in everything that reads one line: `\W` and `\s` match a newline, so an
# empty field would borrow the next line's text.
VERDICT_RE = re.compile(r"^[^\w\n]*verdict[^\w\n]*merge\b", re.IGNORECASE | re.MULTILINE)

# The review's statement of the AUTOMATION.md §7.3 checks: one line, naming each one.
# A check that doesn't apply says so ("red n/a, docs only"); one left undone must not pass.
CHECKS_LINE_RE = re.compile(r"^\W*checks done\b.*$", re.IGNORECASE | re.MULTILINE)
REQUIRED_CHECK_WORDS = {
    "red": re.compile(r"\bred\b", re.IGNORECASE),
    "mutation": re.compile(r"\bmutation\b", re.IGNORECASE),
    "test change report": re.compile(r"\btest change report\b", re.IGNORECASE),
    "REVIEW.md": re.compile(r"\bREVIEW\.md\b", re.IGNORECASE),
}
CHECK_NOT_DONE_RE = re.compile(r"\bnot done\b|\bskipped\b", re.IGNORECASE)

EVAL_BEFORE_RE = re.compile(r"^[^\w\n]*before[^\w\n]+(\d+(?:\.\d+)?)", re.IGNORECASE | re.MULTILINE)
EVAL_AFTER_RE = re.compile(r"^[^\w\n]*after[^\w\n]+(\d+(?:\.\d+)?)", re.IGNORECASE | re.MULTILINE)
EVAL_REASON_RE = re.compile(r"^[^\w\n]*reason[^\w\n]+(\w.*)$", re.IGNORECASE | re.MULTILINE)

# What a required field holds when nothing was really recorded.
PLACEHOLDERS = frozenset(
    {
        "",
        "n/a",
        "na",
        "nil",
        "no",
        "no decisions",
        "none",
        "none needed",
        "none yet",
        "not applicable",
        "not needed",
        "nothing",
        "null",
        "tbd",
        "todo",
    }
)


@dataclass
class ChangedFile:
    path: str
    changes: int  # additions + deletions
    previous_path: str | None = None
    patch: str | None = None  # GitHub omits it for binary, renamed-only and huge files
    text: str | None = None  # the file at the head, fetched only for backend/pyproject.toml
    removed: bool = False  # the PR deletes the file


@dataclass
class Classification:
    tier: str
    reasons: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)  # the gate refuses these at any tier
    touches_extract: bool = False


def matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(path, p) for p in patterns)


def changed_lines(patch: str | None) -> list[str]:
    """A patch's added and removed lines, without their +/- prefix."""
    return [ln[1:] for ln in (patch or "").splitlines() if ln[:1] in ("+", "-")]


def loses_data(patch: str) -> bool:
    """True if this infra patch can destroy stored data, or the project itself."""
    changed = changed_lines(patch)
    if any(DATA_LOSS_RE.search(ln) for ln in changed):
        return True
    removed = (ln for ln in patch.splitlines() if ln.startswith("-"))
    if any(STORE_RESOURCE_RE.search(ln) for ln in removed):
        return True
    return bool(STORE_RESOURCE_RE.search(patch)) and any(COUNT_RE.search(ln) for ln in changed)


def infra_risk(f: ChangedFile) -> str | None:
    """Why this file's infra change is Tier C, if it is. An unreadable diff counts."""
    is_infra = any(p and matches(p, INFRA_PATHS) for p in (f.path, f.previous_path))
    if not is_infra:
        return None
    if f.previous_path and any(matches(p, TF_CONFIG_PATHS) for p in (f.path, f.previous_path)):
        return f"{f.path} moves Terraform from {f.previous_path}, which can drop what it defines"
    if f.changes == 0 and not f.previous_path:
        return None
    if f.patch is None:
        return f"{f.path}: no diff available to check for IAM or billing changes"
    if INFRA_RISK_RE.search(f.patch):
        return f"{f.path} changes IAM, access, billing, budgets or service accounts"
    if DELETE_RULE_RE.search(f.patch):
        return f"{f.path} changes a delete rule, which can delete content"
    if loses_data(f.patch):
        return f"{f.path} can destroy stored data or the project"
    return None


def defines_checks(path: str) -> bool:
    """True if this path is a check configuration file reserved for Tier C."""
    name = path.rsplit("/", 1)[-1]
    if path == PYPROJECT or (name == "conftest.py" and path.startswith(TESTS_PREFIX)):
        return False
    return matches(name, CHECK_CONFIG_NAMES)


def tool_settings_changed(patch: str, text: str) -> bool:
    """True if a patch to a TOML file changes its tables or anything in a [tool.*] table.

    `text` is the file after the patch. The patch alone can't tell: `addopts` can sit too far
    below its header for the context lines to show it, and a dependency at the end of a list
    shows a [tool.*] header it doesn't change. Any header that changes counts, as a new one
    can move the settings below it out of their table.
    """
    in_tool = []
    current = False
    for line in text.splitlines():
        if TOML_TABLE_RE.search(line):
            current = bool(TOML_TOOL_TABLE_RE.search(line))
        in_tool.append(current)

    next_line = 1  # the line in `text` that the patch has reached, counted from 1
    for line in patch.splitlines():
        if hunk := HUNK_RE.search(line):
            next_line = int(hunk.group(1))
        elif line.startswith(("+", "-")):
            content = line[1:]
            if TOML_TABLE_RE.search(content) or TOML_TOOL_KEY_RE.search(content):
                return True
            # A removed line sat in the same table as the line before it.
            index = next_line - 1 if line.startswith("+") else next_line - 2
            if 0 <= index < len(in_tool) and in_tool[index]:
                return True
            next_line += line.startswith("+")
        elif line.startswith(" "):
            next_line += 1
    return False


def pyproject_risk(f: ChangedFile) -> str | None:
    """Why this change to backend/pyproject.toml is Tier C, if it is."""
    if PYPROJECT not in (f.path, f.previous_path) or (f.changes == 0 and not f.previous_path):
        return None
    if f.patch is None or f.text is None:
        return f"{PYPROJECT}: file not readable, so no way to check its [tool.*] tables"
    if tool_settings_changed(f.patch, f.text):
        return f"{PYPROJECT} changes a [tool.*] table, which defines the checks"
    return None


def size_blocks(files: list[ChangedFile]) -> list[str]:
    counted = [f for f in files if not matches(f.path, GENERATED_PATHS)]
    src_lines = sum(f.changes for f in counted if matches(f.path, SRC_PATHS))
    total = sum(f.changes for f in counted)
    blocks = []
    if src_lines > MAX_SRC_LINES:
        blocks.append(f"{src_lines} source lines changed (limit {MAX_SRC_LINES}): split it")
    if total > MAX_TOTAL_LINES:
        blocks.append(f"{total} lines changed in total (limit {MAX_TOTAL_LINES}): split it")
    return blocks


def classify(files: list[ChangedFile], needs_human: bool) -> Classification:
    """Work out a PR's tier from its changed files and linked issues."""
    # A rename touches both paths, so check the old one too.
    paths = sorted({p for f in files for p in (f.path, f.previous_path) if p})
    extract = any(p.startswith(EXTRACT_PREFIX) for p in paths)
    blocks = size_blocks(files)
    if extract and any(matches(p, EVAL_DECISION_PATHS) for p in paths):
        # AUTOMATION.md §6: a score could otherwise be improved by moving the goalposts.
        blocks.append("changes the evaluation expectations or scorer alongside extract/")

    c_reasons = [
        f"touches {p}" for p in paths if matches(p, TIER_C_PATHS) or defines_checks(p)
    ]
    c_reasons += [risk for f in files if (risk := infra_risk(f) or pyproject_risk(f))]
    if needs_human:
        c_reasons.insert(0, "needs-human PR or issue")
    if c_reasons:
        return Classification("C", c_reasons, blocks, extract)

    b_reasons = [f"touches {p}" for p in paths if matches(p, TIER_B_PATHS)]
    if not files:
        b_reasons.append("no changed files")
    if b_reasons:
        return Classification("B", b_reasons, blocks, extract)
    return Classification("A", ["no Tier B or C paths"], blocks, extract)


def human_review_section(body: str) -> str | None:
    found = SECTION_RE.search(body)
    return found.group(1) if found else None


def unresolved(section: str, marker: re.Pattern[str]) -> list[str]:
    """Items with this marker and no `**Resolved** in <sha>`."""
    return [ln for ln in section.splitlines() if marker.search(ln) and not RESOLVED_RE.search(ln)]


def heading(comment: str) -> str:
    """A comment's first line without Markdown heading or bold markers."""
    first = comment.strip().split("\n", 1)[0]
    return first.lstrip("#* ").strip()


def is_placeholder(value: str) -> bool:
    """True if a required field says nothing, whatever its case, punctuation or bold."""
    return re.sub(r"(?:[^\w/]|_)+", " ", value).strip(" /").lower() in PLACEHOLDERS


def has_confirm_later(body: str) -> bool:
    """True if the header, above the first section, records a decision on the entry's line."""
    found = FIRST_SECTION_RE.search(body)
    header = HTML_COMMENT_RE.sub("", body[: found.start()] if found else body)
    entry = CONFIRM_LATER_RE.search(header)
    if not entry:
        return False
    value = header[entry.end() :].split("\n", 1)[0].split("·", 1)[0]
    return not is_placeholder(value)


def checks_statement_failure(review: str) -> str | None:
    """Why this review doesn't state the §7.3 checks, or None if it does."""
    found = CHECKS_LINE_RE.search(review)
    if not found:
        return "the latest Independent review has no 'Checks done:' line"
    line = found.group(0)
    missing = [name for name, word in REQUIRED_CHECK_WORDS.items() if not word.search(line)]
    if missing:
        return f"'Checks done:' doesn't mention {', '.join(missing)}"
    if CHECK_NOT_DONE_RE.search(line):
        return "'Checks done:' says a check was not done"
    return None


def evaluation_failure(owned: list[str], head: str) -> str | None:
    """Why this extract/ PR has no acceptable scores, or None if it has."""
    comments = [
        c for c in owned if heading(c).lower().startswith("evaluation scores") and head[:7] in c
    ]
    if not comments:
        return f"touches extract/, but no 'Evaluation scores' comment names the head SHA {head[:7]}"
    latest = comments[-1]
    before, after = EVAL_BEFORE_RE.search(latest), EVAL_AFTER_RE.search(latest)
    if not before or not after:
        return "'Evaluation scores' has no 'Before:' and 'After:' lines"
    reasons = [found.group(1) for found in EVAL_REASON_RE.finditer(latest)]
    if float(after.group(1)) < float(before.group(1)) and all(map(is_placeholder, reasons)):
        return f"overall score fell from {before.group(1)} to {after.group(1)} with no 'Reason:'"
    return None


def gate_failures(
    pr: dict[str, Any],
    classification: Classification,
    checks: dict[str, str],
    comments: list[dict[str, Any]],
) -> list[str]:
    """Every reason an agent may not merge this PR. Empty means it may.

    `comments` are REST issue comments. Only the owner's count: agents post as the
    owner, and anyone can comment on a public repo.
    """
    failures: list[str] = []
    head = pr["head"]["sha"]
    labels = {label["name"] for label in pr["labels"]}

    if classification.tier not in ("A", "B"):
        failures.append(f"Tier {classification.tier}: the human merges it")
    failures += classification.blocks
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
        if unresolved(section, NEEDS_HUMAN_RE):
            failures.append("reviewer marked an item **Needs human:**")
        open_defects = unresolved(section, DEFECT_RE)
        if open_defects:
            failures.append(f"{len(open_defects)} unresolved **Defect:** item(s)")
    if classification.tier == "B" and not has_confirm_later(body):
        failures.append("Tier B, but the header has no **Confirm later:** entry")

    owned = [c.get("body") or "" for c in comments if c.get("author_association") == "OWNER"]
    reviews = [c for c in owned if heading(c).lower().startswith("independent review")]
    if not reviews:
        failures.append("no 'Independent review' comment from the owner account")
    elif problem := checks_statement_failure(reviews[-1]):
        failures.append(problem)
    delivery = [c for c in owned if heading(c).lower().startswith("delivery review")]
    if not any(head[:7] in c and VERDICT_RE.search(c) for c in delivery):
        failures.append(
            f"no 'Delivery review' with 'Verdict: merge' naming the head SHA {head[:7]}"
        )
    if classification.touches_extract and (problem := evaluation_failure(owned, head)):
        failures.append(problem)
    return failures


# --- Test change report ----------------------------------------------------------


def hunks(patch: str) -> list[list[str]]:
    """A patch's lines, split at each `@@` hunk header."""
    result: list[list[str]] = []
    for line in patch.splitlines():
        if line.startswith("@@") or not result:
            result.append([])
        if not line.startswith("@@"):
            result[-1].append(line)
    return result


def assertion_changes(path: str, patch: str) -> list[str]:
    """Assertions a test file's patch deletes or changes, and any skips or live marks it adds."""
    findings: list[str] = []
    for hunk in hunks(patch):
        removed = [ln[1:].strip() for ln in hunk if ln.startswith("-")]
        added = [ln[1:].strip() for ln in hunk if ln.startswith("+")]
        replaced = any(ASSERTION_RE.search(ln) for ln in added)
        for line in removed:
            # The same line on both sides is only a change of indent or a move.
            if ASSERTION_RE.search(line) and line not in added:
                findings.append(f"{path}: {'changed' if replaced else 'deleted'}: {line}")
        findings += [
            f"{path}: skip or live marker added: {ln}"
            for ln in added
            if SKIP_RE.search(ln) and ln not in removed
        ]
    return findings


def diff_findings(files: list[ChangedFile]) -> list[str]:
    """The test change report's first part, from the PR's whole diff."""
    findings: list[str] = []
    for f in files:
        if not matches(f.path, TEST_PATHS):
            continue
        if f.patch is not None:
            findings += assertion_changes(f.path, f.patch)
        elif f.changes:
            findings.append(f"{f.path}: no diff available, so check it by hand")
    return findings


def is_test_commit(commit: dict[str, Any]) -> bool:
    message: str = commit["commit"]["message"]
    return bool(TEST_COMMIT_RE.match(message))


def has_test_author_trailer(commit: dict[str, Any]) -> bool:
    """True if the message's last paragraph, where trailers go, has the trailer."""
    message: str = commit["commit"]["message"]
    return bool(TEST_AUTHOR_TRAILER_RE.search(message.rstrip().rsplit("\n\n", 1)[-1]))


def test_author_commits(commits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The `test:` commits with the trailer at the start of the branch (AUTOMATION.md §5.4)."""
    return list(takewhile(lambda c: is_test_commit(c) and has_test_author_trailer(c), commits))


def test_author_edits(commits: list[dict[str, Any]]) -> list[str]:
    """Lines that later commits removed from files in the test author's commits.

    `commits` are REST commit objects, oldest first. Adding to their files is fine, so
    only removed lines are listed; a changed line shows as removed.
    """
    authored = test_author_commits(commits)
    their_files = {f["filename"] for c in authored for f in c.get("files", [])}
    findings: list[str] = []
    for commit in commits[len(authored) :]:
        for f in commit.get("files", []):
            if f["filename"] not in their_files or not f.get("deletions"):
                continue
            where = f"{commit['sha'][:7]} {f['filename']}"
            if "patch" not in f:
                findings.append(f"{where}: no diff available, so check it by hand")
            findings += [
                f"{where}: {ln[1:].strip()}"
                for ln in (f.get("patch") or "").splitlines()
                if ln.startswith("-") and ln[1:].strip()
            ]
    return findings


def authorship_notes(commits: list[dict[str, Any]]) -> list[str]:
    """Commits whose author the report can't tell for certain, so a reviewer checks them."""
    rest = commits[len(test_author_commits(commits)) :]
    unmarked = list(takewhile(is_test_commit, rest))
    notes = [
        f"{c['sha'][:7]}: a leading test: commit with no Test-Author trailer. Counted as the "
        "worker's, so the report can't tell whether it holds a test author's tests"
        for c in unmarked
    ]
    notes += [
        f"{c['sha'][:7]}: has the Test-Author trailer, but not as a test: commit at the start "
        "of the branch. Counted as the worker's"
        for c in rest
        if has_test_author_trailer(c)
    ]
    return notes


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
        ChangedFile(
            f["filename"],
            f["additions"] + f["deletions"],
            f.get("previous_filename"),
            f.get("patch"),
            removed=f.get("status") == "removed",
        )
        for f in gh_api(f"pulls/{number}/files?per_page=100", paginate=True)
    ]


def fetch_text(path: str, sha: str) -> str | None:
    """A file's text at a commit, or None if GitHub doesn't send it inline."""
    data = gh_api(f"contents/{path}?ref={sha}")
    if not isinstance(data, dict) or data.get("encoding") != "base64":
        return None
    return base64.b64decode(data["content"]).decode("utf-8", errors="replace")


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
    files = fetch_files(pr["number"])
    for f in files:
        if f.path == PYPROJECT and not f.removed:
            f.text = fetch_text(PYPROJECT, pr["head"]["sha"])
    return classify(files, needs_human)


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


def fetch_commits(number: int) -> list[dict[str, Any]]:
    """The PR's commits, oldest first, each with its files."""
    listed = gh_api(f"pulls/{number}/commits?per_page=100", paginate=True)
    return [gh_api(f"commits/{c['sha']}") for c in listed]


def cmd_classify(number: int, label: bool) -> None:
    pr = fetch_pr(number)
    result = classify_pr(pr)
    print(f"PR #{number}: Tier {result.tier}")
    for reason in result.reasons:
        print(f"  - {reason}")
    for block in result.blocks:
        print(f"  ! merge blocked: {block}")
    if label:
        set_tier_label(number, result.tier, {lb["name"] for lb in pr["labels"]})
        print(f"label tier:{result.tier} set")


def cmd_tests(number: int) -> None:
    deleted = diff_findings(fetch_files(number))
    commits = fetch_commits(number)
    edits = test_author_edits(commits)
    notes = authorship_notes(commits)
    print(f"PR #{number}: test change report")
    print("Assertions deleted or changed, and skips or live markers added:")
    print("\n".join(f"  - {ln}" for ln in deleted) or "  none")
    print("Edits to the test author's tests:")
    print("\n".join(f"  - {ln}" for ln in edits) or "  none")
    if notes:
        print("Check by hand:")
        print("\n".join(f"  - {ln}" for ln in notes))


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
    p_tests = sub.add_parser("tests", help="print the test change report")
    p_tests.add_argument("pr", type=int)
    p_merge = sub.add_parser("merge", help="merge a Tier A or B PR if the gate passes")
    p_merge.add_argument("pr", type=int)
    p_merge.add_argument("--dry-run", action="store_true", help="check the gate only")
    args = parser.parse_args()
    if args.command == "classify":
        cmd_classify(args.pr, args.label)
    elif args.command == "tests":
        cmd_tests(args.pr)
    else:
        cmd_merge(args.pr, args.dry_run)


if __name__ == "__main__":
    main()
