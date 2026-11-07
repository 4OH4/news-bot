"""``newsfeed report``: failing feeds and failed articles by site and reason (NFR-5).

``build_report`` is a pure function from ``Repository`` data to a ``Report``;
``render_text`` and ``Report.model_dump_json`` present it. It reads stored data
only and fetches nothing, so it works the same against SQLite and Firestore.
Per-domain counts and medians come from the ``RunStats`` of the chosen runs;
failed articles come from the articles themselves, matched to the runs by when
they were last attempted (or first fetched, until attempts are
recorded). Article text is never included, only URLs.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict

from newsfeed.extract.overrides import url_domain
from newsfeed.metrics import reason_category
from newsfeed.models import Article, ExtractionStatus, Feedback, FeedbackEvent, RunStats
from newsfeed.ports import Repository

__all__ = [
    "DomainSummary",
    "FailedGroup",
    "FeedFailure",
    "FeedbackSummary",
    "Report",
    "RunTrend",
    "UnknownRunError",
    "build_report",
    "feedback_article_ids",
    "render_text",
    "summarise_feedback",
]

_PROBLEM_STATUSES = (ExtractionStatus.FAILED, ExtractionStatus.PARTIAL)


class UnknownRunError(LookupError):
    """The requested run id has no stored ``RunStats``."""


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class DomainSummary(_Out):
    """Extraction outcomes for one domain across the selected runs."""

    domain: str
    counts: dict[str, int]
    total: int
    ok_rate: float
    median_word_count: float | None = None


class RunTrend(_Out):
    """One selected run's ``ok`` rate per domain, for ``--since`` trends."""

    run_id: str
    started_at: datetime
    ok_rate_by_domain: dict[str, float]


class FeedFailure(_Out):
    """A source whose latest fetches keep failing."""

    source_id: str
    consecutive_failures: int
    last_error: str | None = None
    last_status: int | None = None
    last_success_at: datetime | None = None


class FailedGroup(_Out):
    """Failed or partial articles of one domain with one reason category."""

    domain: str
    reason: str
    status: str
    count: int
    urls: list[str]


_UNDOABLE = (FeedbackEvent.LIKE, FeedbackEvent.DISMISS, FeedbackEvent.MUTE_SOURCE)
_RECENT = 10
_UNKNOWN = "unknown"


@dataclass(frozen=True)
class DayFeedback:
    """Feedback counted for one UTC day; undone events are left out."""

    day: str
    counts: dict[str, int]
    articles_opened: int


@dataclass(frozen=True)
class RecentEvent:
    """One stored feedback event, as stored (undos included)."""

    at: datetime
    event: str
    value: float | None
    article_id: str
    title: str | None


@dataclass(frozen=True)
class MutedSource:
    """A source muted from the phone: a candidate for ``config/interests.yaml``."""

    source_id: str
    count: int


@dataclass(frozen=True)
class FeedbackSummary:
    """Reader feedback over the last days, for the Feedback section."""

    days: list[DayFeedback]
    recent: list[RecentEvent]
    muted: list[MutedSource]
    total: int


def _is_undo(f: Feedback) -> bool:
    return f.event in _UNDOABLE and f.value == 0


def _counted(ordered: Sequence[Feedback]) -> list[Feedback]:
    """Events still standing: an undo cancels the latest earlier event it undoes."""
    standing: dict[tuple[str, FeedbackEvent], list[Feedback]] = defaultdict(list)
    for f in ordered:
        stack = standing[(f.article_id, f.event)]
        if _is_undo(f):
            if stack:
                stack.pop()
        else:
            stack.append(f)
    return [f for stack in standing.values() for f in stack]


def _ordered(feedback: Sequence[Feedback]) -> list[Feedback]:
    return sorted(feedback, key=lambda f: (f.created_at, f.id))


def feedback_article_ids(feedback: Sequence[Feedback]) -> list[str]:
    """Ids of the articles to look up: those of the recent events and of mute events."""
    ordered = _ordered(feedback)
    ids = [f.article_id for f in ordered[-_RECENT:]]
    ids += [f.article_id for f in ordered if f.event == FeedbackEvent.MUTE_SOURCE]
    return list(dict.fromkeys(ids))


