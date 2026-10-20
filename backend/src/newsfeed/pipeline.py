"""The pipeline stages that make up ``newsfeed run``: ingest, extract, then rank.

Stages talk to each other only through the ``Repository`` and ``BlobStore``
(CLAUDE.md, rule 3): ingest leaves ``pending`` articles behind and extract picks
them up, so either can be run alone. Errors are caught at the stage boundary
(per source, per article), logged as structured warnings, counted in
``RunStats.error_count`` and never fail the run.
"""

from __future__ import annotations

import codecs
import re
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TypedDict
from urllib.parse import urlsplit

from newsfeed.extract.chain import ExtractorChain
from newsfeed.extract.overrides import Mode, url_domain
from newsfeed.extract.retry import should_retry
from newsfeed.ingest.canonical import canonical_from_html, normalise_url, with_canonical_url
from newsfeed.ingest.dedupe import dedupe
from newsfeed.logging import get_logger
from newsfeed.metrics import ExtractionRecord, FeedRecord, reason_category, summarise_run
from newsfeed.models import (
    Article,
    ArticleExtraction,
    ExtractionResult,
    ExtractionStatus,
    PipelineStage,
    RunStats,
    Source,
    SourceState,
    article_id,
)
from newsfeed.ports import Extractor, Fetcher
from newsfeed.publish.feed import FEED_WINDOW_HOURS, publish_feed
from newsfeed.rank.interests import InterestsConfig
from newsfeed.rank.stage import DEFAULT_WINDOW_HOURS, rank_recent
from newsfeed.wiring import Services

__all__ = [
    "AD_HOC_SOURCE_ID",
    "FetchedPage",
    "NotAnArticleError",
    "PageUnavailableError",
    "decode_html",
    "extract_pending",
    "extract_saved_url",
    "fetch_page",
    "ingest_sources",
    "inspect_url",
    "publish_stage",
    "run_pipeline",
]

log = get_logger(__name__)

AD_HOC_SOURCE_ID = "adhoc"
"""Source id of articles saved with ``newsfeed extract --url ... --save``."""

_HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.I)
_CONTENT_TYPE_CHARSET = re.compile(r"charset\s*=\s*[\"']?([A-Za-z0-9_.:-]+)", re.I)
_SNIFF_BYTES = 4096


# --- Errors and small helpers --------------------------------------------------


class PageUnavailableError(Exception):
    """A page couldn't be fetched (network error, robots.txt, or an HTTP error status).

    ``status_code`` is the HTTP status when the server answered with an error,
    otherwise None.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class NotAnArticleError(Exception):
    """A fetched URL isn't an HTML page worth extracting (e.g. a PDF)."""


@dataclass(frozen=True)
class FetchedPage:
    """A fetched HTML page, decoded."""

    html: str
    final_url: str


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _warn(
    message: str,
    *,
    stage: PipelineStage,
    error_type: str,
    error: str,
    source_id: str | None = None,
    url: str | None = None,
) -> None:
    """Log a caught error with the fields the issue requires."""
    log.warning(
        message,
        extra={
            "stage": stage.value,
            "source_id": source_id,
            "url": url,
            "error_type": error_type,
            "error": error,
        },
    )


def _codec(name: str) -> str | None:
    try:
        return codecs.lookup(name.strip()).name
    except LookupError:
        return None


def decode_html(content: bytes, headers: dict[str, str] | None = None) -> str:
    """Decode a fetched page: Content-Type charset, then ``<meta charset>``, then a fallback.

    A declared charset is trusted: if no candidate decodes the page cleanly,
    the first declared one (UTF-8 if it was ASCII) is applied with undecodable
    bytes replaced, so one bad byte costs one character, not the whole page.
    Windows-1252 (the browsers' default for undeclared legacy pages) is only
    the fallback for pages that declare nothing and aren't valid UTF-8. A BOM
    wins over everything.
    """
    if content.startswith(codecs.BOM_UTF8):
        return content[len(codecs.BOM_UTF8) :].decode("utf-8", errors="replace")
    candidates: list[str] = []
    header_match = _CONTENT_TYPE_CHARSET.search((headers or {}).get("content-type", ""))
    if header_match:
        candidates.append(header_match.group(1))
    meta_match = _META_CHARSET.search(content[:_SNIFF_BYTES])
    if meta_match:
        candidates.append(meta_match.group(1).decode("ascii", errors="ignore"))
    declared = [codec for name in candidates if (codec := _codec(name)) is not None]
    for codec in [*declared, "utf-8"]:
        try:
            return content.decode(codec)
        except UnicodeDecodeError:
            continue
    if declared:
        # A page that says it is ASCII but isn't is far more likely UTF-8.
        codec = "utf-8" if declared[0] == "ascii" else declared[0]
        return content.decode(codec, errors="replace")
    return content.decode("cp1252", errors="replace")


