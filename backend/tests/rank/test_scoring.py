"""Tests for the pure scoring function."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from newsfeed.models import Article, Source, SourceType
from newsfeed.rank.interests import InterestsConfig, load_interests
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


CELEBS = {"name": "celebs", "keywords": ["celebrity"], "weight": -1.0}


@pytest.mark.parametrize(
    ("title", "body", "expected"),
    [
        ("A quiet day", "A celebrity was there", 0.5),  # body only: 1 / (1 + 1 * 1)
        ("Celebrity wedding", None, 0.25),  # title: 1 / (1 + 1 * 3)
        ("Celebrity wedding", "celebrity guests", 0.25),  # strongest field, not summed
    ],
)
def test_negative_topic_is_a_multiplier_from_the_strongest_field(
    title: str, body: str | None, expected: float
) -> None:
    score, breakdown = score_article(article(title=title), body, config(topics=[CELEBS]), None, NOW)

    assert breakdown["demote:celebs"] == pytest.approx(expected)
    assert "topic:celebs" not in breakdown
    assert score == pytest.approx(expected)


def test_demoted_article_scores_above_zero_and_below_the_same_article_undemoted() -> None:
    cfg = config(topics=[TOPIC_AI, {**CELEBS, "weight": -5.0}])
    a = article(title="Celebrity AI startup")

    demoted, breakdown = score_article(a, None, cfg, None, NOW)
    undemoted, _ = score_article(a, None, config(topics=[TOPIC_AI]), None, NOW)

    assert 0.0 < demoted < undemoted
    assert demoted == pytest.approx(
        (1 + breakdown["topic:ai"])
        * breakdown["demote:celebs"]
        * breakdown["source"]
        * breakdown["recency"]
    )


def test_demoted_articles_keep_their_recency_order() -> None:
    cfg = config(topics=[{**CELEBS, "weight": -10.0}])
    newer = article(title="Celebrity news", published_at=NOW - timedelta(hours=2))
    older = article(title="Celebrity news", published_at=NOW - timedelta(hours=30))

    newer_score = score_article(newer, None, cfg, None, NOW)[0]
    older_score = score_article(older, None, cfg, None, NOW)[0]

    assert newer_score > older_score > 0.0


@pytest.mark.parametrize(
    ("title", "summary"), [("Sponsored: AI roundup", None), ("AI roundup", "A sponsored post")]
)
def test_mute_keyword_in_title_or_summary_zeroes_the_score_but_keeps_the_breakdown(
    title: str, summary: str | None
) -> None:
    cfg = config(topics=[TOPIC_AI], mute_keywords=["sponsored"])
    a = article(title=title, summary=summary)
    score, breakdown = score_article(a, "Body text.", cfg, None, NOW)

    assert score == 0.0
    assert breakdown["muted"] == 1.0
    assert "topic:ai" in breakdown
    assert "mute_keyword_in_body" not in breakdown


def test_mute_keyword_only_in_body_halves_the_score_without_muting() -> None:
    cfg = config(topics=[TOPIC_AI], mute_keywords=["sponsored"])
    a = article(title="AI roundup")

    score, breakdown = score_article(a, "This post is sponsored.", cfg, None, NOW)
    unmentioned, _ = score_article(a, "This post is not.", cfg, None, NOW)

    assert "muted" not in breakdown
    assert breakdown["mute_keyword_in_body"] == 0.5
    assert score == pytest.approx(unmentioned * 0.5)
    assert score > 0.0


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


@pytest.mark.parametrize(
    ("scale", "title", "summary", "body", "expected"),
    [
        (0.0, "Celebrity wedding", None, None, 0.25),  # title still demotes fully
        (0.0, "Quiet", "celebrity guests", None, 1 / 3),  # summary too
        (0.0, "Quiet", None, "celebrity guests", 1.0),  # body only: ignored
        (0.5, "Quiet", None, "celebrity guests", 1 / 1.5),  # body only: half strength
        (1.0, "Quiet", None, "celebrity guests", 0.5),  # default
    ],
)
def test_body_only_demotion_scale(
    scale: float, title: str, summary: str | None, body: str, expected: float
) -> None:
    cfg = config(topics=[CELEBS], scoring={"body_only_demotion": scale})

    score, breakdown = score_article(article(title=title, summary=summary), body, cfg, None, NOW)

    assert score == pytest.approx(expected)
    body_only = title == "Quiet" and summary is None
    assert ("demote_body_only:celebs" in breakdown) == (body_only and scale < 1.0)
    assert ("demote:celebs" in breakdown) == (expected < 1.0)


def test_default_scoring_leaves_body_only_demotion_unmarked() -> None:
    _, breakdown = score_article(article(), "celebrity guests", config(topics=[CELEBS]), None, NOW)

    assert breakdown["demote:celebs"] == pytest.approx(0.5)
    assert not any(key.startswith("demote_body_only") for key in breakdown)


def test_body_only_demotion_must_be_between_0_and_1() -> None:
    with pytest.raises(ValueError):
        config(scoring={"body_only_demotion": 1.5})


def test_repo_config_weakens_body_only_demotions() -> None:
    cfg = load_interests(Path(__file__).resolve().parents[3] / "config" / "interests.yaml")
    plain_body = "Nothing of note happened."
    plain, _ = score_article(article(), plain_body, cfg, None, NOW)

    body_only, breakdown = score_article(article(), "Also: football results", cfg, None, NOW)
    in_title, _ = score_article(article(title="Football final"), plain_body, cfg, None, NOW)

    # Sport is weight -2: a body-only match counts a quarter, so 1 / (1 + 2 * 0.25)
    assert body_only == pytest.approx(plain * 2 / 3)
    assert breakdown["demote_body_only:sport"] == pytest.approx(0.25)
    # A title match keeps full strength: 1 / (1 + 2 * 3)
    assert in_title == pytest.approx(plain / 7)
