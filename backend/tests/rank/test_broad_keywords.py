"""Broad topic keywords must not mark unrelated stories (#390)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from newsfeed.models import Article
from newsfeed.rank.interests import load_interests
from newsfeed.rank.scoring import score_article

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
CONFIG = Path(__file__).resolve().parents[3] / "config" / "interests.yaml"


def about(title: str) -> set[str]:
    """Topics the committed interests mark an invented title as being about."""
    a = Article.model_validate(
        {
            "canonical_url": "https://news.example.com/a",
            "url": "https://news.example.com/a",
            "source_id": "gazette",
            "title": title,
            "fetched_at": NOW,
        }
    )
    _, breakdown = score_article(a, None, load_interests(CONFIG), None, NOW)
    return {key.removeprefix("about:") for key in breakdown if key.startswith("about:")}


@pytest.mark.parametrize(
    ("title", "topic"),
    [
        ("Fears of an AI takeover grow", "business"),
        ("Robots and the takeover of the factory floor", "business"),
        ("Road closures planned for the village festival", "culture"),
        ("Plumber turned actor wins tax tribunal", "culture"),
    ],
)
def test_broad_single_word_does_not_mark_an_unrelated_title(title: str, topic: str) -> None:
    assert topic not in about(title)


@pytest.mark.parametrize(
    ("title", "topic"),
    [
        ("Rival lodges takeover bid for retailer", "business"),
        ("Takeover talks collapse at the retailer", "business"),
        ("Orchestra announces a concert series", "culture"),
    ],
)
def test_specific_phrase_still_marks_the_topic(title: str, topic: str) -> None:
    assert topic in about(title)
