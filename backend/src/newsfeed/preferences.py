"""Reader preference defaults and the pure rules that apply them (ADR-0016, #335).

``config/preferences.yaml`` says which sources and topics the preferences menu
offers, which section each source sits in, the starter set, and what Hide,
Fewer, Show and More do to a score. The reader's own choices are stored as
overrides (``models.ReaderPreferences``); the functions here combine the two:

* ``load_preferences_config`` validates the file against the enabled sources
  and places each source in a section;
* ``effective_states`` resolves every source and topic to a state;
* ``item_effect`` says whether an item is hidden and which factors scale it;
* ``menu`` builds what the API returns.

Everything is pure; the API (#337) only calls these.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from newsfeed.config import ConfigError, ConfigFile, load_config
from newsfeed.logging import get_logger
from newsfeed.models import PreferenceState, ReaderPreferences, Source
from newsfeed.rank.interests import load_interests

__all__ = [
    "EffectiveStates",
    "Menu",
    "PreferencesConfig",
    "effective_states",
    "item_effect",
    "load_preferences_config",
    "menu",
]

log = get_logger(__name__)


class _Frozen(BaseModel):
    # allow_inf_nan=False: YAML's .nan/.inf would otherwise poison scores.
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Multipliers(_Frozen):
    """What each state does to a score. Hide is always 0 (the item is removed)."""

    hide: float = Field(ge=0)
    fewer: float = Field(ge=0.01)
    show: float = Field(ge=0)
    more: float = Field(ge=0.01)

    @model_validator(mode="after")
    def _hide_is_zero(self) -> Self:
        if self.hide != 0:
            raise ValueError("multipliers.hide must be 0")
        return self

    def of(self, state: PreferenceState) -> float:
        """The multiplier for ``state``."""
        return float(getattr(self, state.value))


class SectionDef(_Frozen):
    """A source section: ``tag`` is what claims a source for it; none means catch-all."""

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    tag: str | None = None


class SourceLabel(_Frozen):
    """Display label for one source."""

    label: str = Field(min_length=1)


class TopicDef(_Frozen):
    """A preference topic: its label and the ``interests.yaml`` topics it covers."""

    label: str = Field(min_length=1)
    covers: list[str] = Field(min_length=1)


class Starter(_Frozen):
    """The suggested starter set of sources and topics."""

    sources: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)


class _RawConfig(ConfigFile):
    """Schema of ``config/preferences.yaml`` before it is checked against the sources."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    multipliers: Multipliers
    max_factor: float
    min_factor: float
    source_sections: list[SectionDef]
    claim_order: list[str]
    sources: dict[str, SourceLabel] = Field(default_factory=dict)
    topics: dict[str, TopicDef]
    starter: Starter = Field(default_factory=Starter)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not (math.isfinite(self.max_factor) and math.isfinite(self.min_factor)):
            raise ValueError("min_factor and max_factor must be finite")
        if self.min_factor <= 0 or self.min_factor > 1:
            raise ValueError("min_factor must be in (0, 1]")
        if self.max_factor < 1:
            raise ValueError("max_factor must be at least 1")
        ids = [s.id for s in self.source_sections]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate section ids")
        untagged = [s.id for s in self.source_sections if s.tag is None]
        if len(untagged) != 1:
            raise ValueError(f"exactly one section must have no tag, found {len(untagged)}")
        tagged = {s.id for s in self.source_sections if s.tag is not None}
        for section_id in self.claim_order:
            if section_id not in ids:
                raise ValueError(f"claim_order names unknown section {section_id!r}")
            if section_id not in tagged:
                raise ValueError(f"claim_order names section {section_id!r}, which has no tag")
        unclaimed = sorted(tagged - set(self.claim_order))
        if unclaimed:
            raise ValueError(f"tagged sections missing from claim_order: {', '.join(unclaimed)}")
        for topic_id in self.starter.topics:
            if topic_id not in self.topics:
                raise ValueError(f"starter topic {topic_id!r} is not a configured topic")
        return self


class PreferencesConfig(_Frozen):
    """The validated defaults, with each enabled source's section resolved."""

    multipliers: Multipliers
    max_factor: float
    min_factor: float
    sections: list[SectionDef]
    source_labels: dict[str, str]
    topics: dict[str, TopicDef]
    starter_sources: list[str]
    starter_topics: list[str]
    source_order: list[str] = Field(description="Enabled source ids, in sources.yaml order.")
    section_of: Mapping[str, str] = Field(description="Enabled source id to section id.")


def _section_for(source: Source, raw: _RawConfig) -> str:
    by_id = {s.id: s for s in raw.source_sections}
    for section_id in raw.claim_order:
        if by_id[section_id].tag in source.tags:
            return section_id
    return next(s.id for s in raw.source_sections if s.tag is None)


def _warn_unknown_covers(path: Path, raw: _RawConfig) -> None:
    """Warn (not fail) on covered topics missing from a sibling interests.yaml."""
    interests_path = path.with_name("interests.yaml")
    if not interests_path.is_file():
        return
    try:
        known = {t.name for t in load_interests(interests_path).topics}
    except ConfigError:
        return
    for topic_id, topic in raw.topics.items():
        for covered in topic.covers:
            if covered not in known:
                log.warning(
                    "preference topic covers a topic missing from interests.yaml",
                    extra={"stage": "preferences", "topic": topic_id, "covers": covered},
                )