def summarise_feedback(
    feedback: Sequence[Feedback],
    articles_by_id: Mapping[str, Article],
    *,
    now: datetime,
    days: int = 14,
) -> FeedbackSummary:
    """Summarise ``feedback`` for the Feedback section; pure.

    Daily counts leave out undone events and cover the ``days`` UTC days ending
    with ``now``'s, zeros included so a gap in use shows.
    Muted sources use all the feedback given, because a later undo can be older
    than the window.
    """
    ordered = _ordered(feedback)
    today = now.astimezone(UTC).date()
    window = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    counts = {d: Counter[str]() for d in window}
    opened: dict[object, set[str]] = {d: set() for d in window}
    for f in _counted(ordered):
        day = f.created_at.astimezone(UTC).date()
        if day not in counts:
            continue
        counts[day][f.event.value] += 1
        if f.event == FeedbackEvent.OPEN:
            opened[day].add(f.article_id)
    day_rows = [
        DayFeedback(
            day=d.isoformat(),
            counts={e.value: counts[d][e.value] for e in FeedbackEvent},
            articles_opened=len(opened[d]),
        )
        for d in window
    ]
    recent = []
    for f in reversed(ordered[-_RECENT:]):
        article = articles_by_id.get(f.article_id)
        recent.append(
            RecentEvent(
                at=f.created_at,
                event=f.event.value,
                value=f.value,
                article_id=f.article_id,
                title=article.title if article else None,
            )
        )
    latest: dict[str, tuple[datetime, bool]] = {}
    mute_counts: Counter[str] = Counter()
    for f in ordered:
        if f.event != FeedbackEvent.MUTE_SOURCE:
            continue
        article = articles_by_id.get(f.article_id)
        source = article.source_id if article else _UNKNOWN
        latest[source] = (f.created_at, _is_undo(f))
        if not _is_undo(f):
            mute_counts[source] += 1
    muted = [MutedSource(s, n) for s, n in mute_counts.items() if not latest[s][1]]
    muted.sort(key=lambda m: (-latest[m.source_id][0].timestamp(), m.source_id))
    return FeedbackSummary(days=day_rows, recent=recent, muted=muted, total=len(ordered))


def _render_feedback(fb: FeedbackSummary) -> list[str]:
    lines = ["", "Feedback (UTC days; an undo cancels its event)"]
    if fb.total == 0:
        return [*lines, "  none"]
    for d in fb.days:
        counts = " ".join(f"{k} {v}" for k, v in d.counts.items())
        lines.append(f"  {d.day}: {counts}, articles opened {d.articles_opened}")
    lines += ["", "Recent feedback"]
    for r in fb.recent:
        value = "-" if r.value is None else f"{r.value:g}"
        what = r.title if r.title is not None else f"{r.article_id} (article not found)"
        lines.append(f"  {r.at:%Y-%m-%d %H:%M} {r.event} {value} {what}")
    lines += ["", "Muted sources, most recent first (candidates for config/interests.yaml)"]
    if not fb.muted:
        lines.append("  none")
    lines += [f"  {m.source_id}: {m.count} mute event(s)" for m in fb.muted]
    return lines


class Report(_Out):
    """Everything ``newsfeed report`` prints."""

    run_ids: list[str]
    domains: list[DomainSummary]
    trend: list[RunTrend]
    feeds: list[FeedFailure]
    failures: list[FailedGroup]
    feedback: FeedbackSummary | None = None


def _select_runs(repo: Repository, run_id: str | None, since: datetime | None) -> list[RunStats]:
    """Runs to report on, newest first: the given one, those since a date, or the latest."""
    if run_id is not None:
        stats = repo.get_run_stats(run_id)
        if stats is None:
            raise UnknownRunError(f"no run with id {run_id!r}")
        return [stats]
    if since is not None:
        return repo.list_run_stats(since=since)
    return repo.list_run_stats(limit=1)


def _domain_summaries(runs: list[RunStats]) -> list[DomainSummary]:
    counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    medians: defaultdict[str, list[float]] = defaultdict(list)
    for run in runs:
        for domain, by_status in run.extraction_by_domain.items():
            for status, n in by_status.items():
                counts[domain][ExtractionStatus(status).value] += n
        for domain, median in run.median_word_count_by_domain.items():
            medians[domain].append(median)
    summaries = []
    for domain, counter in counts.items():
        total = sum(counter.values())
        summaries.append(
            DomainSummary(
                domain=domain,
                counts=dict(sorted(counter.items())),
                total=total,
                ok_rate=counter["ok"] / total if total else 0.0,
                # Median of the runs' medians: the per-article counts aren't kept.
                median_word_count=statistics.median(medians[domain]) if medians[domain] else None,
            )
        )
    return sorted(summaries, key=lambda s: (s.ok_rate, s.domain))


def _trend(runs: list[RunStats]) -> list[RunTrend]:
    trend = []
    for run in sorted(runs, key=lambda r: (r.started_at, r.run_id)):
        rates = {}
        for domain, by_status in sorted(run.extraction_by_domain.items()):
            total = sum(by_status.values())
            if total:
                rates[domain] = by_status.get(ExtractionStatus.OK, 0) / total
        trend.append(
            RunTrend(run_id=run.run_id, started_at=run.started_at, ok_rate_by_domain=rates)
        )
    return trend


