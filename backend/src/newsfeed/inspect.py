"""Read-only queries over stored articles and feedback (``newsfeed inspect``).

Everything here is generic: it filters, sorts, groups and renders records by
their fields and knows nothing about particular sources, topics or domains.
The functions take records or a ``Repository`` and return rows (plain dicts);
rendering is separate. Only ``list_articles``, ``get_article`` and
``list_feedback`` are called, and no body text is read from the ``BlobStore``.
"""

from __future__ import annotations

import csv
import io
import json
import re
import statistics
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from newsfeed.extract.overrides import normalise_domain, url_domain
from newsfeed.models import Article, ExtractionStatus, Feedback, FeedbackEvent
from newsfeed.ports import Repository

ARTICLE_FIELDS = (
    "id",
    "source_id",
    "domain",
    "title",
    "url",
    "published_at",
    "fetched_at",
    "score",
    "status",
    "method",
    "error_type",
    "error",
    "word_count",
    "attempts",
    "breakdown",
    "summary",
)
DEFAULT_ARTICLE_FIELDS = ("score", "published_at", "source_id", "status", "word_count", "title")
ARTICLE_GROUP_KEYS = ("source_id", "domain", "status", "method", "error_type", "breakdown")
FEEDBACK_FIELDS = ("created_at", "event", "value", "article_id", "source_id", "title")
FEEDBACK_GROUP_KEYS = ("event", "source_id")
FORMATS = ("text", "jsonl", "csv")

_NUMERIC_FIELDS = frozenset({"score", "word_count", "attempts", "value"})
_DATE_FIELDS = frozenset({"published_at", "fetched_at", "created_at"})
_DURATION = re.compile(r"^(\d+)([mhd])$")
_DURATION_UNITS = {"m": "minutes", "h": "hours", "d": "days"}
_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z")
_STATUSES = tuple(s.value for s in ExtractionStatus)


class InspectError(ValueError):
    """An invalid option; the message names the valid choices."""


def parse_since(text: str, now: datetime | None = None) -> datetime:
    """Parse a duration (``90m``, ``24h``, ``7d``) or a date or datetime (UTC if naive)."""
    now = now or datetime.now(UTC)
    match = _DURATION.match(text.strip())
    if match:
        return now - timedelta(**{_DURATION_UNITS[match.group(2)]: int(match.group(1))})
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(text.strip(), fmt)
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
    if text is None:
        return list(default)
    names = [n.strip() for n in text.split(",") if n.strip()]
    if not names:
        raise InspectError(f"--fields is empty: choose from {', '.join(valid)}")
    for name in names:
        check_choice("--fields", name, valid)
    return names


# --- articles ----------------------------------------------------------------


def article_value(article: Article, field: str) -> Any:
    """The value of one listing field of ``article`` (never the body text)."""
    extraction = article.extraction
    match field:
        case "domain":
            return url_domain(article.canonical_url)
        case "status":
            return extraction.status.value
        case "method":
            return extraction.method
        case "error_type":
            return extraction.error_type
        case "error":
            return extraction.error
        case "word_count":
            return extraction.word_count
        case "attempts":
            return extraction.attempts
        case "breakdown":
            return dict(article.score_breakdown)
        case _:
            return getattr(article, field)


def _has_key(breakdown: dict[str, float], key: str) -> bool:
    if key.endswith(":"):
        return any(k.startswith(key) for k in breakdown)
    return key in breakdown


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
    if min_score is not None and max_score is not None and min_score > max_score:
        raise InspectError(f"--min-score {min_score} is above --max-score {max_score}")
    for status in statuses:
        check_choice("--status", status, _STATUSES)
    pattern = None
    if title_match is not None:
        try:
            pattern = re.compile(title_match, re.IGNORECASE)
        except re.error as exc:
            raise InspectError(f"invalid --title-match regex: {exc}") from exc
    wanted_domains = {normalise_domain(d) for d in domains}

    def keep(a: Article) -> bool:
        if sources and a.source_id not in sources:
            return False
        if wanted_domains and url_domain(a.canonical_url) not in wanted_domains:
            return False
        if statuses and a.extraction.status.value not in statuses:
            return False
        if min_score is not None or max_score is not None:
            if a.score is None:
                return False
            if min_score is not None and a.score < min_score:
                return False
            if max_score is not None and a.score > max_score:
                return False
        if any(not _has_key(a.score_breakdown, k) for k in has):
            return False
        if any(_has_key(a.score_breakdown, k) for k in lacks):
            return False
        return not (pattern is not None and not pattern.search(a.title))

    return [a for a in articles if keep(a)]


