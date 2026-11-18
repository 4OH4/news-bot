"""Tests for the preferences defaults config and the rules that apply it (#335)."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from newsfeed.config import ConfigError, load_sources
from newsfeed.models import ReaderPreferences, Source, SourceType
from newsfeed.preferences import (
    PreferencesConfig,
    effective_states,
    item_effect,
    load_preferences_config,
    menu,
)

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
NOW = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)

BASE: dict[str, Any] = {
    "multipliers": {"hide": 0, "fewer": 0.5, "show": 1.0, "more": 2.0},
    "max_factor": 4.0,
    "min_factor": 0.25,
    "source_sections": [
        {"id": "news", "label": "News sources", "tag": "news"},
        {"id": "tech", "label": "Tech", "tag": "tech"},
        {"id": "science", "label": "Science", "tag": "science"},
        {"id": "more", "label": "More sources"},
        {"id": "long-reads", "label": "Long Reads", "tag": "long-form"},
    ],
    "claim_order": ["news", "long-reads", "tech", "science"],
    "sources": {"bbc-news": {"label": "BBC News"}},
    "topics": {
        "politics": {"label": "Politics", "covers": ["politics"]},
        "tech": {"label": "Tech", "covers": ["ai", "software"]},
        "science": {"label": "Science", "covers": ["science", "space"]},
        "sport": {"label": "Sport", "covers": ["sport"]},
        "ai": {"label": "AI", "covers": ["ai"]},
    },
    "starter": {"sources": ["bbc-news", "quanta"], "topics": ["sport", "politics", "tech"]},
}


def src(id_: str, *tags: str, enabled: bool = True) -> Source:
    return Source(
        id=id_,
        type=SourceType.RSS,
        url=f"https://{id_}.example/feed",
        tags=list(tags),
        enabled=enabled,
    )


NO_STARTER: dict[str, list[str]] = {"sources": [], "topics": []}

SOURCES = [
    src("bbc-news", "news", "uk"),
    src("quanta", "science", "long-form"),
    src("ars", "tech"),
    src("nature", "science"),
    src("blogger", "blog", "economics"),
    src("example-off", "news", enabled=False),
]


def make_config(
    tmp_path: Path, source_list: list[Source] = SOURCES, **changes: Any
) -> PreferencesConfig:
    data = copy.deepcopy(BASE)
    data.update(changes)
    path = tmp_path / "preferences.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return load_preferences_config(path, source_list)


@pytest.fixture
def config(tmp_path: Path) -> PreferencesConfig:
    return make_config(tmp_path)


def prefs(**kw: dict[str, str]) -> ReaderPreferences:
    return ReaderPreferences(updated_at=NOW, **kw)  # type: ignore[arg-type]


def effect(
    config: PreferencesConfig,
    overrides: ReaderPreferences | None,
    source_id: str,
    **breakdown: float,
) -> tuple[bool, dict[str, float]]:
    item = {
        "source_id": source_id,
        "score_breakdown": {k.replace("__", ":"): v for k, v in breakdown.items()},
    }
    return item_effect(item, effective_states(config, overrides), config)


def section_ids(config: PreferencesConfig) -> dict[str, list[str]]:
    states = effective_states(config, None)
    return {
        s["id"]: [x["id"] for x in s["sources"]]
        for s in menu(config, states).model_dump()["sections"]
    }


# --- Loading and sections ------------------------------------------------------


def test_committed_config_places_every_enabled_source_in_its_section() -> None:
    sources = load_sources(CONFIG_DIR / "sources.yaml")
    config = load_preferences_config(CONFIG_DIR / "preferences.yaml", sources)
    sections = section_ids(config)
    assert list(sections) == ["news", "tech", "science", "more", "long-reads"]
    assert sections["news"] == [
        "bbc-news", "guardian-world", "independent-uk", "inews",
        "al-jazeera-english", "dw-news", "business-insider",
    ]  # fmt: skip
    assert sections["tech"] == [
        "ars-technica", "the-verge", "wired", "lwn", "404media", "mit-tech-review",
        "simon-willison", "julia-evans", "martin-fowler", "cloudflare-blog",
    ]  # fmt: skip
    assert sections["science"] == ["nature-news", "phys-org", "carbon-brief"]
    assert sections["more"] == ["noahpinion"]
    assert sections["long-reads"] == [
        "quanta", "bbc-future", "guardian-longread", "aeon", "new-yorker",
    ]  # fmt: skip
    placed = [s for ids in sections.values() for s in ids]
    assert sorted(placed) == sorted(s.id for s in sources if s.enabled)
    assert not {"example-google-news", "example-hacker-news"} & set(placed)


@pytest.mark.parametrize(
    ("tags", "section"),
    [
        (["news", "long-form"], "news"),
        (["science", "long-form"], "long-reads"),
        (["long-form", "tech"], "long-reads"),
        (["tech", "science"], "tech"),
        (["blog", "economics"], "more"),
    ],
)
def test_source_joins_first_claiming_section(tmp_path: Path, tags: list[str], section: str) -> None:
    config = make_config(tmp_path, [src("probe", *tags)], starter=NO_STARTER, sources={})
    assert config.section_of["probe"] == section


def test_disabled_sources_are_not_selectable(config: PreferencesConfig) -> None:
    assert "example-off" not in config.section_of
    assert "example-off" not in {i for ids in section_ids(config).values() for i in ids}


def test_menu_leaves_out_empty_sections(tmp_path: Path) -> None:
    config = make_config(
        tmp_path,
        [s for s in SOURCES if s.id not in {"nature", "quanta"}],
        starter={"sources": ["bbc-news"], "topics": []},
    )
    assert "science" not in section_ids(config)


@pytest.mark.parametrize(
    "changes",
    [
        {"starter": {"sources": ["example-off"], "topics": []}},
        {"starter": {"sources": ["nobody"], "topics": []}},
        {"sources": {"nobody": {"label": "Nobody"}}},
        {"claim_order": ["news", "no-such-section"]},
        {"claim_order": ["news", "more"]},
        {"multipliers": {"hide": 0, "fewer": -0.5, "show": 1.0, "more": 2.0}},
        {"multipliers": {"hide": 0.1, "fewer": 0.5, "show": 1.0, "more": 2.0}},
        {"min_factor": 1.5},
        {"max_factor": 0.5},
        {"max_factor": float("inf")},
        {"unknown_key": 1},
    ],
    ids=lambda c: next(iter(c)) + "-" + str(next(iter(c.values())))[:30],
)
def test_invalid_config_is_rejected(tmp_path: Path, changes: dict[str, Any]) -> None:
    with pytest.raises((ConfigError, ValueError)):
        make_config(tmp_path, **changes)


def test_two_sections_without_a_tag_are_rejected(tmp_path: Path) -> None:
    sections = [*BASE["source_sections"], {"id": "extra", "label": "Extra"}]
    with pytest.raises((ConfigError, ValueError)):
        make_config(tmp_path, source_sections=sections)


def test_disabled_starter_source_error_names_it(tmp_path: Path) -> None:
    with pytest.raises((ConfigError, ValueError), match="example-off"):
        make_config(tmp_path, starter={"sources": ["example-off"], "topics": []})


# --- effective_states -------------------------------------------------------------


def test_no_overrides_means_everything_shows(config: PreferencesConfig) -> None:
    m = menu(config, effective_states(config, None)).model_dump()
    states = [x["state"] for s in m["sections"] for x in s["sources"]] + [
        t["state"] for t in m["topics"]
    ]
    assert states and set(states) == {"show"}
    assert effect(config, None, "bbc-news", about__sport=1.0, demote__sport=0.2) == (False, {})
    assert effect(config, prefs(), "quanta") == (False, {})


def test_overrides_for_unknown_ids_are_ignored(config: PreferencesConfig) -> None:
    overrides = prefs(sources={"gone": "hide"}, topics={"gone-topic": "hide"})
    states = effective_states(config, overrides)
    assert menu(config, states) == menu(config, effective_states(config, None))
    assert effect(config, overrides, "bbc-news") == (False, {})


def test_menu_reflects_overrides_and_starter_flags(config: PreferencesConfig) -> None:
    m = menu(config, effective_states(config, prefs(sources={"bbc-news": "hide"}))).model_dump()
    sources = {x["id"]: x for s in m["sections"] for x in s["sources"]}
    assert sources["bbc-news"]["state"] == "hide"
    assert sources["bbc-news"]["label"] == "BBC News"
    assert sources["ars"]["label"] == "ars"  # no label configured: shows its id
    assert {i for i, x in sources.items() if x["starter"]} == {"bbc-news", "quanta"}
    assert m["multipliers"] == BASE["multipliers"]


def test_menu_lists_starter_topics_first_in_starter_order(config: PreferencesConfig) -> None:
    topics = menu(config, effective_states(config, None)).model_dump()["topics"]
    assert [t["id"] for t in topics] == ["sport", "politics", "tech", "science", "ai"]
    assert [t["starter"] for t in topics] == [True, True, True, False, False]


# --- item_effect ----------------------------------------------------------------


def test_hidden_source_hides_its_items(config: PreferencesConfig) -> None:
    hidden, _ = effect(config, prefs(sources={"bbc-news": "hide"}), "bbc-news")
    assert hidden


def test_fewer_topic_halves_and_keeps_the_demotion(config: PreferencesConfig) -> None:
    result = effect(
        config, prefs(topics={"sport": "fewer"}), "bbc-news", about__sport=1.0, demote__sport=0.2
    )
    assert result == (False, {"pref:topic:sport": 0.5})


def test_more_topic_lifts_the_rules_demotion(config: PreferencesConfig) -> None:
    hidden, factors = effect(
        config, prefs(topics={"sport": "more"}), "bbc-news", about__sport=1.0, demote__sport=0.2
    )
    assert not hidden
    assert factors == pytest.approx({"pref:topic:sport": 2.0, "pref:undemote:sport": 5.0})


def test_body_only_match_is_not_about_the_topic(config: PreferencesConfig) -> None:
    assert effect(config, prefs(topics={"sport": "more"}), "bbc-news", demote__sport=0.5) == (
        False,
        {},
    )


def test_undemote_applies_once_when_two_more_topics_cover_the_topic(
    config: PreferencesConfig,
) -> None:
    _, factors = effect(
        config, prefs(topics={"tech": "more", "ai": "more"}), "ars", about__ai=1.0, demote__ai=0.5
    )
    assert factors == pytest.approx(
        {"pref:topic:tech": 2.0, "pref:topic:ai": 2.0, "pref:undemote:ai": 2.0}
    )
    assert "pref:clamp" not in factors  # exactly at the clamp


def test_product_of_owner_multipliers_is_clamped(config: PreferencesConfig) -> None:
    overrides = prefs(topics={"tech": "more", "science": "more"}, sources={"quanta": "more"})
    _, factors = effect(config, overrides, "quanta", about__ai=1.0, about__space=1.0)
    assert factors["pref:clamp"] == pytest.approx(0.5)
    assert (
        factors["pref:source:quanta"] * factors["pref:topic:tech"] * factors["pref:topic:science"]
        == 8.0
    )


def test_hide_wins_over_more(config: PreferencesConfig) -> None:
    overrides = prefs(topics={"sport": "hide", "tech": "more"})
    hidden, _ = effect(config, overrides, "bbc-news", about__sport=1.0, about__ai=1.0)
    assert hidden