def fetch_page(fetcher: Fetcher, url: str) -> FetchedPage:
    """Fetch ``url`` through the polite fetcher and decode it.

    Raises:
        PageUnavailableError: on a network error, robots.txt refusal or HTTP error status.
        NotAnArticleError: if the response is not HTML.
    """
    result = fetcher.fetch(url)
    # The fetcher reports an HTTP error status both as ``error`` and as ``status_code``.
    error_status = result.status_code if (result.status_code or 0) >= 400 else None
    if result.error is not None or result.status_code is None:
        raise PageUnavailableError(result.error or "no response", status_code=error_status)
    if error_status is not None:
        raise PageUnavailableError(f"HTTP {error_status}", status_code=error_status)
    content_type = result.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type and content_type not in _HTML_TYPES:
        raise NotAnArticleError(f"not an HTML page (content-type {content_type})")
    return FetchedPage(html=decode_html(result.content, result.headers), final_url=result.final_url)


def _skip_reason(url: str) -> str | None:
    """Why ``url`` is never worth fetching for article content, or None.

    Hacker News item pages are discussion threads: text posts (Ask HN) have no
    article behind them, and the feed summary already carries the link.
    """
    parts = urlsplit(url)
    if (parts.hostname or "").lower() == "news.ycombinator.com" and parts.path == "/item":
        return "Hacker News discussion page; keeping the feed summary"
    return None


# --- Run statistics --------------------------------------------------------------


class RunTally:
    """Thread-safe counters that become ``RunStats``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: defaultdict[PipelineStage, Counter[str]] = defaultdict(Counter)
        self._domains: defaultdict[str, Counter[ExtractionStatus]] = defaultdict(Counter)
        self._errors = 0
        self._extractions: list[ExtractionRecord] = []
        self._feeds: dict[str, FeedRecord] = {}

    def count(self, stage: PipelineStage, name: str, n: int = 1) -> None:
        with self._lock:
            self._counts[stage][name] += n

    def extraction(self, record: ExtractionRecord) -> None:
        """Count one processed article per domain; failure reasons come from the records."""
        with self._lock:
            self._domains[record.domain][record.status] += 1
            self._extractions.append(record)

    def feed(self, source_id: str, record: FeedRecord) -> None:
        with self._lock:
            self._feeds[source_id] = record

    def summary(self, run_id: str, durations: dict[str, float]) -> dict[str, object]:
        """The ``run finished`` metrics (see ``newsfeed.metrics``)."""
        with self._lock:
            return summarise_run(run_id, durations, list(self._extractions), dict(self._feeds))

    def error(self) -> None:
        with self._lock:
            self._errors += 1

    def stats(
        self,
        run_id: str,
        started_at: datetime,
        finished_at: datetime | None,
        durations: Mapping[str, float] | None = None,
    ) -> RunStats:
        """The run's ``RunStats``; ``durations`` maps stage names to seconds."""
        with self._lock:
            summary = summarise_run(run_id, {}, list(self._extractions), dict(self._feeds))
            return RunStats(
                run_id=run_id,
                started_at=started_at,
                finished_at=finished_at,
                stage_counts={stage: dict(c) for stage, c in self._counts.items()},
                extraction_by_domain={d: dict(c) for d, c in self._domains.items()},
                error_count=self._errors,
                stage_durations_ms={
                    PipelineStage(stage): round(seconds * 1000)
                    for stage, seconds in (durations or {}).items()
                },
                failures_by_reason=summary["failures_by_reason"],
                median_word_count_by_domain={
                    domain: w["median"] for domain, w in summary["words_by_domain"].items()
                },
            )


# --- Ingest ----------------------------------------------------------------------


