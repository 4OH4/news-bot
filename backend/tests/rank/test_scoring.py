"""Tests for the pure scoring function."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from newsfeed.models import Article, Source, SourceType
from newsfeed.rank.interests import InterestsConfig
from newsfeed.rank.scoring import score_article

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def article(**changes: object) -> Article:
    values: dict[str, object] = {
        "canonical_url": "https://news.example.com/a",
        "url": "https://news.example.com/a",
        "source_id": "gazette",
        "title": "A quiet day",
        "fetched_at": NOW,
        **changes,
    }
    return Article.model_validate(values)


def config(**values: object) -> InterestsConfig:
    return InterestsConfig.model_validate(values)


TOPIC_AI = {"name": "ai", "keywords": ["AI", "machine learning"], "weight": 2.0}


def test_empty_config_scores_on_source_weight_and_recency_only() -> None:
    source = Source(id="gazette", type=SourceType.RSS, url="https://x.example/f", weight=1.5)
    old = article(published_at=NOW - timedelta(hours=24))

    score, breakdown = score_article(old, "AI everywhere", config(), source, NOW)

    assert breakdown == {"source": 1.5, "recency": pytest.approx(0.5)}
    assert score == pytest.approx(0.75)


def test_topic_counts_once_per_field_with_field_weights() -> None:
    a = article(title="AI and AI again", summary="About machine learning")
    score, breakdown = score_article(a, "body mentions AI", config(topics=[TOPIC_AI]), None, NOW)

    # title 3.0 + summary 2.0 + body 1.0, times topic weight 2.0
    assert breakdown["topic:ai"] == pytest.approx(12.0)
    assert score == pytest.approx(13.0)  # BASE 1 + 12, source 1, recency 1


def test_custom_field_weights() -> None:
    cfg = config(topics=[TOPIC_AI], fields={"title": 1.0, "summary": 0.0, "body": 0.0})
    a = article(title="AI news", summary="AI")
    assert score_article(a, "AI", cfg, None, NOW)[1]["topic:ai"] == pytest.approx(2.0)


def test_keywords_match_whole_words_only() -> None:
    a = article(title="Maintain the airport", summary="Said again")
    _, breakdown = score_article(a, "mainstream", config(topics=[TOPIC_AI]), None, NOW)
    assert "topic:ai" not in breakdown


def test_negative_topic_demotes_and_score_never_goes_below_zero() -> None:
    cfg = config(topics=[{"name": "celebs", "keywords": ["celebrity"], "weight": -0.2}])
    a = article(title="Celebrity wedding")
    score, breakdown = score_article(a, None, cfg, None, NOW)
    assert breakdown["topic:celebs"] == pytest.approx(-0.6)
    assert score == pytest.approx(0.4)

    harsh = config(topics=[{"name": "celebs", "keywords": ["celebrity"], "weight": -5}])
    assert score_article(a, None, harsh, None, NOW)[0] == 0.0


def test_mute_keyword_zeroes_the_score_but_keeps_the_breakdown() -> None:
    cfg = config(topics=[TOPIC_AI], mute_keywords=["sponsored"])
    a = article(title="AI roundup")
    score, breakdown = score_article(a, "This post is sponsored.", cfg, None, NOW)

    assert score == 0.0
    assert breakdown["muted"] == 1.0
    assert "topic:ai" in breakdown


def test_muted_source() -> None:
    cfg = config(sources={"gazette": {"mute": True}})
    score, breakdown = score_article(article(), None, cfg, None, NOW)
    assert score == 0.0
    assert "muted" in breakdown


def test_source_rule_overrides_source_weight() -> None:
    source = Source(id="gazette", type=SourceType.RSS, url="https://x.example/f", weight=2.0)
    cfg = config(sources={"gazette": {"weight": 0.5}})
    score, breakdown = score_article(article(), None, cfg, source, NOW)
    assert breakdown["source"] == 0.5
    assert score == pytest.approx(0.5)


def test_recency_uses_half_life_and_falls_back_to_fetched_at() -> None:
    cfg = config(recency={"half_life_hours": 6})
    published = article(published_at=NOW - timedelta(hours=12), fetched_at=NOW)
    fetched = article(fetched_at=NOW - timedelta(hours=6))

    assert score_article(published, None, cfg, None, NOW)[1]["recency"] == pytest.approx(0.25)
    assert score_article(fetched, None, cfg, None, NOW)[1]["recency"] == pytest.approx(0.5)


def test_future_dates_do_not_boost_above_one() -> None:
    a = article(published_at=NOW + timedelta(hours=5))
    assert score_article(a, None, config(), None, NOW)[1]["recency"] == 1.0


def test_does_not_modify_the_config() -> None:
    cfg = config(topics=[TOPIC_AI], mute_keywords=["x"])
    before = cfg.model_dump()
    score_article(article(title="AI"), "text", cfg, None, NOW)
    assert cfg.model_dump() == before
