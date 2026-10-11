"""Shared data model: the contract between pipeline stages.

Every stage (ingest, extract, rank, publish) and every adapter exchanges these
models through the ports in ``ports.py``. Changing anything here is a contract
change and needs its own ``needs-human`` issue (see CLAUDE.md, rule 2).

Conventions:

* All models are Pydantic v2 (ADR-0001). Unknown fields are rejected and
  assignments are validated, so typos and bad values fail loudly at the point
  they are introduced rather than several stages later.
* Every datetime is timezone-aware UTC, and aware datetimes in other zones are
  converted to UTC. Timestamps we produce ourselves (``UtcDatetime``) reject
  naive values, because a naive value there is a bug: use ``datetime.now(UTC)``,
  never ``datetime.now()`` or ``utcnow()``. Third-party dates such as
  ``published_at`` (``LenientUtcDatetime``) are lenient instead: naive means UTC,
  a date means midnight UTC, and unparseable means None, so one messy metadata
  field never fails an article.
* URLs are plain ``str``. Pydantic's URL types rewrite URLs (e.g. adding a
  trailing slash), and URL normalisation belongs to ``ingest/canonical.py``.
* Article content is not stored on the model: it lives in the ``BlobStore`` and
  ``Article.content_ref`` points at it (ADR-0001, decision 3).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, model_validator

__all__ = [
    "Article",
    "ArticleExtraction",
    "ExtractionAttempt",
    "ExtractionResult",
    "ExtractionStatus",
    "Feedback",
    "FeedbackEvent",
    "FetchResult",
    "InterestRules",
    "LenientUtcDatetime",
    "PipelineStage",
    "Profile",
    "RunStats",
    "Source",
    "SourceState",
    "SourceType",
    "UtcDatetime",
    "article_id",
]

ARTICLE_ID_LENGTH = 16
"""Number of hex characters of the SHA-256 digest used as an article id."""


def article_id(canonical_url: str) -> str:
    """Return the article id for a canonical URL.

    The id is the first 16 hex characters (64 bits) of the SHA-256 digest of the
    UTF-8 encoded URL. The URL is hashed exactly as given, so callers must
    canonicalise it first (``ingest/canonical.py``).
    """
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:ARTICLE_ID_LENGTH]


def _ensure_utc(value: datetime) -> datetime:
    """Reject naive datetimes and convert aware ones to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware (UTC); got a naive datetime")
    return value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_ensure_utc)]
"""A timezone-aware datetime, normalised to UTC. Naive datetimes are rejected.

Used for timestamps our own code produces, where a naive value means a bug.
"""