def _known_ids(services: Services, since: datetime) -> set[str]:
    """Ids of articles fetched at or after ``since``, plus their first-seen URL ids.

    Reads only that window rather than the whole history, so the read count per
    run (billed on Firestore) stays flat as the store grows: about one read per
    article fetched in the last ``dedupe_window_days`` days. An entry older than
    the window is treated as new; ``_store_new`` keeps that from clobbering the
    stored article.

    Extract may refine an article's canonical URL, which changes its id. The
    feed still lists the original URL, so without the second set the same
    entry would be ingested, fetched and merged again on every run.
    """
    known: set[str] = set()
    for article in services.repository.list_articles(since=since):
        known.add(article.id)
        try:
            known.add(article_id(normalise_url(article.url, rules=services.url_rules)))
        except ValueError:
            continue
    return known


def _store_new(services: Services, articles: Sequence[Article]) -> list[Article]:
    """Upsert ``articles`` that aren't stored yet; return those actually stored.

    The known-id set only covers the dedupe window, so an old entry that
    reappears looks new. One ``get_article`` per candidate (a few per run)
    stops its upsert replacing a stored article's content, score or extraction.
    """
    stored: list[Article] = []
    for article in articles:
        if services.repository.get_article(article.id) is not None:
            continue
        services.repository.upsert_article(article)
        stored.append(article)
    return stored


def _canonicalise(
    services: Services, articles: Sequence[Article], tally: RunTally
) -> list[Article]:
    """Give each article its normalised canonical URL; skip entries with unusable URLs."""
    result: list[Article] = []
    for article in articles:
        try:
            result.append(
                with_canonical_url(article, normalise_url(article.url, rules=services.url_rules))
            )
        except ValueError as exc:
            tally.error()
            _warn(
                "skipping entry with an unusable URL",
                stage=PipelineStage.INGEST,
                source_id=article.source_id,
                url=article.url,
                error_type=type(exc).__name__,
                error=str(exc),
            )
    return result


def _record_feed_failure(services: Services, state: SourceState, error: str) -> None:
    """Persist one more consecutive failure; the conditional-GET fields stay as they were."""
    try:
        services.repository.save_source_state(
            state.model_copy(
                update={
                    "consecutive_failures": state.consecutive_failures + 1,
                    "last_error": error[:500],
                }
            )
        )
    except Exception as exc:  # the repository itself is failing; nothing more to do
        _warn(
            "couldn't record the feed failure",
            stage=PipelineStage.INGEST,
            source_id=state.source_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )


