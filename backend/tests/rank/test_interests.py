"""Tests for loading ``config/interests.yaml``."""

from __future__ import annotations

from pathlib import Path

import pytest

from newsfeed.config import ConfigError, load_sources
from newsfeed.models import Source, SourceType
from newsfeed.rank.interests import (
    InterestsConfig,
    check_sources,
    keyword_pattern,
    load_interests,
)

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "interests.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_repo_config_is_valid() -> None:
    config = load_interests(CONFIG_DIR / "interests.yaml")
    assert check_sources(config, load_sources(CONFIG_DIR / "sources.yaml")) == []


def test_loads_every_part(tmp_path: Path) -> None:
    config = load_interests(
        write(
            tmp_path,
            """
topics:
  - {name: ai, keywords: [AI, "machine learning"], weight: 2.5}
  - {name: crypto, keywords: [bitcoin], weight: -1}
mute_keywords: [horoscope]
sources:
  a: {weight: 1.5}
  b: {mute: true}
recency: {half_life_hours: 12}
fields: {title: 5, body: 0.5}
""",
        )
    )
    assert [t.name for t in config.topics] == ["ai", "crypto"]
    assert config.topics[0].keywords == ["AI", "machine learning"]
    assert config.topics[1].weight == -1
    assert config.mute_keywords == ["horoscope"]
    assert config.sources["a"].weight == 1.5
    assert config.sources["b"].mute
    assert config.recency.half_life_hours == 12
    assert (config.fields.title, config.fields.summary, config.fields.body) == (5, 2, 0.5)


def test_defaults(tmp_path: Path) -> None:
    config = load_interests(write(tmp_path, ""))
    assert config == InterestsConfig()
    assert config.recency.half_life_hours == 24
    assert config.fields.title > config.fields.summary > config.fields.body


def test_missing_file_is_empty_config(tmp_path: Path) -> None:
    assert load_interests(tmp_path / "nope.yaml") == InterestsConfig()


def test_config_is_frozen() -> None:
    config = InterestsConfig()
    with pytest.raises(ValueError):
        config.topics = []


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("tpics: []", "tpics"),
        ("topics:\n  - {name: a, keywords: [x], weight: 1, colour: red}", "colour"),
        ("topics:\n  - {name: a, keywords: [], weight: 1}", "topics[0].keywords"),
        ("topics:\n  - {name: a, keywords: [' '], weight: 1}", "topics[0].keywords[0]"),
        ("topics:\n  - {name: a, keywords: [x]}", "weight"),
        ("topics:\n  - {name: a, keywords: [x], weight: lots}", "topics[0].weight"),
        ("topics:\n  - {name: a, keywords: [x, X], weight: 1}", "more than once"),
        (
            "topics:\n  - {name: a, keywords: [x], weight: 1}\n"
            "  - {name: A, keywords: [y], weight: 1}",
            "duplicate topic names",
        ),
        ("mute_keywords: [a, A]", "more than once"),
        ("mute_keywords: ['']", "mute_keywords[0]"),
        ("sources:\n  a: {weight: -1}", "sources.a.weight"),
        ("sources:\n  a: {}", "needs a weight"),
        ("sources:\n  a: {weight: 2, mute: true}", "use one or the other"),
        ("sources:\n  a: {mute: true, boost: 2}", "boost"),
        ("recency: {half_life_hours: 0}", "recency.half_life_hours"),
        ("recency: {half_life: 5}", "half_life"),
        ("fields: {title: -1}", "fields.title"),
        ("fields: {headline: 1}", "headline"),
        ("- not a mapping", "mapping"),
        ("topics:\n  - {name: a, keywords: [x], weight: .nan}", "topics[0].weight"),
        ("sources:\n  a: {weight: .nan}", "sources.a.weight"),
        ("recency: {half_life_hours: .nan}", "recency.half_life_hours"),
        ("fields: {title: .nan}", "fields.title"),
        ("fields: {summary: .nan}", "fields.summary"),
        ("fields: {body: .nan}", "fields.body"),
        ("topics:\n  - {name: a, keywords: [x], weight: .inf}", "topics[0].weight"),
        ("sources:\n  a: {weight: .inf}", "sources.a.weight"),
        ("recency: {half_life_hours: .inf}", "recency.half_life_hours"),
        ("fields: {title: .inf}", "fields.title"),
        ("fields: {summary: .inf}", "fields.summary"),
        ("fields: {body: .inf}", "fields.body"),
        ("topics:\n  - {name: a, keywords: [x], weight: -.inf}", "topics[0].weight"),
        ("sources:\n  a: {weight: -.inf}", "sources.a.weight"),
        ("recency: {half_life_hours: -.inf}", "recency.half_life_hours"),
        ("fields: {title: -.inf}", "fields.title"),
        ("fields: {summary: -.inf}", "fields.summary"),
        ("fields: {body: -.inf}", "fields.body"),
    ],
)
def test_bad_input_names_the_problem(tmp_path: Path, text: str, fragment: str) -> None:
    with pytest.raises(ConfigError, match=r"interests\.yaml") as exc:
        load_interests(write(tmp_path, text))
    assert fragment in str(exc.value)


def test_check_sources_reports_unknown_ids() -> None:
    config = InterestsConfig.model_validate(
        {"sources": {"known": {"weight": 2}, "zzz": {"mute": True}, "aaa": {"weight": 1}}}
    )
    sources = [Source(id="known", type=SourceType.HN), Source(id="other", type=SourceType.HN)]
    assert check_sources(config, sources) == ["aaa", "zzz"]
    assert check_sources(InterestsConfig(), sources) == []


@pytest.mark.parametrize(
    ("keyword", "text", "matches"),
    [
        ("ai", "AI safety news", True),
        ("ai", "Maintain the rail", False),
        ("machine learning", "Machine\n learning is hard", True),
        ("rust", "trust me", False),
        ("c++", "Why C++ lives on", True),
        ("c++", "abc++", False),
        ("open source", "an open-source tool", False),
    ],
)
def test_keyword_pattern(keyword: str, text: str, matches: bool) -> None:
    assert bool(keyword_pattern(keyword).search(text)) is matches