def sort_rows(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    """Order by ``field``: numbers and dates descending, text ascending, blanks last.

    Ties are broken by ``id`` (or ``article_id``/``created_at`` order for feedback) so
    the output is stable.
    """
    descending = field in _NUMERIC_FIELDS or field in _DATE_FIELDS
    tie = "id" if rows and "id" in rows[0] else None
    ordered = sorted(rows, key=lambda r: str(r[tie])) if tie else list(rows)

    def sort_key(row: dict[str, Any]) -> Any:
        value = row[field]
        return str(value) if isinstance(value, dict) else value

    present = [r for r in ordered if r[field] is not None]
    blank = [r for r in ordered if r[field] is None]
    present.sort(key=sort_key, reverse=descending)
    return present + blank


def list_article_rows(
    articles: Sequence[Article], fields: Sequence[str], sort: str
) -> list[dict[str, Any]]:
    """One row per article holding the sort field, ``id`` and the chosen fields."""
    wanted = {*fields, sort, "id"}
    return sort_rows([{f: article_value(a, f) for f in wanted} for a in articles], sort)


def _stats(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    return statistics.fmean(values), statistics.median(values)


def group_articles(articles: Sequence[Article], key: str) -> list[dict[str, Any]]:
    """One row per group: count, count per status, median words, mean and median score."""
    groups: dict[Any, list[Article]] = defaultdict(list)
    for a in articles:
        if key == "breakdown":
            for k in a.score_breakdown:
                groups[k].append(a)
        else:
            groups[article_value(a, key)].append(a)
    out = []
    for group_key, members in groups.items():
        row: dict[str, Any] = {key: group_key, "count": len(members)}
        for status in _STATUSES:
            row[status] = sum(1 for a in members if a.extraction.status.value == status)
        row["word_count_median"] = statistics.median(a.extraction.word_count for a in members)
        row["score_mean"], row["score_median"] = _stats(
            [a.score for a in members if a.score is not None]
        )
        out.append(row)
    out.sort(key=lambda r: (-r["count"], str(r[key] if r[key] is not None else "")))
    return out


def group_columns(key: str) -> list[str]:
    """Column names of a grouped articles table."""
    return [key, "count", *_STATUSES, "word_count_median", "score_mean", "score_median"]


def query_articles(
    repo: Repository,
    *,
    since: datetime,
    statuses: Sequence[str] = (),
    **filters: Any,
) -> list[Article]:
    """Read articles fetched since ``since`` and apply the in-memory filters."""
    for status in statuses:
        check_choice("--status", status, _STATUSES)
    only = ExtractionStatus(statuses[0]) if len(statuses) == 1 else None
    found = repo.list_articles(status=only, since=since)
    return filter_articles(found, statuses=statuses, **filters)


# --- feedback ----------------------------------------------------------------


def query_feedback(
    repo: Repository, *, since: datetime, events: Sequence[str] = ()
) -> list[dict[str, Any]]:
    """Feedback rows newest first, each joined with its article's source and title."""
    for event in events:
        check_choice("--event", event, [e.value for e in FeedbackEvent])
    items: list[Feedback] = [
        f for f in repo.list_feedback(since=since) if not events or f.event.value in events
    ]
    items.sort(key=lambda f: f.id)
    items.sort(key=lambda f: f.created_at, reverse=True)
    cache: dict[str, Article | None] = {}
    rows = []
    for f in items:
        if f.article_id not in cache:
            cache[f.article_id] = repo.get_article(f.article_id)
        article = cache[f.article_id]
        rows.append(
            {
                "created_at": f.created_at,
                "event": f.event.value,
                "value": f.value,
                "article_id": f.article_id,
                "source_id": article.source_id if article else None,
                "title": article.title if article else None,
            }
        )
    return rows


def group_feedback(rows: Sequence[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """Count feedback rows per ``key``, largest group first."""
    counts: dict[Any, int] = defaultdict(int)
    for row in rows:
        counts[row[key]] += 1
    out = [{key: k, "count": n} for k, n in counts.items()]
    out.sort(key=lambda r: (-r["count"], str(r[key] if r[key] is not None else "")))
    return out


# --- rendering ---------------------------------------------------------------


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


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def render(rows: Sequence[dict[str, Any]], columns: Sequence[str], fmt: str) -> str:
    """Render ``rows`` with exactly ``columns`` as an aligned table, JSON lines or CSV."""
    check_choice("--format", fmt, FORMATS)
    if fmt == "jsonl":
        lines = [json.dumps({c: r[c] for c in columns}, default=_json_default) for r in rows]
        return "\n".join(lines)
    cells = [[_plain(r[c]) for c in columns] for r in rows]
    if fmt == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(cells)
        return buffer.getvalue().rstrip("\n")
    widths = [max([len(c), *(len(row[i]) for row in cells)]) for i, c in enumerate(columns)]
    table = [list(columns), *cells]
    return "\n".join(
        "  ".join(t.ljust(w) for t, w in zip(row, widths, strict=True)).rstrip() for row in table
    )