def _ingest_source(
    services: Services,
    source: Source,
    known: set[str],
    tally: RunTally,
    *,
    since: datetime | None,
    limit: int | None,
) -> None:
    stage = PipelineStage.INGEST
    ingester = services.ingesters.get(source.type)
    if ingester is None:
        log.warning(
            "no ingester for this source type; skipping",
            extra={"stage": stage.value, "source_id": source.id, "source_type": source.type.value},
        )
        tally.count(stage, "sources_skipped")
        tally.feed(source.id, FeedRecord("skipped", reason="no ingester for this source type"))
        return
    state = services.repository.get_source_state(source.id) or SourceState(source_id=source.id)
    try:
        articles, new_state = ingester.ingest(source, services.fetcher, state)
        entries = _canonicalise(services, articles, tally)
        in_range = entries
        if since is not None:
            in_range = [a for a in entries if a.published_at is None or a.published_at >= since]
        fresh = dedupe(in_range, known)
        dropped = len(in_range) < len(entries) or (limit is not None and len(fresh) > limit)
        if limit is not None:
            fresh = fresh[:limit]
        stored = _store_new(services, fresh)
        # The state goes last: if storing the articles failed, the next run must
        # not get a 304 for entries we never kept. The same goes for entries that
        # --since or --limit dropped: the old ETag lets a later run see them.
        # Health is set here, after the ingesters' 304 check below compares the
        # states, so it can't make a 304 look like a fresh fetch.
        not_modified = new_state == state
        fetched_at = _utc_now()
        healthy = (new_state if not dropped else state).model_copy(
            update={"consecutive_failures": 0, "last_error": None, "last_success_at": fetched_at}
        )
        services.repository.save_source_state(healthy)
    except Exception as exc:
        tally.error()
        tally.count(stage, "sources_failed")
        error = f"{type(exc).__name__}: {exc}"
        _record_feed_failure(services, state, error)
        tally.feed(
            source.id,
            FeedRecord(
                "failed",
                http_status=getattr(exc, "status_code", None),
                reason=error[:200],
            ),
        )
        _warn(
            "source failed; continuing with the next",
            stage=stage,
            source_id=source.id,
            url=source.url,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return
    known.update(a.id for a in fresh)
    tally.count(stage, "sources")
    tally.count(stage, "entries", len(articles))
    tally.count(stage, "duplicates", len(entries) - len(stored))
    tally.count(stage, "new", len(stored))
    tally.feed(
        source.id,
        FeedRecord(
            "not_modified" if not_modified else "ok",
            http_status=304 if not_modified else new_state.last_status,
            new=len(stored),
        ),
    )
    log.info(
        "source ingested",
        extra={"stage": stage.value, "source_id": source.id, "new": len(stored)},
    )


def ingest_sources(
    services: Services,
    sources: Sequence[Source],
    tally: RunTally,
    *,
    since: datetime | None = None,
    limit: int | None = None,
) -> None:
    """Ingest each source: canonicalise, dedupe against the repository, store pending articles.

    ``since`` drops entries published before it (entries without a date are
    kept); ``limit`` keeps at most that many new articles per source. The
    dedupe lookup covers articles fetched within ``services.dedupe_window_days``,
    widened to reach back to ``since`` if that is older.
    """
    window_start = _utc_now() - timedelta(days=services.dedupe_window_days)
    known = _known_ids(services, min(window_start, since) if since else window_start)
    log.info(
        "known articles loaded",
        extra={"stage": PipelineStage.INGEST.value, "known_ids": len(known)},
    )
    for source in sources:
        _ingest_source(services, source, known, tally, since=since, limit=limit)


# --- Extract ---------------------------------------------------------------------


def _error_type(status: ExtractionStatus, error: str | None) -> str | None:
    """The ``error_type`` category stored for a result: set only for failures and paywalls."""
    if status is ExtractionStatus.FAILED:
        return reason_category(error)
    if status is ExtractionStatus.PAYWALLED:
        return "paywall"
    return None


def _with_changes(article: Article, **changes: object) -> Article:
    """Rebuild ``article`` with ``changes``, validated (``model_copy`` would skip validation)."""
    return Article.model_validate({**article.model_dump(), **changes})


class _Attempt(TypedDict):
    attempts: int
    last_attempt_at: datetime


def _attempt(article: Article, now: datetime) -> _Attempt:
    """The ``attempts`` and ``last_attempt_at`` to record for an attempt made at ``now``."""
    return {"attempts": article.extraction.attempts + 1, "last_attempt_at": now}


def _merge(
    article: Article, result: ExtractionResult, content_ref: str | None, now: datetime
) -> Article:
    """Fold an extraction result into the article.

    Feed-provided fields win: the feed's ``published_at`` usually has a real
    time and offset, while the extracted date is often date-only.
    """
    title = article.title
    if (not title or title == article.url) and result.title:
        title = result.title
    return _with_changes(
        article,
        title=title,
        author=article.author or result.author,
        published_at=article.published_at or result.published_at,
        lead_image=article.lead_image or result.lead_image,
        content_ref=content_ref or article.content_ref,
        extraction=ArticleExtraction(
            status=result.status,
            method=result.method,
            error=result.error,
            word_count=result.word_count,
            error_type=_error_type(result.status, result.error),
            **_attempt(article, now),
        ),
    )


@dataclass(frozen=True)
class _ExtractRun:
    services: Services
    tally: RunTally
    # Serialises the check-then-store in _refine_canonical across worker threads.
    refine_lock: threading.Lock = field(default_factory=threading.Lock)
    clock: Callable[[], datetime] = _utc_now
    # Old id -> the id it now lives under, for articles re-keyed or merged by
    # canonical refinement in this run. Written under ``refine_lock``.
    rekeyed: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _Outcome:
    """How one article's extraction ended; logged exactly once by ``_extract_safely``."""

    article: Article
    domain: str
    status: str  # an ExtractionStatus value, or "merged" for a duplicate dropped at refinement
    method: str | None = None
    word_count: int = 0
    error: str | None = None
    attempts: tuple[tuple[str, str], ...] = ()


def _log_outcome(outcome: _Outcome, elapsed_ms: int) -> None:
    """The one ``extraction finished`` entry per article (NFR-5)."""
    log.info(
        "extraction finished",
        extra={
            "stage": PipelineStage.EXTRACT.value,
            "source_id": outcome.article.source_id,
            "url": outcome.article.url,
            "domain": outcome.domain,
            "status": outcome.status,
            "method": outcome.method,
            "word_count": outcome.word_count,
            "elapsed_ms": elapsed_ms,
            "chain": [{"method": m, "status": s} for m, s in outcome.attempts],
            "error": outcome.error,
        },
    )


def _finish_skipped(run: _ExtractRun, article: Article, reason: str, domain: str) -> _Outcome:
    run.services.repository.upsert_article(
        _with_changes(
            article,
            extraction=ArticleExtraction(
                status=ExtractionStatus.SKIPPED, error=reason, **_attempt(article, run.clock())
            ),
        )
    )
    run.tally.extraction(ExtractionRecord(domain, ExtractionStatus.SKIPPED, error=reason))
    run.tally.count(PipelineStage.EXTRACT, ExtractionStatus.SKIPPED.value)
    return _Outcome(article, domain, ExtractionStatus.SKIPPED.value, error=reason)


def _finish_failed(
    run: _ExtractRun, article: Article, error_type: str, error: str, domain: str | None = None
) -> _Outcome:
    """Record a failure on the article and in the run, and log it."""
    domain = domain or url_domain(article.url) or "unknown"
    _warn(
        "extraction failed",
        stage=PipelineStage.EXTRACT,
        source_id=article.source_id,
        url=article.url,
        error_type=error_type,
        error=error,
    )
    run.tally.error()
    run.tally.extraction(ExtractionRecord(domain, ExtractionStatus.FAILED, error=error))
    run.tally.count(PipelineStage.EXTRACT, ExtractionStatus.FAILED.value)
    try:
        run.services.repository.upsert_article(
            _with_changes(
                article,
                extraction=ArticleExtraction(
                    status=ExtractionStatus.FAILED,
                    error=f"{error_type}: {error}"[:500],
                    error_type=reason_category(error),
                    **_attempt(article, run.clock()),
                ),
            )
        )
    except Exception as exc:  # the repository itself is failing; nothing more to do
        _warn(
            "couldn't record the failure",
            stage=PipelineStage.EXTRACT,
            source_id=article.source_id,
            url=article.url,
            error_type=type(exc).__name__,
            error=str(exc),
        )
    return _Outcome(article, domain, ExtractionStatus.FAILED.value, error=error)


def _summary_only(extractor: Extractor, url: str) -> bool:
    if not isinstance(extractor, ExtractorChain):
        return False
    override = extractor.override_for(url)
    return override is not None and override.mode is Mode.SUMMARY_ONLY


def _paywall_refusal(extractor: Extractor, url: str, status_code: int | None) -> str | None:
    """Why an HTTP error from ``url`` means a paywall (ADR-0003), or None if it doesn't.

    402 Payment Required always does. 403 only does on a domain configured
    ``paywall: true``; elsewhere it may be bot blocking, which is a failure.
    """
    if status_code == 402:
        return "HTTP 402 Payment Required"
    if status_code == 403 and isinstance(extractor, ExtractorChain):
        override = extractor.override_for(url)
        if override is not None and override.paywall:
            return f"HTTP 403 from {url_domain(url)}, configured paywall: true"
    return None


def _finish_paywalled(run: _ExtractRun, article: Article, reason: str, domain: str) -> _Outcome:
    """Record a page refused for want of a subscription; not an error (rule 6)."""
    run.services.repository.upsert_article(
        _with_changes(
            article,
            extraction=ArticleExtraction(
                status=ExtractionStatus.PAYWALLED,
                error=reason,
                error_type="paywall",
                **_attempt(article, run.clock()),
            ),
        )
    )
    run.tally.extraction(ExtractionRecord(domain, ExtractionStatus.PAYWALLED, error=reason))
    run.tally.count(PipelineStage.EXTRACT, ExtractionStatus.PAYWALLED.value)
    return _Outcome(article, domain, ExtractionStatus.PAYWALLED.value, error=reason)


def _extract_article(run: _ExtractRun, article: Article) -> _Outcome:
    services = run.services
    domain = url_domain(article.url) or "unknown"
    reason = _skip_reason(article.url)
    if reason is None and _summary_only(services.extractor, article.url):
        reason = f"extraction skipped: {domain} is configured summary_only"
    if reason is not None:
        return _finish_skipped(run, article, reason, domain)

    try:
        page = fetch_page(services.fetcher, article.url)
    except NotAnArticleError as exc:
        return _finish_skipped(run, article, str(exc), domain)
    except PageUnavailableError as exc:
        paywall = _paywall_refusal(services.extractor, article.url, exc.status_code)
        if paywall is not None:
            return _finish_paywalled(run, article, paywall, domain)
        return _finish_failed(run, article, type(exc).__name__, str(exc), domain)

    # After redirects the domain may differ; per-domain behaviour (overrides,
    # stats) follows the page we actually got.
    domain = url_domain(page.final_url) or domain
    refined = _refine_canonical(run, article, page)
    if refined is None:
        return _Outcome(article, domain, "merged", error="duplicate of a stored canonical URL")

    try:
        return _extract_content(run, refined, page, domain)
    except Exception as exc:
        # Report against the refined article: the original id is already retired.
        return _finish_failed(run, refined, type(exc).__name__, str(exc), domain)


def _extract_content(
    run: _ExtractRun, article: Article, page: FetchedPage, domain: str
) -> _Outcome:
    services = run.services
    result = services.extractor.extract(page.html, page.final_url)
    content_ref = None
    if result.content_html:
        # Blob first, article second: a crash in between leaves an orphan blob,
        # not an article pointing at nothing (ports.py).
        content_ref = services.blobs.put(
            f"articles/{article.id}.html",
            result.content_html.encode("utf-8"),
            "text/html; charset=utf-8",
        )
    services.repository.upsert_article(_merge(article, result, content_ref, run.clock()))
    run.tally.extraction(
        ExtractionRecord(domain, result.status, word_count=result.word_count, error=result.error)
    )
    run.tally.count(PipelineStage.EXTRACT, result.status.value)
    if result.status is ExtractionStatus.FAILED:
        _warn(
            "extraction failed",
            stage=PipelineStage.EXTRACT,
            source_id=article.source_id,
            url=article.url,
            error_type="ExtractionFailed",
            error=result.error or "unknown error",
        )
        run.tally.error()
    return _Outcome(
        article,
        domain,
        result.status.value,
        method=result.method,
        word_count=result.word_count,
        error=result.error,
        attempts=tuple((a.method, a.status.value) for a in result.attempts),
    )


def _refine_canonical(run: _ExtractRun, article: Article, page: FetchedPage) -> Article | None:
    """Re-key the article if the page declares a different canonical URL.

    Returns the article to carry on with (a new object when its id changed), or
    None if the page's canonical already exists as another article, in which
    case the old one is deleted and there is nothing left to extract.
    """
    services = run.services
    refined = canonical_from_html(page.html, page.final_url, rules=services.url_rules)
    if refined is None or refined == article.canonical_url:
        return article
    new = with_canonical_url(article, refined)
    log.info(
        "canonical URL refined",
        extra={
            "stage": "extract",
            "source_id": article.source_id,
            "url": article.url,
            "canonical": refined,
            "old_id": article.id,
            "new_id": new.id,
        },
    )
    with run.refine_lock:
        run.rekeyed[article.id] = new.id
        if services.repository.get_article(new.id) is not None:
            services.repository.delete_article(article.id)
            run.tally.count(PipelineStage.EXTRACT, "merged")
            return None
        # Store the new id before retiring the old one, so a crash in between
        # leaves a duplicate that the next run merges, never a lost article.
        services.repository.upsert_article(new)
        services.repository.delete_article(article.id)
    run.tally.count(PipelineStage.EXTRACT, "refined")
    return new


def _extract_safely(run: _ExtractRun, article: Article) -> None:
    """Process one article; whatever goes wrong is recorded on it and never raised."""
    started = time.perf_counter()
    try:
        outcome = _extract_article(run, article)
    except Exception as exc:
        outcome = _finish_failed(run, article, type(exc).__name__, str(exc))
    _log_outcome(outcome, round((time.perf_counter() - started) * 1000))


def _select_for_extraction(
    services: Services,
    now: datetime,
    *,
    source_ids: Collection[str] | None,
    since: datetime | None,
    limit: int | None,
) -> tuple[list[Article], int]:
    """The articles to extract: all pending ones, then failed ones due a retry.

    Returns them with how many are retries. The limit applies to the total,
    pending first, and after the source filter, so it can't be pushed down
    when ``source_ids`` is given.
    """
    wanted = None if source_ids is None else set(source_ids)

    def pick(status: ExtractionStatus, since_: datetime | None, max_n: int | None) -> list[Article]:
        if wanted is None:
            return services.repository.list_articles(status=status, since=since_, limit=max_n)
        found = [
            a
            for a in services.repository.list_articles(status=status, since=since_)
            if a.source_id in wanted
        ]
        return found if max_n is None else found[:max_n]

    selected = pick(ExtractionStatus.PENDING, since, limit)
    policy = services.retry
    if not policy.backoff_hours or (limit is not None and len(selected) >= limit):
        return selected, 0
    window_start = now - timedelta(days=policy.window_days)
    retry_since = max(window_start, since) if since else window_start
    statuses = [ExtractionStatus.FAILED]
    if policy.retry_partial:
        statuses.append(ExtractionStatus.PARTIAL)
    due = [
        a
        for status in statuses
        for a in pick(status, retry_since, None)
        if should_retry(a, now, policy)
    ]
    if limit is not None:
        due = due[: limit - len(selected)]
    return selected + due, len(due)


def extract_pending(
    services: Services,
    tally: RunTally,
    *,
    source_ids: Collection[str] | None = None,
    since: datetime | None = None,
    limit: int | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> None:
    """Fetch and extract pending articles, and retry due failures, in a bounded thread pool.

    ``source_ids``, when given, restricts this to those sources' articles;
    ``None`` extracts every pending article, including those of sources since
    disabled or removed and ad hoc ones. ``since`` only considers articles
    ingested at or after it, ``limit`` at most that many. Failed articles
    whose failure was transient are tried again after a backoff, up to a cap
    (``newsfeed.extract.retry``); they are counted as ``retried`` as well as
    ``attempted``.
    """
    articles, retries = _select_for_extraction(
        services, clock(), source_ids=source_ids, since=since, limit=limit
    )
    tally.count(PipelineStage.EXTRACT, "attempted", len(articles))
    if retries:
        tally.count(PipelineStage.EXTRACT, "retried", retries)
    run = _ExtractRun(services, tally, clock=clock)
    with ThreadPoolExecutor(max_workers=services.concurrency) as pool:
        for _ in pool.map(lambda a: _extract_safely(run, a), articles):
            pass


# --- Single URLs (debug) -----------------------------------------------------------


def inspect_url(fetcher: Fetcher, extractor: Extractor, url: str) -> ExtractionResult:
    """Fetch and extract one page without storing anything.

    Raises:
        PageUnavailableError: if the page can't be fetched.
        NotAnArticleError: if it isn't an HTML page.
    """
    page = fetch_page(fetcher, url)
    return extractor.extract(page.html, page.final_url)


def extract_saved_url(
    services: Services, url: str, *, clock: Callable[[], datetime] = _utc_now
) -> Article:
    """Store ``url`` as an ad hoc article, extract it, and return the stored article.

    Goes through the same path as feed articles, including canonical
    refinement, so the result is what a run would have stored. If the URL is
    already stored (say from a feed), that article is re-extracted with its
    feed metadata intact rather than replaced by an ad hoc one; this includes
    an article stored under a refined canonical URL whose first-seen URL is
    ``url``. If the page's canonical URL belongs to another stored article, the
    ad hoc row merges into it and that article is returned.
    """
    canonical = normalise_url(url, rules=services.url_rules)
    existing = services.repository.get_article(article_id(canonical)) or _find_by_first_url(
        services, canonical
    )
    article = existing or Article(
        canonical_url=canonical,
        url=url,
        source_id=AD_HOC_SOURCE_ID,
        title=url,
        fetched_at=clock(),
    )
    if existing is None:
        services.repository.upsert_article(article)
    run = _ExtractRun(services, RunTally(), clock=clock)
    _extract_safely(run, article)
    # The page may have declared another canonical URL, re-keying the article
    # or merging it into one already stored; report where it lives now.
    final_id = run.rekeyed.get(article.id, article.id)
    return services.repository.get_article(final_id) or article


def _find_by_first_url(services: Services, canonical: str) -> Article | None:
    """The stored article first seen at ``canonical`` but since re-keyed, if any.

    Scans every stored article, like ingest's known-id set; fine for a debug
    command at personal scale.
    """
    for article in services.repository.list_articles():
        try:
            if normalise_url(article.url, rules=services.url_rules) == canonical:
                return article
        except ValueError:
            continue
    return None


# --- Whole runs --------------------------------------------------------------------


def publish_stage(
    services: Services,
    sources: Sequence[Source],
    tally: RunTally,
    *,
    run_id: str,
    now: datetime,
    window_hours: float,
) -> None:
    """Write ``feed.json``. A failure is logged and counted; the previous feed stays."""
    try:
        built = publish_feed(services, sources, run_id=run_id, now=now, window_hours=window_hours)
    except Exception as exc:
        log.error(
            "publish failed; previous feed.json left in place",
            extra={
                "stage": PipelineStage.PUBLISH.value,
                "run_id": run_id,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        tally.count(PipelineStage.PUBLISH, "failed")
        tally.error()
        return
    tally.count(PipelineStage.PUBLISH, "items", len(built.feed["items"]))
    if built.dropped:
        tally.count(PipelineStage.PUBLISH, "dropped", built.dropped)
    if built.links_for_missing_content:
        tally.count(PipelineStage.PUBLISH, "missing_content", built.links_for_missing_content)


def run_pipeline(
    services: Services,
    sources: Sequence[Source],
    *,
    ingest: bool = True,
    extract: bool = True,
    rank: bool = True,
    publish: bool = True,
    interests: InterestsConfig | None = None,
    rank_sources: Sequence[Source] | None = None,
    rank_window_hours: float = DEFAULT_WINDOW_HOURS,
    extract_source_ids: Collection[str] | None = None,
    since: datetime | None = None,
    limit: int | None = None,
    run_id: str | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> RunStats:
    """Run the chosen stages, saving ``RunStats`` when the run starts and when it ends.

    ``sources`` are ingested. Extract takes every pending article unless
    ``extract_source_ids`` narrows it (the CLI passes ``--source`` here), so
    articles left pending by a source since disabled or removed, and ad hoc
    ones, are still extracted by a plain run. Rank scores articles fetched in
    the last ``rank_window_hours`` against ``interests`` (none means no rules),
    taking source weights from ``rank_sources`` (default: ``sources``). Publish
    then writes ``feed.json``; its window never exceeds ``rank_window_hours``.
    """
    started = clock()
    run_id = run_id or started.strftime("%Y%m%dT%H%M%S") + f"-{started.microsecond:06d}"
    tally = RunTally()
    services.repository.save_run_stats(tally.stats(run_id, started, None))
    durations: dict[str, float] = {}
    if ingest:
        began = time.perf_counter()
        ingest_sources(services, sources, tally, since=since, limit=limit)
        durations[PipelineStage.INGEST.value] = time.perf_counter() - began
    if extract:
        began = time.perf_counter()
        extract_pending(
            services, tally, source_ids=extract_source_ids, since=since, limit=limit, clock=clock
        )
        durations[PipelineStage.EXTRACT.value] = time.perf_counter() - began
    if rank:
        began = time.perf_counter()
        summary = rank_recent(
            services,
            interests or InterestsConfig(),
            since=clock() - timedelta(hours=rank_window_hours),
            sources=sources if rank_sources is None else rank_sources,
            clock=clock,
        )
        for name, n in summary.counts().items():
            if n:
                tally.count(PipelineStage.RANK, name, n)
        for _ in range(summary.errors):
            tally.error()
        durations[PipelineStage.RANK.value] = time.perf_counter() - began
    if publish:
        began = time.perf_counter()
        publish_stage(
            services,
            sources if rank_sources is None else rank_sources,
            tally,
            run_id=run_id,
            now=clock(),
            window_hours=min(FEED_WINDOW_HOURS, rank_window_hours),
        )
        durations[PipelineStage.PUBLISH.value] = time.perf_counter() - began
    stats = tally.stats(run_id, started, clock(), durations)
    services.repository.save_run_stats(stats)
    log.info(
        "run finished",
        extra={
            "stage": "run",
            "run_id": run_id,
            "error_count": stats.error_count,
            "stage_counts": {s.value: c for s, c in stats.stage_counts.items()},
            "metrics": tally.summary(run_id, durations),
        },
    )
    return stats
