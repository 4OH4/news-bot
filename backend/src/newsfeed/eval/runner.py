"""Run extractors over saved snapshots and score the results (#18).

Everything here works offline: pages come from the snapshot directory written by
``newsfeed eval snapshot`` (``snapshot.py``), never from the network. Entries
without a usable snapshot are listed as "not snapshotted", not scored as failures.
"""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from newsfeed.eval.scoring import Score, score_result
from newsfeed.eval.snapshot import SnapshotError, read_snapshot, snapshot_paths
from newsfeed.eval.urls import EvalEntry
from newsfeed.extract.chain import ExtractorChain
from newsfeed.extract.newspaper_extractor import Newspaper4kExtractor
from newsfeed.extract.overrides import ExtractionConfig, url_domain
from newsfeed.extract.readability_extractor import ReadabilityExtractor
from newsfeed.extract.trafilatura_extractor import TrafilaturaExtractor
from newsfeed.logging import get_logger
from newsfeed.models import ExtractionResult, ExtractionStatus
from newsfeed.ports import Extractor

__all__ = [
    "Evaluation",
    "Outcome",
    "default_extractors",
    "entry_domain",
    "filter_entries",
    "git_short_sha",
    "run_evaluation",
]

log = get_logger(__name__)

_CHARSET = re.compile(r"charset=([\w.:-]+)", re.IGNORECASE)


@dataclass(frozen=True)
class Outcome:
    """One extractor's scored result for one entry. Holds no article text."""

    entry_id: str
    domain: str
    paywalled: bool
    extractor: str
    status: str
    words: int
    score: Score
    seconds: float


@dataclass
class Evaluation:
    """Everything one harness run produced."""

    total_entries: int = 0
    outcomes: list[Outcome] = field(default_factory=list)
    not_snapshotted: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)

    @property
    def evaluated(self) -> int:
        """Number of entries that had a snapshot and were run."""
        return len({o.entry_id for o in self.outcomes})


def entry_domain(entry: EvalEntry) -> str:
    """The normalised host of the entry's URL (the report's per-domain key)."""
    return url_domain(entry.url) or ""


def filter_entries(entries: Sequence[EvalEntry], domain: str | None) -> list[EvalEntry]:
    """Keep entries on ``domain`` or one of its subdomains; all of them if None."""
    if domain is None:
        return list(entries)
    wanted = domain.strip().lower().removeprefix("www.")
    return [
        e for e in entries if entry_domain(e) == wanted or entry_domain(e).endswith("." + wanted)
    ]


def default_extractors(overrides: ExtractionConfig | None = None) -> list[Extractor]:
    """The static extractors plus the chain built from them, in report order.

    The renderer (Playwright) is deliberately absent: it loads the live URL, but
    the evaluation must work offline from snapshots.
    """
    static: list[Extractor] = [
        TrafilaturaExtractor(),
        ReadabilityExtractor(),
        Newspaper4kExtractor(),
    ]
    return [*static, ExtractorChain(static, overrides)]


def git_short_sha(cwd: Path) -> str:
    """The short commit hash of the checkout at ``cwd`` (``-dirty`` if modified), or ``nogit``."""
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "nogit"
    sha = done.stdout.strip() or "nogit"
    try:
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return sha
    # Mark uncommitted changes so a before/after pair isn't mislabelled.
    return f"{sha}-dirty" if dirty and sha != "nogit" else sha


def _decode(html: bytes, content_type: str | None) -> str:
    """Decode a snapshot body using the response's charset, else UTF-8."""
    match = _CHARSET.search(content_type or "")
    for charset in (match.group(1) if match else None, "utf-8"):
        if charset is None:
            continue
        try:
            return html.decode(charset)
        except (LookupError, UnicodeDecodeError):
            continue
    return html.decode("utf-8", errors="replace")


def _extract(extractor: Extractor, html: str, entry: EvalEntry, url: str) -> ExtractionResult:
    # Extractors must not raise, but one that does mustn't stop the harness.
    try:
        return extractor.extract(html, url)
    except Exception as exc:
        log.warning(
            "extractor raised",
            extra={
                "stage": "eval",
                "entry_id": entry.id,
                "extractor": extractor.name,
                "error_type": type(exc).__name__,
            },
        )
        return ExtractionResult(
            status=ExtractionStatus.FAILED, method=extractor.name, error=type(exc).__name__
        )


def run_evaluation(
    entries: Iterable[EvalEntry],
    fixtures_dir: Path,
    extractors: Sequence[Extractor],
    boilerplate: Sequence[str] = (),
    *,
    timer: Callable[[], float] = time.perf_counter,
) -> Evaluation:
    """Run every extractor on every entry that has a snapshot and score it.

    ``timer`` is injectable so tests get deterministic timings. Entries come out
    in the order given, and each entry's outcomes in extractor order.
    """
    evaluation = Evaluation()
    for entry in entries:
        evaluation.total_entries += 1
        context = {"stage": "eval", "entry_id": entry.id, "url": entry.url}
        try:
            meta, body = read_snapshot(snapshot_paths(fixtures_dir, entry))
        except SnapshotError as exc:
            # A missing meta file is the normal "never snapshotted" case; anything
            # else is a damaged snapshot worth a warning.
            if isinstance(exc.__cause__, FileNotFoundError):
                evaluation.not_snapshotted.append(entry.id)
            else:
                log.warning("snapshot unreadable", extra={**context, "error": str(exc)})
                evaluation.unreadable.append(entry.id)
            continue
        except ValueError as exc:
            log.warning("no snapshot path", extra={**context, "error": str(exc)})
            evaluation.unreadable.append(entry.id)
            continue
        if meta is None or body is None:
            evaluation.not_snapshotted.append(entry.id)
            continue

        html = _decode(body, meta.content_type)
        # The post-redirect URL is what a real run would hand the extractor, so
        # per-domain overrides and relative links resolve against that host.
        page_url = meta.final_url or entry.url
        for extractor in extractors:
            started = timer()
            result = _extract(extractor, html, entry, page_url)
            seconds = timer() - started
            evaluation.outcomes.append(
                Outcome(
                    entry_id=entry.id,
                    domain=entry_domain(entry),
                    paywalled=entry.paywalled,
                    extractor=extractor.name,
                    status=str(result.status),
                    words=result.word_count,
                    score=score_result(entry, result, boilerplate),
                    seconds=seconds,
                )
            )
    return evaluation