def _failed_groups(articles: Iterable[Article], runs: list[RunStats]) -> list[FailedGroup]:
    """Group articles last attempted during any of ``runs`` by domain, reason and status."""
    windows = [(r.started_at, r.finished_at) for r in runs]
    urls: defaultdict[tuple[str, str, str], list[str]] = defaultdict(list)
    for article in articles:
        # Nothing sets last_attempt_at yet (issue #88 will), so fall back to when
        # the article was first fetched, which is within the run that extracted it.
        attempted = article.extraction.last_attempt_at or article.fetched_at
        # A run still marked unfinished has no end, so it covers everything after its start.
        if not any(s <= attempted and (e is None or attempted <= e) for s, e in windows):
            continue
        reason = article.extraction.error_type or reason_category(article.extraction.error)
        key = (url_domain(article.url) or "unknown", reason, article.extraction.status.value)
        urls[key].append(article.url)
    groups = [
        FailedGroup(domain=d, reason=r, status=s, count=len(u), urls=sorted(u))
        for (d, r, s), u in urls.items()
    ]
    return sorted(groups, key=lambda g: (g.domain, -g.count, g.reason, g.status))


def _feedback_summary(
    repo: Repository, now: datetime, since: datetime | None = None
) -> FeedbackSummary:
    # With ``since`` the whole section, muted sources included, covers only events
    # from that time, so the report reads as one window.
    feedback = [f for f in repo.list_feedback() if since is None or f.created_at >= since]
    articles = {}
    for article_id in feedback_article_ids(feedback):
        article = repo.get_article(article_id)
        if article is not None:
            articles[article_id] = article
    return summarise_feedback(feedback, articles, now=now)


def build_report(
    repo: Repository,
    source_ids: Iterable[str],
    *,
    run_id: str | None = None,
    since: datetime | None = None,
    now: datetime | None = None,
) -> Report:
    """Summarise the latest run, run ``run_id``, or every run since ``since``.

    Raises ``UnknownRunError`` for an unknown ``run_id``. Feed health is the current
    state of ``source_ids`` whatever runs are chosen, because it is a running
    count rather than a per-run figure.
    """
    runs = _select_runs(repo, run_id, since)
    feedback = _feedback_summary(repo, now or datetime.now(UTC), since)
    feeds = []
    for source_id in source_ids:
        state = repo.get_source_state(source_id)
        if state is not None and state.consecutive_failures > 0:
            feeds.append(
                FeedFailure(
                    source_id=source_id,
                    consecutive_failures=state.consecutive_failures,
                    last_error=state.last_error,
                    last_status=state.last_status,
                    last_success_at=state.last_success_at,
                )
            )
    feeds.sort(key=lambda f: (-f.consecutive_failures, f.source_id))
    if not runs:
        return Report(run_ids=[], domains=[], trend=[], feeds=feeds, failures=[], feedback=feedback)
    # Filter on the last attempt, not fetched_at: a retried article was fetched earlier.
    problem_articles = [a for s in _PROBLEM_STATUSES for a in repo.list_articles(status=s)]
    return Report(
        run_ids=[r.run_id for r in runs],
        domains=_domain_summaries(runs),
        trend=_trend(runs) if len(runs) > 1 else [],
        feeds=feeds,
        failures=_failed_groups(problem_articles, runs),
        feedback=feedback,
    )


def render_text(report: Report) -> str:
    """Render ``report`` as plain text."""
    if report.run_ids:
        shown = ", ".join(report.run_ids[:3]) + (", ..." if len(report.run_ids) > 3 else "")
        lines = [f"Runs: {len(report.run_ids)} ({shown})"]
    else:
        lines = ["No runs found."]
    lines += ["", "Extraction by domain (lowest ok rate first)"]
    if not report.domains:
        lines.append("  none")
    for d in report.domains:
        counts = " ".join(f"{k}={v}" for k, v in d.counts.items())
        words = f", median words {d.median_word_count:.0f}" if d.median_word_count else ""
        lines.append(f"  {d.domain}: {d.ok_rate:.0%} ok of {d.total} ({counts}{words})")
    if report.trend:
        lines += ["", "Trend: ok rate per run, oldest first"]
        for t in report.trend:
            rates = ", ".join(f"{d} {r:.0%}" for d, r in t.ok_rate_by_domain.items())
            lines.append(f"  {t.started_at:%Y-%m-%d %H:%M} {t.run_id}: {rates}")
    lines += ["", "Failing feeds"]
    if not report.feeds:
        lines.append("  none")
    for f in report.feeds:
        lines.append(
            f"  {f.source_id}: {f.consecutive_failures} consecutive failure(s), "
            f"last error: {f.last_error or 'unknown'}"
        )
    lines += ["", "Failed and partial articles"]
    if not report.failures:
        lines.append("  none")
    for g in report.failures:
        lines.append(f"  {g.domain}: {g.reason} ({g.status}) x{g.count}")
        lines += [f"    {u}" for u in g.urls]
    if report.feedback is not None:
        lines += _render_feedback(report.feedback)
    return "\n".join(lines)
