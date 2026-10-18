"""Interest rules from ``config/interests.yaml`` (FR-7).

The file says what the reader cares about; the scoring stage (#81) turns it
into a score per article. It holds:

* ``topics``: named groups of keywords with a weight (negative to demote);
* ``mute_keywords``: phrases that mute an article;
* ``sources``: per source id, a ``weight`` multiplier or ``mute: true``;
* ``recency``: the half-life of the recency decay;
* ``fields``: how much a keyword match counts in the title, summary or body.

Keywords are case-insensitive words or phrases matched on word boundaries
(``keyword_pattern``), so ``ai`` matches "AI safety" but not "maintain".

The model lives here rather than in ``models.py``: rules are configuration,
not stored data, so they are not part of the contract (#96). A missing file
is an empty config, so the pipeline runs before any rules are written; an
invalid one fails at load time with the location named (ADR-0002).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Final, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from newsfeed.config import ConfigFile, load_config
from newsfeed.models import Source

__all__ = [
    "FieldWeights",
    "InterestsConfig",
    "Recency",
    "SourceRule",
    "Topic",
    "check_sources",
    "keyword_pattern",
    "load_interests",
]

DEFAULT_HALF_LIFE_HOURS: Final = 24.0

Keyword = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _Frozen(BaseModel):
    # allow_inf_nan=False: YAML's .nan/.inf would otherwise pass the ge/gt
    # bounds (or any bound-free field) and poison scores.
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


def keyword_pattern(keyword: str) -> re.Pattern[str]:
    """A case-insensitive pattern matching ``keyword`` as whole words.

    Runs of whitespace in a phrase match any whitespace. Boundaries use
    lookarounds on word characters rather than ``\\b``, so keywords that start
    or end with punctuation (``c++``, ``.net``) still match.
    """
    words = r"\s+".join(re.escape(word) for word in keyword.split())
    return re.compile(rf"(?<!\w){words}(?!\w)", re.IGNORECASE)


def _clean_keywords(keywords: list[str], what: str) -> list[str]:
    seen: dict[str, str] = {}
    for keyword in keywords:
        key = " ".join(keyword.split()).casefold()
        if key in seen:
            raise ValueError(f"{what} {keyword!r} is listed more than once")
        seen[key] = " ".join(keyword.split())
    return list(seen.values())


class Topic(_Frozen):
    """A named group of keywords that adds ``weight`` when one matches."""

    name: Keyword
    keywords: Annotated[list[Keyword], Field(min_length=1)]
    weight: float = Field(description="Points added on a match; negative demotes.")

    @field_validator("keywords")
    @classmethod
    def _unique_keywords(cls, keywords: list[str]) -> list[str]:
        return _clean_keywords(keywords, "keyword")


class SourceRule(_Frozen):
    """A reader's override for one source: a weight multiplier, or mute it."""

    weight: Annotated[float, Field(ge=0.0)] | None = Field(
        default=None, description="Replaces Source.weight from sources.yaml."
    )
    mute: bool = False

    @model_validator(mode="after")
    def _weight_or_mute(self) -> Self:
        if self.mute and self.weight is not None:
            raise ValueError("mute: true makes weight irrelevant; use one or the other")
        if not self.mute and self.weight is None:
            raise ValueError("needs a weight, or mute: true")
        return self


class Recency(_Frozen):
    """Exponential decay of the score with article age."""

    half_life_hours: Annotated[float, Field(gt=0.0)] = DEFAULT_HALF_LIFE_HOURS


class FieldWeights(_Frozen):
    """Multiplier for a keyword match in each part of an article."""

    title: Annotated[float, Field(ge=0.0)] = 3.0
    summary: Annotated[float, Field(ge=0.0)] = 2.0
    body: Annotated[float, Field(ge=0.0)] = 1.0


class InterestsConfig(ConfigFile):
    """Schema of ``config/interests.yaml``. Every part is optional."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    topics: list[Topic] = Field(default_factory=list)
    mute_keywords: list[Keyword] = Field(default_factory=list)
    sources: dict[str, SourceRule] = Field(default_factory=dict)
    recency: Recency = Field(default_factory=Recency)
    fields: FieldWeights = Field(default_factory=FieldWeights)

    @field_validator("mute_keywords")
    @classmethod
    def _unique_mutes(cls, keywords: list[str]) -> list[str]:
        return _clean_keywords(keywords, "mute keyword")

    @model_validator(mode="after")
    def _unique_topic_names(self) -> Self:
        counts = Counter(topic.name.casefold() for topic in self.topics)
        duplicates = sorted(name for name, n in counts.items() if n > 1)
        if duplicates:
            raise ValueError(f"duplicate topic names: {', '.join(duplicates)}")
        return self


def load_interests(path: Path) -> InterestsConfig:
    """Load the rules in ``path`` (normally ``config/interests.yaml``).

    A missing or empty file gives an empty config: articles then score on
    recency and source weight alone.

    Raises:
        ConfigError: if the file exists but is invalid (see ``load_config``).
    """
    if not path.exists():
        return InterestsConfig()
    return load_config(path, InterestsConfig)


def check_sources(config: InterestsConfig, sources: Iterable[Source]) -> list[str]:
    """Source ids in ``config.sources`` that ``sources`` doesn't define, sorted.

    A separate check rather than part of loading, so the file can be read
    without ``sources.yaml``. An empty result means every id is known.
    """
    known = {source.id for source in sources}
    return sorted(set(config.sources) - known)
