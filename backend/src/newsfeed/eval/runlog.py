"""The detailed per-run log of an evaluation: one JSON line per entry (#18).

The committed report (``report.py``) holds aggregate metrics. This log holds the
per-entry data they are collated from, so a score can be traced back to the
entries and checks behind it. It is written to ``eval/logs/``, which is
git-ignored, one file per run.

It holds no page content or extracted text, only URLs, statuses, error
messages, numbers and check outcomes. The one exception is the
``must_contain`` snippets, which are copied from ``eval/urls.yaml`` (already in
the repo) so a missing snippet can be read without cross-referencing.

Format (JSON Lines): the first line is a ``"record": "run"`` header with the
run's settings; each following line is a ``"record": "entry"`` object for one
entry, in evaluation-set order. Load it with, for example::

    [json.loads(line) for line in path.open()][1:]
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from newsfeed.eval.runner import Evaluation, Outcome, Skipped, entry_domain
from newsfeed.eval.scoring import BOILERPLATE_MAX_HITS, TITLE_MIN_RATIO
from newsfeed.eval.urls import EvalEntry

__all__ = ["LOG_SCHEMA_VERSION", "build_log_records", "log_path", "write_log"]

LOG_SCHEMA_VERSION: Final = 1

ERROR_MAX_CHARS: Final = 300
"""Longest error message kept. Extractor errors are short; this guards the log
against one that quotes a page."""

Record = dict[str, Any]


def _short(message: str | None) -> str | None:
    if message is None or len(message) <= ERROR_MAX_CHARS:
        return message
    return message[: ERROR_MAX_CHARS - 1] + "…"


def _result_record(entry: EvalEntry, outcome: Outcome) -> Record:
    """One extractor's result for one entry: status, metrics and check outcomes."""
    record: Record = {
        "clean": outcome.score.clean,
        "status": outcome.status,
        "method": outcome.method,
        "error": _short(outcome.error),
        "words": outcome.words,
        "seconds": round(outcome.seconds, 3),
        "failures": [str(f) for f in outcome.score.failures],
        "details": list(outcome.score.details),
    }
    checks = outcome.score.checks
    if checks is None:
        # Extraction failed, so the content checks were skipped.
        record["checks"] = None
    else:
        word_range = entry.word_range
        record["checks"] = {
            "title": {
                "expected": entry.expected_title is not None,
                "ratio": checks.title_ratio,
                "via": checks.title_via,
                "min_ratio": TITLE_MIN_RATIO,
            },
            "must_contain": [
                {"n": n, "snippet": snippet, "found": n in checks.snippets_found}
                for n, snippet in enumerate(entry.must_contain, 1)
            ],
            "word_range": {
                "min": None if word_range is None else word_range[0],
                "max": None if word_range is None else word_range[1],
                "words": outcome.words,
                "in_range": (
                    None if word_range is None else word_range[0] <= outcome.words <= word_range[1]
                ),
            },
            "boilerplate": {
                "hits": checks.boilerplate_hits,
                "max_hits": BOILERPLATE_MAX_HITS,
                "phrases": dict(checks.boilerplate),
            },
        }
    if outcome.attempts:
        record["attempts"] = [
            {
                "method": a.method,
                "status": str(a.status),
                "error": _short(a.error),
                "words": a.word_count,
            }
            for a in outcome.attempts
        ]
    return record


def _entry_base(entry: EvalEntry) -> Record:
    return {
        "record": "entry",
        "entry": entry.id,
        "url": entry.url,
        "domain": entry_domain(entry),
        "paywalled": entry.paywalled,
    }


def build_log_records(
    entries: Sequence[EvalEntry],
    evaluation: Evaluation,
    *,
    started: datetime,
    commit: str,
    fixtures_dir: Path,
    urls_path: Path,
) -> list[Record]:
    """Build the log: a run header, then one record per entry in ``entries`` order.

    ``entries`` are the entries the run was given (after any ``--domain``
    filter); ``evaluation`` is what ``run_evaluation`` made of them.
    """
    by_entry: dict[str, list[Outcome]] = {}
    for outcome in evaluation.outcomes:
        by_entry.setdefault(outcome.entry_id, []).append(outcome)
    skipped: dict[str, Skipped] = {s.entry_id: s for s in evaluation.skipped}

    extractor_names = list(dict.fromkeys(o.extractor for o in evaluation.outcomes))
    records: list[Record] = [
        {
            "record": "run",
            "schema": LOG_SCHEMA_VERSION,
            "started": started.isoformat(),
            "commit": commit,
            "urls_file": str(urls_path),
            "fixtures_dir": str(fixtures_dir),
            "extractors": extractor_names,
            "entries": evaluation.total_entries,
            "evaluated": evaluation.evaluated,
            "not_snapshotted": len(evaluation.not_snapshotted),
            "unreadable": len(evaluation.unreadable),
        }
    ]
    for entry in entries:
        record = _entry_base(entry)
        if entry.id in skipped:
            skip = skipped[entry.id]
            record.update(snapshot=skip.reason, snapshot_detail=_short(skip.detail), results={})
        else:
            outcomes = by_entry.get(entry.id, [])
            record.update(
                snapshot="ok",
                final_url=outcomes[0].final_url if outcomes else None,
                results={o.extractor: _result_record(entry, o) for o in outcomes},
            )
        records.append(record)
    return records


def log_path(logs_dir: Path, started: datetime, commit: str) -> Path:
    """``<logs_dir>/<UTC timestamp>-<commit>.jsonl``, unique per run to the second."""
    return logs_dir / f"{started.strftime('%Y%m%dT%H%M%SZ')}-{commit}.jsonl"


def write_log(records: Sequence[Record], path: Path) -> Path:
    """Write ``records`` to ``path`` as JSON Lines (sorted keys) and return ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = (json.dumps(r, sort_keys=True, ensure_ascii=False) for r in records)
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return path
