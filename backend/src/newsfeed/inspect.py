"""Read-only, generic queries over stored articles and feedback (``newsfeed inspect``).

Functions take records or a ``Repository`` and return rows (dicts); rendering is separate.
Only ``list_articles``, ``get_article`` and ``list_feedback`` are called, never the BlobStore.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from newsfeed.extract.overrides import normalise_domain, url_domain
from newsfeed.models import Article, ExtractionStatus, Feedback, FeedbackEvent, RankingArm
from newsfeed.ports import Repository

_words = str.split  # field lists written as words, one line each
ARTICLE_FIELDS = _words(
    "id source_id domain title url published_at fetched_at score status method error_type "
    "error word_count attempts breakdown summary"
)
DEFAULT_ARTICLE_FIELDS = _words("score published_at source_id status word_count title")
ARTICLE_GROUP_KEYS = _words("source_id domain status method error_type breakdown")
FEEDBACK_FIELDS = _words("created_at event value article_id source_id title")
FEEDBACK_GROUP_KEYS = ["event", "source_id"]
FORMATS = ["text", "jsonl", "csv"]

_ASCENDING = set(ARTICLE_FIELDS) - {"score", "published_at", "fetched_at", "word_count", "attempts"}
_DURATION = re.compile(r"^(\d+)([mhd])$")
_UNITS = {"m": "minutes", "h": "hours", "d": "days"}
_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z")
_STATUSES = [s.value for s in ExtractionStatus]


class InspectError(ValueError):
    """An invalid option; the message names the valid choices."""


def parse_since(text: str, now: datetime | None = None) -> datetime:
    """Parse a duration (``90m``, ``24h``, ``7d``) or a date or datetime (UTC if naive)."""
    text = text.strip()
    if match := _DURATION.match(text):
        try:
            return (now or datetime.now(UTC)) - timedelta(**{_UNITS[match[2]]: int(match[1])})
        except OverflowError:
            raise InspectError(f"--since {text!r} reaches back too far; use less") from None
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    raise InspectError(
        f"invalid --since {text!r}: use a duration such as 90m, 24h or 7d, "
        "or a date or datetime such as 2026-11-01 or 2026-11-01T08:00:00"
    )


def check_choice(option: str, value: str, choices: Sequence[str]) -> str:
    """Return ``value`` if it is one of ``choices``, else raise naming the choices."""
    if value not in choices:
        raise InspectError(f"invalid {option} {value!r}: choose from {', '.join(choices)}")
    return value


def parse_fields(text: str | None, valid: Sequence[str], default: Sequence[str]) -> list[str]:
    """Split a comma-separated field list, rejecting unknown names."""
    names = [n.strip() for n in text.split(",") if n.strip()] if text is not None else default
    return [check_choice("--fields", n, valid) for n in names] or list(default)


def article_value(article: Article, field: str) -> Any:
    """The value of one listing field of ``article`` (never the body text)."""
    match field:
        case "domain":
            return url_domain(article.canonical_url)
        case "status":
            return article.extraction.status.value
        case "breakdown":
            return dict(article.score_breakdown)
        case "method" | "error_type" | "error" | "word_count" | "attempts":
            return getattr(article.extraction, field)
        case _:
            return getattr(article, field)


def _has_key(breakdown: dict[str, float], key: str) -> bool:
    return any(k.startswith(key) for k in breakdown) if key.endswith(":") else key in breakdown


def filter_articles(
    articles: Sequence[Article],
    *,
    sources: Sequence[str] = (),
    domains: Sequence[str] = (),
    statuses: Sequence[str] = (),
    min_score: float | None = None,
    max_score: float | None = None,
    has: Sequence[str] = (),
    lacks: Sequence[str] = (),
    title_match: str | None = None,
) -> list[Article]:
    """Keep the articles matching every given filter (a repeated one matches any value)."""
    for name, bound in (("--min-score", min_score), ("--max-score", max_score)):
        if bound is not None and not math.isfinite(bound):
            raise InspectError(f"{name} must be a finite number, got {bound}")
    if min_score is not None and max_score is not None and min_score > max_score:
        raise InspectError(f"--min-score {min_score} is above --max-score {max_score}")
    for status in statuses:
        check_choice("--status", status, _STATUSES)
    try:
        pattern = re.compile(title_match, re.IGNORECASE) if title_match is not None else None
    except re.error as exc:
        raise InspectError(f"invalid --title-match regex: {exc}") from exc
    wanted = {normalise_domain(d) for d in domains}
    scored = min_score is not None or max_score is not None

    def keep(a: Article) -> bool:
        s = a.score
        return (
            (not sources or a.source_id in sources)
            and (not wanted or url_domain(a.canonical_url) in wanted)
            and (not statuses or a.extraction.status.value in statuses)
            and (not scored or s is not None)
            and (min_score is None or (s is not None and s >= min_score))
            and (max_score is None or (s is not None and s <= max_score))
            and all(_has_key(a.score_breakdown, k) for k in has)
            and not any(_has_key(a.score_breakdown, k) for k in lacks)
            and (pattern is None or bool(pattern.search(a.title)))
        )

    return [a for a in articles if keep(a)]


def query_articles(
    repo: Repository, *, since: datetime, statuses: Sequence[str] = (), **filters: Any
) -> list[Article]:
    """Read articles fetched since ``since`` and apply the in-memory filters."""
    one = len(statuses) == 1 and statuses[0] in _STATUSES
    found = repo.list_articles(status=ExtractionStatus(statuses[0]) if one else None, since=since)
    return filter_articles(found, statuses=statuses, **filters)


def list_article_rows(
    articles: Sequence[Article], fields: Sequence[str], sort: str
) -> list[dict[str, Any]]:
    """Rows holding ``id``, the sort field and the chosen fields.

    Ordered by ``sort``: numbers and dates descending, text ascending, blanks last,
    ties broken by ``id``.
    """
    rows = [{f: article_value(a, f) for f in {*fields, sort, "id"}} for a in articles]
    rows.sort(key=lambda r: r["id"])
    present = [r for r in rows if r[sort] is not None]
    by = (lambda r: str(r[sort])) if sort == "breakdown" else (lambda r: r[sort])
    present.sort(key=by, reverse=sort not in _ASCENDING)
    return present + [r for r in rows if r[sort] is None]


def group_columns(key: str) -> list[str]:
    """Column names of a grouped articles table."""
    return [key, "count", *_STATUSES, "word_count_median", "score_mean", "score_median"]


def group_articles(articles: Sequence[Article], key: str) -> list[dict[str, Any]]:
    """One row per group: count, count per status, median words, mean and median score."""
    groups: dict[Any, list[Article]] = defaultdict(list)
    for a in articles:
        for k in a.score_breakdown if key == "breakdown" else [article_value(a, key)]:
            groups[k].append(a)
    out = []
    for name, members in groups.items():
        scores = [a.score for a in members if a.score is not None]
        out.append(
            {key: name, "count": len(members)}
            | {s: sum(a.extraction.status.value == s for a in members) for s in _STATUSES}
            | {
                "word_count_median": statistics.median(a.extraction.word_count for a in members),
                "score_mean": statistics.fmean(scores) if scores else None,
                "score_median": statistics.median(scores) if scores else None,
            }
        )
    return _largest_first(out, key)


def _largest_first(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: (-r["count"], str(r[key] if r[key] is not None else "")))


def query_feedback(
    repo: Repository, *, since: datetime, events: Sequence[str] = ()
) -> list[dict[str, Any]]:
    """Feedback rows newest first, each joined with its article's source and title."""
    for event in events:
        check_choice("--event", event, [e.value for e in FeedbackEvent])
    items = [f for f in repo.list_feedback(since=since) if not events or f.event in events]
    items.sort(key=lambda f: (f.created_at, f.id), reverse=True)
    articles = {i.article_id: repo.get_article(i.article_id) for i in items}
    return [
        {
            "created_at": f.created_at,
            "event": f.event.value,
            "value": f.value,
            "article_id": f.article_id,
            "source_id": a.source_id if (a := articles[f.article_id]) else None,
            "title": a.title if a else None,
        }
        for f in items
    ]


