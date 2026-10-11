"""Tests for the shared data model in ``newsfeed.models``."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from newsfeed.models import (
    Article,
    ArticleExtraction,
    ExtractionAttempt,
    ExtractionResult,
    ExtractionStatus,
    Feedback,
    FeedbackEvent,
    FetchResult,
    InterestRules,
    PipelineStage,
    Profile,
    RunStats,
    Source,
    SourceState,
    SourceType,
    article_id,
)

NOW = datetime(2026, 10, 11, 12, 30, tzinfo=UTC)
URL = "https://example.com/news/2026/10/invented-story"


def make_article(**overrides: Any) -> Article:
    """Build a valid article, with keyword overrides."""
    fields: dict[str, Any] = {
        "canonical_url": URL,
        "url": URL + "?utm_source=feed",
        "source_id": "example",
        "title": "An invented headline",
        "fetched_at": NOW,
    }
    fields.update(overrides)
    return Article(**fields)


# --- article_id ----------------------------------------------------------------


def test_article_id_known_value() -> None:
    assert article_id("https://example.com/") == "0f115db062b7c0dd"


def test_article_id_is_sha256_prefix() -> None:
    assert article_id(URL) == hashlib.sha256(URL.encode("utf-8")).hexdigest()[:16]


def test_article_id_is_deterministic_and_16_hex_chars() -> None:
    first = article_id(URL)
    assert first == article_id(URL)
    assert len(first) == 16
    assert all(c in "0123456789abcdef" for c in first)


def test_article_id_distinguishes_urls() -> None:
    assert article_id(URL) != article_id(URL + "/")


def test_article_id_handles_non_ascii() -> None:
    url = "https://example.com/café"
    assert article_id(url) == hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


# --- Article -------------------------------------------------------------------


def test_article_derives_id_and_defaults() -> None:
    article = make_article()
    assert article.id == article_id(URL)
    assert article.extraction == ArticleExtraction()
    assert article.extraction.status is ExtractionStatus.PENDING
    assert article.content_ref is None
    assert article.score is None
    assert article.score_breakdown == {}


def test_article_accepts_matching_id_and_rejects_mismatch() -> None:
    assert make_article(id=article_id(URL)).id == article_id(URL)
    with pytest.raises(ValidationError, match="does not match"):
        make_article(id="0000000000000000")


def test_article_identity_fields_are_frozen() -> None:
    article = make_article()
    with pytest.raises(ValidationError):
        article.canonical_url = "https://example.com/other"
    with pytest.raises(ValidationError):
        article.id = "0000000000000000"
    assert article.canonical_url == URL


def test_article_assignment_is_validated() -> None:
    article = make_article()
    article.extraction.status = ExtractionStatus.OK
    with pytest.raises(ValidationError):
        article.extraction.status = "bogus"  # type: ignore[assignment]
    with pytest.raises(ValidationError):
        article.fetched_at = datetime(2026, 1, 1)


def test_article_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        make_article(headline="typo")


def test_article_rejects_bad_types() -> None:
    with pytest.raises(ValidationError):
        make_article(title=None)
    with pytest.raises(ValidationError):
        make_article(score="high")


# --- datetimes -----------------------------------------------------------------


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        make_article(fetched_at=datetime(2026, 10, 11, 12, 30))


def test_naive_datetime_string_is_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        Feedback.model_validate(
            {"article_id": "a", "event": "open", "created_at": "2026-10-11T12:30:00"}
        )


def test_aware_datetime_is_converted_to_utc() -> None:
    plus_two = timezone(timedelta(hours=2))
    article = make_article(published_at=datetime(2026, 10, 11, 14, 30, tzinfo=plus_two))
    assert article.published_at == NOW
    assert article.published_at is not None
    assert article.published_at.tzinfo is UTC


def test_utc_string_with_z_is_accepted() -> None:
    state = SourceState.model_validate(
        {"source_id": "example", "last_fetched_at": "2026-10-11T12:30:00Z"}
    )
    assert state.last_fetched_at == NOW


def test_datetimes_serialise_as_utc_iso() -> None:
    assert '"fetched_at":"2026-10-11T12:30:00Z"' in make_article().model_dump_json()


# Third-party dates (published_at) are lenient: one messy field must not fail an article.
LENIENT_CASES: list[tuple[str, object, datetime | None]] = [
    ("aware ISO, Z", "2026-10-11T12:30:00Z", NOW),
    ("aware ISO, offset", "2026-10-11T14:30:00+02:00", NOW),
    ("naive ISO assumed UTC", "2026-10-11T12:30:00", NOW),
    ("date only (Trafilatura)", "2026-10-11", datetime(2026, 10, 11, tzinfo=UTC)),
    ("naive datetime (newspaper4k)", datetime(2026, 10, 11, 12, 30), NOW),
    ("date object", datetime(2026, 10, 11).date(), datetime(2026, 10, 11, tzinfo=UTC)),
    ("RFC 2822 (RSS/HTTP)", "Sun, 11 Oct 2026 12:30:00 GMT", NOW),
    ("epoch seconds (HN)", int(NOW.timestamp()), NOW),
    ("unparseable", "last Tuesday", None),
    ("epoch out of range", 10**20, None),
    ("unsupported type", ["2026-10-11"], None),
    ("empty string", "", None),
    ("None", None, None),
]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(value, expected) for _, value, expected in LENIENT_CASES],
    ids=[name for name, _, _ in LENIENT_CASES],
)
@pytest.mark.parametrize("model", ["extraction_result", "article"])
def test_published_at_is_lenient(model: str, value: object, expected: datetime | None) -> None:
    if model == "article":
        published_at = make_article(published_at=value).published_at
    else:
        published_at = ExtractionResult(status="ok", published_at=value).published_at
    assert published_at == expected
    if published_at is not None:
        assert published_at.tzinfo is UTC


def test_published_at_assignment_is_lenient() -> None:
    article = make_article()
    article.published_at = "2026-10-11"  # type: ignore[assignment]
    assert article.published_at == datetime(2026, 10, 11, tzinfo=UTC)


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (RunStats, "started_at"),
        (SourceState, "last_fetched_at"),
        (Feedback, "created_at"),
    ],
)
def test_own_timestamps_stay_strict(model: type[BaseModel], field: str) -> None:
    base: dict[str, Any] = {
        RunStats: {"run_id": "r1", "started_at": NOW},
        SourceState: {"source_id": "example"},
        Feedback: {"article_id": "a", "event": "open", "created_at": NOW},
    }[model]
    with pytest.raises(ValidationError, match="timezone-aware"):
        model.model_validate({**base, field: "2026-10-11T12:30:00"})


# --- enums ---------------------------------------------------------------------


def test_enum_values() -> None:
    assert [m.value for m in SourceType] == ["rss", "google_news", "hn", "reddit"]
    assert [m.value for m in ExtractionStatus] == [
        "pending",
        "ok",
        "partial",
        "failed",
        "paywalled",
        "skipped",
    ]
    assert [m.value for m in FeedbackEvent] == ["open", "read", "like", "dismiss", "mute_source"]
    assert [m.value for m in PipelineStage] == ["ingest", "extract", "rank", "publish"]


def test_enums_parse_from_strings_and_compare_as_strings() -> None:
    attempt = ExtractionAttempt.model_validate({"method": "trafilatura", "status": "ok"})
    assert attempt.status is ExtractionStatus.OK
    assert str(attempt.status) == "ok"
    assert attempt.model_dump(mode="json")["status"] == "ok"


def test_unknown_enum_value_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Source.model_validate({"id": "x", "type": "atom", "url": "https://example.com/feed"})
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate({"status": "done"})


# --- Source --------------------------------------------------------------------


def test_source_defaults() -> None:
    source = Source(id="example", type=SourceType.RSS, url="https://example.com/feed.xml")
    assert source.weight == 1.0
    assert source.enabled is True
    assert source.tags == []
    assert source.options == {}
    assert source.query is None


def test_rss_source_requires_url() -> None:
    with pytest.raises(ValidationError, match="requires a url"):
        Source(id="example", type=SourceType.RSS)


def test_non_rss_source_may_use_query() -> None:
    source = Source(id="gn-python", type=SourceType.GOOGLE_NEWS, query="python")
    assert source.url is None


@pytest.mark.parametrize("bad_id", ["", "Has-Caps", "-leading", "with space", "a/b"])
def test_source_id_must_be_a_slug(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        Source(id=bad_id, type=SourceType.HN)


def test_source_weight_cannot_be_negative() -> None:
    with pytest.raises(ValidationError):
        Source(id="hn", type=SourceType.HN, weight=-0.5)


# --- FetchResult and ExtractionResult ------------------------------------------


def test_fetch_result_lowercases_header_names() -> None:
    result = FetchResult(url=URL, final_url=URL, headers={"ETag": '"abc"', "Content-Type": "x"})
    assert result.headers == {"etag": '"abc"', "content-type": "x"}


def test_fetch_result_rejects_negative_elapsed() -> None:
    with pytest.raises(ValidationError):
        FetchResult(url=URL, final_url=URL, elapsed_ms=-1)


def test_extraction_result_defaults() -> None:
    result = ExtractionResult(status=ExtractionStatus.FAILED, error="boom")
    assert result.attempts == []
    assert result.word_count == 0
    assert result.content_html is None


def test_default_factories_are_not_shared() -> None:
    first = ExtractionResult(status=ExtractionStatus.OK)
    second = ExtractionResult(status=ExtractionStatus.OK)
    first.attempts.append(ExtractionAttempt(method="m", status=ExtractionStatus.OK))
    assert second.attempts == []


# --- JSON round trips ------------------------------------------------------------

ROUND_TRIP_MODELS: list[BaseModel] = [
    Source(
        id="example",
        type=SourceType.RSS,
        url="https://example.com/feed.xml",
        weight=1.5,
        tags=["tech"],
        options={"max_items": 50, "nested": {"flag": True}},
    ),
    SourceState(
        source_id="example",
        etag='W/"abc"',
        last_modified="Sun, 11 Oct 2026 12:30:00 GMT",
        last_fetched_at=NOW,
        last_status=304,
    ),
    FetchResult(
        url=URL,
        final_url=URL + "/",
        status_code=200,
        headers={"content-type": "text/html"},
        content=bytes(range(256)),
        elapsed_ms=120,
    ),
    ExtractionResult(
        status=ExtractionStatus.OK,
        method="trafilatura",
        title="An invented headline",
        author="A. Writer",
        published_at=NOW,
        content_html="<p>Invented text.</p>",
        text="Invented text.",
        lead_image="https://example.com/img.jpg",
        word_count=2,
        attempts=[
            ExtractionAttempt(method="readability", status=ExtractionStatus.FAILED, error="x"),
            ExtractionAttempt(method="trafilatura", status=ExtractionStatus.OK, word_count=2),
        ],
    ),
    make_article(
        author="A. Writer",
        published_at=NOW - timedelta(hours=1),
        summary="An invented summary.",
        lead_image="https://example.com/img.jpg",
        extraction=ArticleExtraction(
            status=ExtractionStatus.OK, method="trafilatura", word_count=812
        ),
        content_ref="articles/0123456789abcdef.html",
        score=0.75,
        score_breakdown={"rules": 0.5, "freshness": 0.25},
        cluster_id="c1",
    ),
    Feedback(article_id=article_id(URL), event=FeedbackEvent.READ, value=42.5, created_at=NOW),
    Profile(rules=InterestRules(boost_topics=["python"], mute_sources=["noisy"])),
    RunStats(
        run_id="2026-10-11T12:30:00Z",
        started_at=NOW,
        finished_at=NOW + timedelta(minutes=3),
        stage_counts={PipelineStage.INGEST: {"new": 12, "seen": 40}},
        extraction_by_domain={"example.com": {ExtractionStatus.OK: 10, ExtractionStatus.FAILED: 2}},
        error_count=2,
    ),
]


@pytest.mark.parametrize("model", ROUND_TRIP_MODELS, ids=lambda m: type(m).__name__)
def test_json_round_trip(model: BaseModel) -> None:
    restored = type(model).model_validate_json(model.model_dump_json())
    assert restored == model


@pytest.mark.parametrize("model", ROUND_TRIP_MODELS, ids=lambda m: type(m).__name__)
def test_python_dict_round_trip(model: BaseModel) -> None:
    restored = type(model).model_validate(model.model_dump(mode="json"))
    assert restored == model


def test_feedback_ids_are_unique_by_default() -> None:
    a = Feedback(article_id="a", event=FeedbackEvent.OPEN, created_at=NOW)
    b = Feedback(article_id="a", event=FeedbackEvent.OPEN, created_at=NOW)
    assert a.id != b.id


def test_run_stats_rejects_unknown_stage_and_negative_counts() -> None:
    with pytest.raises(ValidationError):
        RunStats.model_validate(
            {"run_id": "r", "started_at": NOW, "stage_counts": {"deploy": {"n": 1}}}
        )
    with pytest.raises(ValidationError):
        RunStats(run_id="r", started_at=NOW, stage_counts={PipelineStage.RANK: {"n": -1}})
