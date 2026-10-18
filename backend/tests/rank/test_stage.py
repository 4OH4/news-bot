"""Tests for the rank stage, with in-memory fakes."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from newsfeed.config import UrlParamsConfig
from newsfeed.models import Article, Source, SourceType
from newsfeed.rank import stage
from newsfeed.rank.interests import InterestsConfig
from newsfeed.rank.stage import html_to_text, load_rank_config, rank_recent
from newsfeed.wiring import Services
from tests.fakes import FakeExtractor, FakeFetcher, InMemoryBlobStore, InMemoryRepository

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
DAY_AGO = NOW - timedelta(days=1)
CONFIG = InterestsConfig.model_validate(
    {"topics": [{"name": "rust", "keywords": ["rust"], "weight": 2.0}]}
)


def services() -> Services:
    return Services(
        repository=InMemoryRepository(),
        blobs=InMemoryBlobStore(),
        fetcher=FakeFetcher(),
        ingesters={},
        extractor=FakeExtractor(),
        url_rules=UrlParamsConfig(),
    )


def article(name: str, *, age_hours: float = 1, **changes: object) -> Article:
    url = f"https://news.example.com/{name}"
    return Article.model_validate(
        {
            "canonical_url": url,
            "url": url,
            "source_id": "gazette",
            "title": f"Story {name}",
            "fetched_at": NOW - timedelta(hours=age_hours),
            **changes,
        }
    )


def test_html_to_text_drops_tags_and_scripts() -> None:
    assert html_to_text("<p>Hello <b>world</b></p><script>x()</script>") == "Hello world"
    assert html_to_text("  ") == ""


def test_scores_recent_articles_using_body_text() -> None:
    s = services()
    ref = s.blobs.put("articles/a.html", b"<p>All about Rust.</p>")
    s.repository.upsert_article(article("a", content_ref=ref))
    s.repository.upsert_article(article("b"))
    s.repository.upsert_article(article("old", age_hours=200))

    summary = rank_recent(s, CONFIG, since=NOW - timedelta(hours=72), clock=lambda: NOW)

    assert summary.scored == 2
    assert summary.no_body == 1
    ranked = {a.title: a for a in s.repository.list_articles()}
    assert ranked["Story a"].score_breakdown["topic:rust"] == pytest.approx(2.0)
    assert ranked["Story a"].score is not None
    assert ranked["Story b"].score_breakdown.keys() == {"source", "recency"}
    assert ranked["Story old"].score is None  # outside the window


def test_source_weight_comes_from_sources() -> None:
    s = services()
    s.repository.upsert_article(article("a"))
    source = Source(id="gazette", type=SourceType.RSS, url="https://x.example/f", weight=3.0)

    rank_recent(s, InterestsConfig(), since=DAY_AGO, sources=[source], clock=lambda: NOW)

    assert s.repository.list_articles()[0].score_breakdown["source"] == 3.0


def test_muted_articles_are_stored_with_muted_in_the_breakdown() -> None:
    s = services()
    s.repository.upsert_article(article("a"))
    cfg = InterestsConfig.model_validate({"sources": {"gazette": {"mute": True}}})

    summary = rank_recent(s, cfg, since=DAY_AGO, clock=lambda: NOW)

    stored = s.repository.list_articles()[0]
    assert summary.muted == 1
    assert stored.score == 0.0
    assert "muted" in stored.score_breakdown


def test_failure_on_one_article_is_logged_and_does_not_stop_the_stage(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = services()
    for name in ("a", "b", "c"):
        s.repository.upsert_article(article(name))
    real = stage.score_article

    def flaky(art: Article, *args: object, **kwargs: object) -> tuple[float, dict[str, float]]:
        if art.title == "Story b":
            raise RuntimeError("boom")
        return real(art, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(stage, "score_article", flaky)
    with caplog.at_level(logging.WARNING):
        summary = rank_recent(s, CONFIG, since=DAY_AGO, clock=lambda: NOW)

    assert (summary.scored, summary.errors) == (2, 1)
    record = next(r for r in caplog.records if r.getMessage() == "ranking failed")
    assert (record.stage, record.source_id, record.url) == (  # type: ignore[attr-defined]
        "rank",
        "gazette",
        "https://news.example.com/b",
    )
    scores = {a.title: a.score for a in s.repository.list_articles()}
    assert scores["Story b"] is None
    assert scores["Story a"] is not None


def test_unreadable_content_falls_back_to_title_and_summary() -> None:
    s = services()
    s.repository.upsert_article(article("a", content_ref="articles/missing.html"))

    summary = rank_recent(s, CONFIG, since=DAY_AGO, clock=lambda: NOW)

    assert (summary.scored, summary.errors, summary.no_body) == (1, 0, 1)


def test_load_rank_config_logs_what_was_loaded(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "interests.yaml"
    path.write_text(
        "topics:\n  - {name: t, keywords: [x], weight: 1}\nsources:\n  ghost: {weight: 2}\n"
    )
    known = [Source(id="a", type=SourceType.RSS, url="https://x.example/f")]
    with caplog.at_level(logging.INFO):
        config = load_rank_config(path, known)

    assert len(config.topics) == 1
    loaded = next(r for r in caplog.records if r.getMessage() == "interests loaded")
    assert (loaded.topics, loaded.source_rules) == (1, 1)  # type: ignore[attr-defined]
    assert any(r.getMessage() == "interests file mentions unknown sources" for r in caplog.records)


def test_missing_interests_file_warns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        config = load_rank_config(tmp_path / "nope.yaml")

    assert config.topics == []
    assert any("not found" in r.getMessage() for r in caplog.records)