def group_feedback(rows: Sequence[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """Count feedback rows per ``key``, largest group first."""
    counts: dict[Any, int] = defaultdict(int)
    for row in rows:
        counts[row[key]] += 1
    return _largest_first([{key: k, "count": n} for k, n in counts.items()], key)


def _plain(value: Any) -> str:
    """A cell as text: blank for None, compact for floats, datetimes and breakdowns."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(timespec="minutes")
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    if isinstance(value, dict):
        return " ".join(f"{k}={_plain(v)}" for k, v in value.items())
    return " ".join(str(value).split())


def render(rows: Sequence[dict[str, Any]], columns: Sequence[str], fmt: str) -> str:
    """Render ``rows`` with exactly ``columns`` as an aligned table, JSON lines or CSV."""
    check_choice("--format", fmt, FORMATS)
    if fmt == "jsonl":
        as_json = lambda v: v.isoformat() if isinstance(v, datetime) else str(v)  # noqa: E731
        return "\n".join(json.dumps({c: r[c] for c in columns}, default=as_json) for r in rows)
    table = [list(columns), *([_plain(r[c]) for c in columns] for r in rows)]
    if fmt == "csv":
        buffer = io.StringIO()
        csv.writer(buffer, lineterminator="\n").writerows(table)
        return buffer.getvalue().rstrip("\n")
    widths = [max(len(row[i]) for row in table) for i in range(len(columns))]

    def pad(row: list[str]) -> str:
        return "  ".join(t.ljust(w) for t, w in zip(row, widths, strict=True)).rstrip()

    return "\n".join(pad(r) for r in table)


# --- A/B results (ADR-0015) -------------------------------------------------------

AB_COLUMNS = ["row", "rules", "personal", "share_personal", "ci_low", "ci_high", "p_value", "note"]


@dataclass(frozen=True)
class ArmCounts:
    """Credited events per arm, the personalised share and how far it is from chance."""

    rules: int
    personal: int
    share_personal: float | None
    ci_low: float | None
    ci_high: float | None
    p_value: float | None


@dataclass(frozen=True)
class AbSummary:
    """Everything ``newsfeed inspect ab`` reports; holds counts only, never article ids."""

    opens: ArmCounts
    reads: ArmCounts
    likes: dict[RankingArm, int]
    days: dict[date, tuple[int, int]] = field(default_factory=dict)  # day -> (rules, personal)
    days_won: tuple[int, int] = (0, 0)  # (rules, personal)
    opens_per_day: float = 0.0
    projected: date | None = None
    verdict: str = ""


def binomial_p_value(k: int, n: int) -> float | None:
    """Exact two-sided p-value of ``k`` successes in ``n`` fair trials (None when n is 0)."""
    if n == 0:
        return None
    observed = math.comb(n, k)
    # Two-sided: every outcome at most as likely as the observed one; the small
    # tolerance keeps ties (k and n-k) together; integers avoid float overflow.
    limit = observed * (10**9 + 1)
    tail: int = sum(c for i in range(n + 1) if (c := math.comb(n, i)) * 10**9 <= limit)
    return min(1.0, tail / (1 << n))


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson 95% interval for a share of ``k`` in ``n`` (None when n is 0)."""
    if n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _arm_counts(rules: int, personal: int) -> ArmCounts:
    n = rules + personal
    interval = wilson_interval(personal, n)
    return ArmCounts(
        rules=rules,
        personal=personal,
        share_personal=personal / n if n else None,
        ci_low=interval[0] if interval else None,
        ci_high=interval[1] if interval else None,
        p_value=binomial_p_value(personal, n),
    )


def _count_arms(arms: Sequence[RankingArm]) -> tuple[int, int]:
    return arms.count(RankingArm.RULES), arms.count(RankingArm.PERSONAL)


def ab_summary(
    events: Sequence[Feedback],
    *,
    now: datetime,
    since: datetime,
    min_credited: int = 200,
    min_days: int = 21,
    read_seconds: float = 30,
) -> AbSummary:
    """Count credited opens and reads per arm and judge them against ADR-0015's stopping rule."""
    open_seen: set[tuple[str, RankingArm]] = set()
    read_seen: dict[str, RankingArm] = {}
    like_seen: set[tuple[str, RankingArm]] = set()
    open_arms: list[tuple[date, RankingArm]] = []
    for e in sorted(events, key=lambda f: (f.created_at, f.id)):
        if e.arm is None or e.created_at < since:
            continue
        if e.event is FeedbackEvent.OPEN and (e.article_id, e.arm) not in open_seen:
            open_seen.add((e.article_id, e.arm))
            open_arms.append((e.created_at.astimezone(UTC).date(), e.arm))
        elif e.event is FeedbackEvent.READ and (e.value or 0) >= read_seconds:
            read_seen.setdefault(e.article_id, e.arm)
        elif e.event is FeedbackEvent.LIKE:
            like_seen.add((e.article_id, e.arm))

    opens = _arm_counts(*_count_arms([a for _, a in open_arms]))
    reads = _arm_counts(*_count_arms(list(read_seen.values())))
    likes = {arm: sum(1 for _, a in like_seen if a is arm) for arm in RankingArm}

    per_day: dict[date, list[RankingArm]] = defaultdict(list)
    for day, arm in open_arms:
        per_day[day].append(arm)
    days = {d: _count_arms(arms) for d, arms in sorted(per_day.items())}
    days_won = (
        sum(1 for r, p in days.values() if r > p),
        sum(1 for r, p in days.values() if p > r),
    )

    total = opens.rules + opens.personal
    rate = 0.0
    projected: date | None = None
    if days:
        today = now.astimezone(UTC).date()
        rate = total / ((today - min(days)).days + 1)  # calendar days since the first credited open
        wait_opens = math.ceil(max(0, min_credited - total) / rate)
        projected = today + timedelta(days=max(wait_opens, min_days - len(days), 0))

    if total < min_credited or len(days) < min_days:
        verdict = (
            f"collecting: {total} of {min_credited} credited opens, {len(days)} of {min_days} days"
        )
    elif opens.ci_low is not None and opens.ci_low > 0.5:
        verdict = "personalised ahead"
    elif opens.ci_high is not None and opens.ci_high < 0.5:
        verdict = "rules ahead"
    else:
        verdict = "no clear difference"
    return AbSummary(opens, reads, likes, days, days_won, rate, projected, verdict)


def _ab_row(name: str, rules: Any = None, personal: Any = None, **rest: Any) -> dict[str, Any]:
    return dict.fromkeys(AB_COLUMNS) | {"row": name, "rules": rules, "personal": personal} | rest


def _counts_row(name: str, c: ArmCounts) -> dict[str, Any]:
    return _ab_row(
        name,
        c.rules,
        c.personal,
        share_personal=c.share_personal,
        ci_low=c.ci_low,
        ci_high=c.ci_high,
        p_value=c.p_value,
    )


def ab_rows(summary: AbSummary) -> list[dict[str, Any]]:
    """The summary as table rows, one shape for the text, JSON lines and CSV outputs."""
    projected = summary.projected.isoformat() if summary.projected else "unknown"
    likes = summary.likes
    rows = [
        _counts_row("credited opens", summary.opens),
        _counts_row("credited reads", summary.reads),
        _ab_row("likes (not tested)", likes[RankingArm.RULES], likes[RankingArm.PERSONAL]),
        _ab_row("days won", *summary.days_won),
    ]
    rows += [_ab_row(f"day {d.isoformat()}", r, p) for d, (r, p) in summary.days.items()]
    rows.append(_ab_row("opens per day", note=f"{summary.opens_per_day:.1f}"))
    rows.append(_ab_row("stopping rule met by", note=projected))
    rows.append(_ab_row("verdict", note=summary.verdict))
    return rows