def _lenient_utc(value: Any) -> datetime | None:
    """Coerce a third-party date to aware UTC, or None if it can't be understood.

    Publishers' dates are messy: Trafilatura returns date-only strings, and meta
    tags often omit the offset. A wrong-by-hours date costs little, but raising
    here would fail an otherwise good article, so naive values are assumed UTC,
    dates become midnight UTC, and anything unparseable becomes None.
    """
    if value is None or isinstance(value, bool):
        return None
    parsed: datetime | None = None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    elif isinstance(value, int | float):
        try:
            return datetime.fromtimestamp(value, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        text = value.strip()
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            try:
                parsed = parsedate_to_datetime(text)  # RFC 2822, e.g. HTTP/RSS dates
            except (TypeError, ValueError, IndexError):
                return None
    if parsed is None:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


LenientUtcDatetime = Annotated[datetime | None, BeforeValidator(_lenient_utc)]
"""An optional third-party date (e.g. ``published_at``), coerced to aware UTC.

Naive values are assumed UTC, date-only values become midnight UTC, and
unparseable values become None instead of failing validation.
"""

NonNegativeInt = Annotated[int, Field(ge=0)]


class _Model(BaseModel):
    """Base for all contract models: strict about unknown fields and assignment."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# --- Sources -----------------------------------------------------------------


class SourceType(StrEnum):
    """Kind of source, which selects the ingester."""

    RSS = "rss"
    GOOGLE_NEWS = "google_news"
    HN = "hn"
    REDDIT = "reddit"


class Source(_Model):
    """A configured news source, loaded from ``config/sources.yaml``.

    Sources are configuration, not repository data (ADR-0001); mutable per-source
    fetch state lives in ``SourceState``.
    """

    id: str = Field(
        pattern=r"^[a-z0-9][a-z0-9_-]*$",
        description="Stable slug, e.g. 'bbc-tech'. Lowercase letters, digits, '-' and '_'.",
    )
    type: SourceType
    url: str | None = Field(default=None, description="Feed or listing URL.")
    query: str | None = Field(default=None, description="Search query, e.g. for Google News.")
    weight: float = Field(default=1.0, ge=0.0, description="Ranking multiplier; 0 disables.")
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)
    options: dict[str, Any] = Field(
        default_factory=dict, description="Type-specific settings, interpreted by the ingester."
    )

    @model_validator(mode="after")
    def _rss_needs_url(self) -> Self:
        if self.type is SourceType.RSS and not self.url:
            raise ValueError(f"source {self.id!r}: type 'rss' requires a url")
        return self


class SourceState(_Model):
    """Per-source fetch state kept in the ``Repository`` for conditional GETs."""

    source_id: str
    etag: str | None = None
    last_modified: str | None = Field(
        default=None,
        description="Last-Modified header value, stored verbatim for If-Modified-Since.",
    )
    last_fetched_at: UtcDatetime | None = None
    last_status: int | None = Field(
        default=None, description="HTTP status of the last fetch; None if no response."
    )


# --- Fetching ----------------------------------------------------------------


class FetchResult(_Model):
    """Outcome of one HTTP fetch made through the ``Fetcher`` port.

    ``content`` is raw bytes; in JSON it is encoded as base64 so binary bodies
    survive a round trip. Header names are lowercased because HTTP headers are
    case-insensitive.
    """

    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")

    url: str = Field(description="URL that was requested.")
    final_url: str = Field(description="URL after redirects; equals url if none.")
    status_code: int | None = Field(
        default=None, description="HTTP status; None if no response (network error, robots)."
    )
    headers: dict[str, str] = Field(default_factory=dict)
    content: bytes = b""
    not_modified: bool = Field(default=False, description="True for a 304 Not Modified.")
    error: str | None = None
    elapsed_ms: NonNegativeInt = 0

    @model_validator(mode="before")
    @classmethod
    def _lowercase_header_names(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("headers"), dict):
            data = {**data, "headers": {str(k).lower(): v for k, v in data["headers"].items()}}
        return data


# --- Extraction --------------------------------------------------------------


class ExtractionStatus(StrEnum):
    """Outcome of extracting an article's content."""

    PENDING = "pending"
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"
    PAYWALLED = "paywalled"
    SKIPPED = "skipped"


class ExtractionAttempt(_Model):
    """One extractor's attempt at a page, kept for evaluation and debugging."""

    method: str = Field(description="Extractor name, e.g. 'trafilatura'.")
    status: ExtractionStatus
    error: str | None = None
    word_count: NonNegativeInt = 0


class ExtractionResult(_Model):
    """What an ``Extractor`` returns for one page.

    For an extractor chain, the top-level fields describe the attempt that was
    chosen and ``attempts`` lists every attempt in order.
    """

    status: ExtractionStatus
    method: str | None = Field(default=None, description="Extractor that produced the result.")
    title: str | None = None
    author: str | None = None
    published_at: LenientUtcDatetime = None
    content_html: str | None = Field(default=None, description="Sanitised article HTML.")
    text: str | None = Field(default=None, description="Plain text, used for word counts.")
    lead_image: str | None = None
    word_count: NonNegativeInt = 0
    error: str | None = None
    attempts: list[ExtractionAttempt] = Field(default_factory=list)


class ArticleExtraction(_Model):
    """Extraction summary stored on an ``Article`` (the content itself is a blob)."""

    status: ExtractionStatus = ExtractionStatus.PENDING
    method: str | None = None
    error: str | None = None
    word_count: NonNegativeInt = 0


# --- Articles ----------------------------------------------------------------


class Article(_Model):
    """Article metadata as stored in the ``Repository``.

    ``id`` is always ``article_id(canonical_url)``: it is derived when omitted
    (or ``None``) and a mismatching value is rejected. Both fields are frozen, so
    changing the canonical URL means creating a new article, not editing this one.
    """

    id: str = Field(
        default="", frozen=True, description="article_id(canonical_url); derived if omitted."
    )
    canonical_url: str = Field(frozen=True)
    url: str = Field(description="URL as first seen in the source.")
    source_id: str
    title: str
    author: str | None = None
    published_at: LenientUtcDatetime = None
    fetched_at: UtcDatetime = Field(description="When the article was first ingested.")
    summary: str | None = None
    lead_image: str | None = None
    extraction: ArticleExtraction = Field(default_factory=ArticleExtraction)
    content_ref: str | None = Field(
        default=None, description="BlobStore ref of the clean HTML; None until extracted."
    )
    score: float | None = Field(default=None, description="Ranking score; None until ranked.")
    score_breakdown: dict[str, float] = Field(
        default_factory=dict, description="Named components of the score, for transparency."
    )
    cluster_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _derive_id(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("id") and "canonical_url" in data:
            data = {**data, "id": article_id(str(data["canonical_url"]))}
        return data

    @model_validator(mode="after")
    def _check_id(self) -> Self:
        expected = article_id(self.canonical_url)
        if self.id != expected:
            raise ValueError(
                f"id {self.id!r} does not match article_id(canonical_url) = {expected!r}"
            )
        return self


# --- Feedback and profile ----------------------------------------------------


class FeedbackEvent(StrEnum):
    """Kind of reader feedback on an article."""

    OPEN = "open"
    READ = "read"
    LIKE = "like"
    DISMISS = "dismiss"
    MUTE_SOURCE = "mute_source"


class Feedback(_Model):
    """One feedback event from the reader client."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    article_id: str
    event: FeedbackEvent
    value: float | None = Field(default=None, description="E.g. seconds read for 'read'.")
    created_at: UtcDatetime


class InterestRules(_Model):
    """Rule-based interests, mirroring ``config/interests.yaml``."""

    boost_topics: list[str] = Field(default_factory=list)
    mute_topics: list[str] = Field(default_factory=list)
    boost_sources: list[str] = Field(default_factory=list)
    mute_sources: list[str] = Field(default_factory=list)


class Profile(_Model):
    """The reader's interest profile. Embedding centroids arrive in Phase 4."""

    rules: InterestRules = Field(default_factory=InterestRules)


# --- Run statistics ----------------------------------------------------------


class PipelineStage(StrEnum):
    """Pipeline stages, used as keys in ``RunStats.stage_counts``."""

    INGEST = "ingest"
    EXTRACT = "extract"
    RANK = "rank"
    PUBLISH = "publish"


class RunStats(_Model):
    """Summary of one pipeline run, saved by the ``Repository``."""

    run_id: str
    started_at: UtcDatetime
    finished_at: UtcDatetime | None = Field(default=None, description="None while running.")
    stage_counts: dict[PipelineStage, dict[str, NonNegativeInt]] = Field(
        default_factory=dict,
        description="Per stage, named counters, e.g. {'ingest': {'new': 12, 'seen': 40}}.",
    )
    extraction_by_domain: dict[str, dict[ExtractionStatus, NonNegativeInt]] = Field(
        default_factory=dict,
        description="Per domain, count of articles by extraction status.",
    )
    error_count: NonNegativeInt = 0
