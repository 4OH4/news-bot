"""Tests for the ``about:<topic>`` markers and the weight 0 topics (#336)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from newsfeed.models import Article
from newsfeed.rank.interests import InterestsConfig, load_interests
from newsfeed.rank.scoring import score_article

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
NEW_TOPICS = {"politics", "business", "health", "culture"}

SPORT = {"name": "sport", "keywords": ["football", "Premier League"], "weight": -2.0}
POLITICS = {"name": "politics", "keywords": ["election", "parliament"], "weight": 0}


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


def score(a: Article, cfg: InterestsConfig, body: str | None = None) -> tuple[float, dict]:
    return score_article(a, body, cfg, None, NOW)


def test_demoted_topic_matched_in_title_has_about_marker_and_same_demotion() -> None:
    cfg = InterestsConfig.model_validate({"topics": [SPORT]})
    _, breakdown = score(article(title="Premier League preview"), cfg)

    assert breakdown["about:sport"] == 1.0
    assert breakdown["demote:sport"] == pytest.approx(1 / (1 + 2 * 3.0))


def test_body_only_match_has_no_about_marker_and_keeps_demotion() -> None:
    cfg = InterestsConfig.model_validate({"topics": [SPORT]})
    _, breakdown = score(article(), cfg, body="Later, football was mentioned")

    assert "about:sport" not in breakdown
    assert breakdown["demote:sport"] == pytest.approx(1 / (1 + 2 * 1.0))


def test_weight_zero_topic_in_summary_marks_without_changing_score() -> None:
    with_topic = InterestsConfig.model_validate({"topics": [POLITICS]})
    a = article(summary="An election is called")

    score_with, breakdown = score(a, with_topic)
    score_without, _ = score(a, InterestsConfig())

    assert breakdown["about:politics"] == 1.0
    assert "topic:politics" not in breakdown
    assert not any(key.startswith("demote:") for key in breakdown)
    assert score_with == pytest.approx(score_without)


def test_committed_interests_cover_every_preferences_topic() -> None:
    cfg = load_interests(CONFIG_DIR / "interests.yaml")
    prefs = yaml.safe_load((CONFIG_DIR / "preferences.yaml").read_text())
    names = {topic.name for topic in cfg.topics}

    covered = {name for entry in prefs["topics"].values() for name in entry["covers"]}
    assert covered <= names
    assert names >= NEW_TOPICS
    assert all(t.weight == 0 for t in cfg.topics if t.name in NEW_TOPICS)


SYNTHETIC = [
    ("The election result surprises parliament", "A new minister is named", None),
    ("Premier League title race", "Football club sign a striker", "shares fell"),
    ("Hospital waiting lists grow", "The NHS warns of winter", "doctors and vaccine"),
    ("New film wins the festival", "A novel adaptation at the theatre", None),
    ("Company profits rise", "Investors welcome the merger", "the CEO said"),
    ("Linux kernel release", "Maintainers ship a patch", "the senate was not involved"),
    ("A quiet day", None, None),
]


@pytest.mark.parametrize(("title", "summary", "body"), SYNTHETIC)
def test_new_weight_zero_topics_change_no_score_only_add_about_keys(
    title: str, summary: str | None, body: str | None
) -> None:
    full = load_interests(CONFIG_DIR / "interests.yaml")
    assert {t.name for t in full.topics} >= NEW_TOPICS
    trimmed = full.model_copy(
        update={"topics": [t for t in full.topics if t.name not in NEW_TOPICS]}
    )
    a = article(title=title, summary=summary)

    score_full, breakdown_full = score(a, full, body)
    score_trimmed, breakdown_trimmed = score(a, trimmed, body)

    assert score_full == pytest.approx(score_trimmed)
    extra = set(breakdown_full) - set(breakdown_trimmed)
    assert extra <= {f"about:{name}" for name in NEW_TOPICS}
    assert {k: v for k, v in breakdown_full.items() if k not in extra} == breakdown_trimmed


def test_new_topics_mark_the_articles_they_are_about() -> None:
    full = load_interests(CONFIG_DIR / "interests.yaml")
    expected = {
        "politics": "The election result surprises parliament",
        "health": "Hospital waiting lists grow",
        "culture": "New film wins the festival",
        "business": "Company profits rise",
    }
    for topic, title in expected.items():
        assert score(article(title=title), full)[1].get(f"about:{topic}") == 1.0


def test_the_word_author_alone_does_not_mark_an_article_as_culture() -> None:
    full = load_interests(CONFIG_DIR / "interests.yaml")
    a = article(title="The author of the report spoke to reporters")

    assert "about:culture" not in score(a, full)[1]
