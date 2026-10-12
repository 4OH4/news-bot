"""Build, render, write and compare evaluation reports (#18).

A report is a plain JSON-able dict (``build_report``) rendered to Markdown
(``render_markdown``). Both are committed to ``eval/results/``, a public repo, so
they hold metrics, entry ids and short failure categories only, never extracted
text. Keys are sorted and numbers rounded, so identical inputs give identical
files; the only run-to-run variation is the measured timings.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from newsfeed.eval.runner import Evaluation, Outcome

__all__ = [
    "HEADLINE_PREFERRED",
    "SCHEMA_VERSION",
    "build_report",
    "compare_reports",
    "load_report",
    "render_markdown",
    "render_summary",
    "write_report",
]

SCHEMA_VERSION: Final = 1
HEADLINE_PREFERRED: Final = "chain"
"""The extractor whose score is the headline, when it was run: it is what production uses."""

SLOWEST_COUNT: Final = 5

Report = dict[str, Any]


def _percent(clean: int, total: int) -> float | None:
    return round(100 * clean / total, 1) if total else None


def _tally(outcomes: list[Outcome]) -> dict[str, Any]:
    clean = sum(o.score.clean for o in outcomes)
    return {"clean": clean, "total": len(outcomes), "percent": _percent(clean, len(outcomes))}


def _headline(extractors: Mapping[str, Mapping[str, Any]]) -> dict[str, Any] | None:
    if not extractors:
        return None
    if HEADLINE_PREFERRED in extractors:
        name = HEADLINE_PREFERRED
    else:
        # Best clean rate; ties go to the alphabetically first name.
        name = min(
            extractors,
            key=lambda n: (-(extractors[n]["percent"] or 0.0), n),
        )
    return {"extractor": name, **extractors[name]}


def build_report(evaluation: Evaluation, *, date: str, commit: str) -> Report:
    """Aggregate ``evaluation`` into a report dict.

    Paywalled entries are kept out of the headline, the per-extractor table and
    the domain matrix, and summarised in their own section.
    """
    scored = [o for o in evaluation.outcomes if not o.paywalled]
    paywalled = [o for o in evaluation.outcomes if o.paywalled]

    by_extractor: dict[str, list[Outcome]] = defaultdict(list)
    for outcome in scored:
        by_extractor[outcome.extractor].append(outcome)
    extractors: dict[str, Any] = {}
    for name, items in by_extractor.items():
        failures = Counter(f for o in items for f in o.score.failures)
        statuses = Counter(o.status for o in items)
        extractors[name] = {
            **_tally(items),
            "failures": {str(k): failures[k] for k in sorted(failures)},
            "statuses": dict(sorted(statuses.items())),
        }

    cells: dict[str, dict[str, list[Outcome]]] = defaultdict(lambda: defaultdict(list))
    for outcome in scored:
        cells[outcome.domain][outcome.extractor].append(outcome)
    domains = {
        domain: {name: _tally(items) for name, items in by_name.items()}
        for domain, by_name in cells.items()
    }

    paywall_section: dict[str, Any] = {}
    paywall_by_extractor: dict[str, list[Outcome]] = defaultdict(list)
    for outcome in paywalled:
        paywall_by_extractor[outcome.extractor].append(outcome)
    for name, items in paywall_by_extractor.items():
        paywall_section[name] = {
            "total": len(items),
            "detected_paywall": sum(o.status == "paywalled" for o in items),
            "clean": sum(o.score.clean for o in items),
        }

    ranked = sorted(evaluation.outcomes, key=lambda o: (-o.seconds, o.entry_id, o.extractor))
    slowest = [
        {"entry": o.entry_id, "extractor": o.extractor, "seconds": round(o.seconds, 3)}
        for o in ranked[:SLOWEST_COUNT]
    ]

    outcomes: dict[str, dict[str, Any]] = defaultdict(dict)
    for outcome in evaluation.outcomes:
        outcomes[outcome.entry_id][outcome.extractor] = {
            "clean": outcome.score.clean,
            "status": outcome.status,
            "words": outcome.words,
            "failures": [str(f) for f in outcome.score.failures],
            "details": list(outcome.score.details),
            "paywalled": outcome.paywalled,
        }

    return {
        "schema": SCHEMA_VERSION,
        "date": date,
        "commit": commit,
        "entries": {
            "total": evaluation.total_entries,
            "evaluated": evaluation.evaluated,
            "not_snapshotted": sorted(evaluation.not_snapshotted),
            "unreadable": sorted(evaluation.unreadable),
        },
        "headline": _headline(extractors),
        "extractors": extractors,
        "domains": domains,
        "paywalled": paywall_section,
        "slowest": slowest,
        "outcomes": outcomes,
    }


# --- Markdown -------------------------------------------------------------------


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def render_markdown(report: Report, comparison: list[str] | None = None) -> str:
    """Render ``report`` as the Markdown file committed to ``eval/results/``."""
    entries = report["entries"]
    headline = report["headline"]
    names = sorted(report["extractors"])
    out = [f"# Extraction evaluation {report['date']} ({report['commit']})", ""]

    if headline is None:
        out.append("**Headline:** no entries were evaluated.")
    else:
        out.append(
            f"**Headline clean:** {_pct(headline['percent'])} "
            f"({headline['clean']}/{headline['total']}, extractor `{headline['extractor']}`, "
            "paywalled entries excluded)"
        )
    out += [
        "",
        f"Entries: {entries['total']} in the evaluation set, {entries['evaluated']} evaluated, "
        f"{len(entries['not_snapshotted'])} not snapshotted, "
        f"{len(entries['unreadable'])} with unreadable snapshots.",
        "",
    ]

    if comparison:
        out += ["## Comparison", "", *comparison, ""]

    if names:
        out += ["## By extractor", ""]
        out += _table(
            ["Extractor", "Clean", "Total", "Clean %"],
            [
                [
                    f"`{n}`",
                    str(report["extractors"][n]["clean"]),
                    str(report["extractors"][n]["total"]),
                    _pct(report["extractors"][n]["percent"]),
                ]
                for n in names
            ],
        )
        out += ["", "## By domain", ""]
        out += _table(
            ["Domain", *[f"`{n}`" for n in names]],
            [
                [
                    domain,
                    *[
                        f"{cell['clean']}/{cell['total']}" if (cell := cells.get(n)) else "-"
                        for n in names
                    ],
                ]
                for domain, cells in sorted(report["domains"].items())
            ],
        )
        out += ["", "## Failure reasons", ""]
        categories = sorted({c for n in names for c in report["extractors"][n]["failures"]})
        if categories:
            out += _table(
                ["Reason", *[f"`{n}`" for n in names]],
                [
                    [c, *[str(report["extractors"][n]["failures"].get(c, 0)) for n in names]]
                    for c in categories
                ],
            )
        else:
            out.append("None.")

    if report["paywalled"]:
        out += ["", "## Paywalled entries (not in the headline)", ""]
        out += _table(
            ["Extractor", "Entries", "Detected as paywalled", "Clean"],
            [
                [f"`{n}`", str(p["total"]), str(p["detected_paywall"]), str(p["clean"])]
                for n, p in sorted(report["paywalled"].items())
            ],
        )

    if report["slowest"]:
        out += ["", "## Slowest extractions", ""]
        out += _table(
            ["Entry", "Extractor", "Seconds"],
            [[s["entry"], f"`{s['extractor']}`", f"{s['seconds']:.3f}"] for s in report["slowest"]],
        )

    if entries["not_snapshotted"]:
        out += ["", "## Not snapshotted", ""]
        out += [f"- {entry_id}" for entry_id in entries["not_snapshotted"]]
    if entries["unreadable"]:
        out += ["", "## Unreadable snapshots", ""]
        out += [f"- {entry_id}" for entry_id in entries["unreadable"]]

    failing = [
        (entry_id, name, result)
        for entry_id, by_name in sorted(report["outcomes"].items())
        for name, result in sorted(by_name.items())
        if not result["clean"] and not result["paywalled"]
    ]
    if failing:
        out += ["", "## Failures", ""]
        out += _table(
            ["Entry", "Extractor", "Status", "Why"],
            [
                [entry_id, f"`{name}`", r["status"], "; ".join(r["details"])]
                for entry_id, name, r in failing
            ],
        )
    return "\n".join(out) + "\n"


def render_summary(report: Report, comparison: list[str] | None = None) -> str:
    """A short text summary for the terminal."""
    entries = report["entries"]
    headline = report["headline"]
    lines = [
        f"Evaluated {entries['evaluated']} of {entries['total']} entries "
        f"({len(entries['not_snapshotted'])} not snapshotted, "
        f"{len(entries['unreadable'])} unreadable)."
    ]
    if headline is not None:
        lines.append(
            f"Headline clean: {_pct(headline['percent'])} "
            f"({headline['clean']}/{headline['total']}, {headline['extractor']})"
        )
    for name, stats in sorted(report["extractors"].items()):
        lines.append(f"  {name}: {_pct(stats['percent'])} ({stats['clean']}/{stats['total']})")
    if entries["not_snapshotted"]:
        lines.append("Not snapshotted: " + ", ".join(entries["not_snapshotted"]))
    if comparison:
        lines += ["", *comparison]
    return "\n".join(lines)


# --- Writing and comparing --------------------------------------------------------


def write_report(
    report: Report, results_dir: Path, comparison: list[str] | None = None
) -> tuple[Path, Path]:
    """Write ``<date>-<commit>.md`` and ``.json`` into ``results_dir``; return both paths."""
    results_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{report['date']}-{report['commit']}"
    md_path = results_dir / f"{stem}.md"
    json_path = results_dir / f"{stem}.json"
    md_path.write_text(render_markdown(report, comparison), encoding="utf-8")
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return md_path, json_path


def load_report(path: Path) -> Report:
    """Read an earlier results JSON.

    Raises:
        ValueError: if the file isn't a readable report of a supported schema.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: cannot read report: {exc}") from None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA_VERSION:
        raise ValueError(f"{path}: not a schema {SCHEMA_VERSION} evaluation report")
    return data