def load_preferences_config(path: Path, sources: Collection[Source]) -> PreferencesConfig:
    """Load and validate ``config/preferences.yaml`` against the configured sources.

    Only enabled sources are selectable. Raises ``ConfigError`` for a bad file,
    a starter naming a source that isn't enabled, a label naming one that isn't
    configured, and so on.
    """
    raw = load_config(path, _RawConfig)
    enabled = [s for s in sources if s.enabled]
    enabled_ids = {s.id for s in enabled}
    for source_id in raw.starter.sources:
        if source_id not in enabled_ids:
            raise ConfigError(f"{path}: starter source {source_id!r} is not an enabled source")
    # Labels may name disabled sources, so disabling one in sources.yaml doesn't
    # also force an edit here; only ids absent from sources.yaml are an error.
    configured_ids = {s.id for s in sources}
    for source_id in raw.sources:
        if source_id not in configured_ids:
            raise ConfigError(f"{path}: label for {source_id!r}, which is not a configured source")
    _warn_unknown_covers(path, raw)
    return PreferencesConfig(
        multipliers=raw.multipliers,
        max_factor=raw.max_factor,
        min_factor=raw.min_factor,
        sections=raw.source_sections,
        source_labels={i: v.label for i, v in raw.sources.items()},
        topics=raw.topics,
        starter_sources=raw.starter.sources,
        starter_topics=raw.starter.topics,
        source_order=[s.id for s in enabled],
        section_of={s.id: _section_for(s, raw) for s in enabled},
    )


class EffectiveStates(_Frozen):
    """The state of every configured source and topic (override, else Show)."""

    sources: dict[str, PreferenceState]
    topics: dict[str, PreferenceState]


def effective_states(
    config: PreferencesConfig, overrides: ReaderPreferences | None
) -> EffectiveStates:
    """Resolve each source and topic to its state; overrides for unknown ids are ignored."""
    src = overrides.sources if overrides else {}
    top = overrides.topics if overrides else {}
    return EffectiveStates(
        sources={i: src.get(i, PreferenceState.SHOW) for i in config.source_order},
        topics={i: top.get(i, PreferenceState.SHOW) for i in config.topics},
    )


def item_effect(
    item: Mapping[str, Any], states: EffectiveStates, config: PreferencesConfig
) -> tuple[bool, dict[str, float]]:
    """Whether ``item`` is hidden, and the factors that scale its score.

    Hide wins over everything. Otherwise each Fewer or More source or topic gives
    a ``pref:source:<id>`` or ``pref:topic:<id>`` factor; their product is clamped
    to ``[min_factor, max_factor]`` (``pref:clamp`` records the scaling). A topic
    set to More also lifts the rules' demotion (``pref:undemote:<t>``), outside
    the clamp, so More on a demoted topic such as sport actually promotes it.
    """
    breakdown: Mapping[str, float] = item.get("score_breakdown") or {}
    source_state = states.sources.get(str(item.get("source_id")), PreferenceState.SHOW)
    if source_state is PreferenceState.HIDE:
        return True, {}

    about = {
        topic_id: [t for t in topic.covers if f"about:{t}" in breakdown]
        for topic_id, topic in config.topics.items()
    }
    factors: dict[str, float] = {}
    if source_state is not PreferenceState.SHOW:
        factors[f"pref:source:{item['source_id']}"] = config.multipliers.of(source_state)
    undemote: dict[str, float] = {}
    for topic_id, matched in about.items():
        if not matched:
            continue
        state = states.topics.get(topic_id, PreferenceState.SHOW)
        if state is PreferenceState.HIDE:
            return True, {}
        if state is PreferenceState.SHOW:
            continue
        factors[f"pref:topic:{topic_id}"] = config.multipliers.of(state)
        if state is PreferenceState.MORE:
            for t in matched:
                demote = breakdown.get(f"demote:{t}")
                if demote:
                    undemote[f"pref:undemote:{t}"] = 1 / demote

    product = math.prod(factors.values())
    if product > config.max_factor:
        factors["pref:clamp"] = config.max_factor / product
    elif product < config.min_factor:
        factors["pref:clamp"] = config.min_factor / product
    factors.update(undemote)
    return False, factors


class MenuSource(_Frozen):
    """One selectable source in the menu."""

    id: str
    label: str
    state: PreferenceState
    starter: bool


class MenuSection(_Frozen):
    """A section of sources in the menu."""

    id: str
    label: str
    sources: list[MenuSource]


class MenuTopic(_Frozen):
    """One selectable topic in the menu."""

    id: str
    label: str
    state: PreferenceState
    starter: bool


class Menu(_Frozen):
    """What the preferences API returns: sections, topics and the multipliers."""

    sections: list[MenuSection]
    topics: list[MenuTopic]
    multipliers: Multipliers


def menu(config: PreferencesConfig, states: EffectiveStates) -> Menu:
    """Build the menu: sections in screen order (empty ones left out), starter topics first."""
    starter_sources = set(config.starter_sources)
    sections = []
    for section in config.sections:
        members = [
            MenuSource(
                id=i,
                label=config.source_labels.get(i, i),
                state=states.sources.get(i, PreferenceState.SHOW),
                starter=i in starter_sources,
            )
            for i in config.source_order
            if config.section_of[i] == section.id
        ]
        if members:
            sections.append(MenuSection(id=section.id, label=section.label, sources=members))
    topic_ids = [
        *config.starter_topics,
        *(t for t in config.topics if t not in config.starter_topics),
    ]
    topics = [
        MenuTopic(
            id=t,
            label=config.topics[t].label,
            state=states.topics.get(t, PreferenceState.SHOW),
            starter=t in config.starter_topics,
        )
        for t in topic_ids
    ]
    return Menu(sections=sections, topics=topics, multipliers=config.multipliers)