def _delta(old: float | None, new: float | None) -> str:
    if old is None or new is None:
        return f"{_pct(old)} -> {_pct(new)}"
    return f"{old:.1f}% -> {new:.1f}% ({new - old:+.1f})"


def compare_reports(old: Report, new: Report) -> list[str]:
    """Describe how ``new`` differs from ``old``: scores, then per-entry changes."""
    lines = [f"Compared with {old['date']} ({old['commit']}):"]
    old_head, new_head = old["headline"], new["headline"]
    if old_head and new_head:
        lines.append(
            f"- Headline: {_delta(old_head['percent'], new_head['percent'])} "
            f"(`{old_head['extractor']}` vs `{new_head['extractor']}`)"
        )
    for name in sorted(set(old["extractors"]) | set(new["extractors"])):
        before = old["extractors"].get(name, {}).get("percent")
        after = new["extractors"].get(name, {}).get("percent")
        lines.append(f"- `{name}`: {_delta(before, after)}")

    fixed: list[str] = []
    regressed: list[str] = []
    for entry_id, by_name in sorted(new["outcomes"].items()):
        for name, result in sorted(by_name.items()):
            if result["paywalled"]:
                continue
            before = old["outcomes"].get(entry_id, {}).get(name)
            if before is None:
                continue
            label = f"{entry_id} (`{name}`)"
            if result["clean"] and not before["clean"]:
                fixed.append(label)
            elif before["clean"] and not result["clean"]:
                regressed.append(f"{label}: {'; '.join(result['details'])}")
    lines.append(f"- Newly clean ({len(fixed)}): " + (", ".join(fixed) or "none"))
    lines.append(f"- Regressed ({len(regressed)}): " + (", ".join(regressed) or "none"))
    return lines
