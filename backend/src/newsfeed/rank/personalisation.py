"""Weights for the learned interest profile, from ``config/personalisation.yaml`` (#323).

The file says how much each kind of feedback counts, how the profile is built
from it, and how strongly similarity to the profile lifts an article in the
personalised order (ADR-0014). Like ``interests.yaml``, it is configuration
rather than stored data, so the model lives here, not in ``models.py``. A
missing file means the defaults; an unknown key, NaN or infinity fails at load
time with the file named (ADR-0002).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from newsfeed.config import ConfigFile, load_config

__all__ = [
    "PersonalisationConfig",
    "ProfileSettings",
    "Signals",
    "SimilaritySettings",
    "load_personalisation",
]


class _Section(BaseModel):
    # allow_inf_nan=False: YAML's .nan and .inf would otherwise pass the bounds.
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Signals(_Section):
    """The weight of one feedback event, before time decay."""

    read: float = Field(default=1.0, ge=0, description="A full read; scaled by seconds read.")
    read_full_seconds: float = Field(default=120.0, gt=0, description="Seconds that count as full.")
    open: float = Field(default=0.3, ge=0)
    like: float = Field(default=2.0, ge=0)
    dismiss: float = Field(default=-1.0, le=0, description="Goes to the negative vector.")


class ProfileSettings(_Section):
    """How the profile is built from weighted stories."""

    window_days: float = Field(default=180.0, gt=0, description="How far back feedback is read.")
    half_life_days: float = Field(default=45.0, gt=0)
    max_centroids: int = Field(default=4, ge=1)
    events_per_centroid: int = Field(default=15, ge=1)
    min_events: int = Field(default=10, ge=1, description="Fewer positive stories: no centroids.")
    rebuild_min_hours: float = Field(default=6.0, ge=0)


class SimilaritySettings(_Section):
    """How similarity to the profile becomes a factor."""

    beta: float = Field(default=2.0, ge=0, description="The top article's factor is 1 + beta.")
    negative_weight: float = Field(default=0.5, ge=0)


class PersonalisationConfig(ConfigFile):
    """Schema of ``config/personalisation.yaml``; every field has a default."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    signals: Signals = Field(default_factory=Signals)
    profile: ProfileSettings = Field(default_factory=ProfileSettings)
    similarity: SimilaritySettings = Field(default_factory=SimilaritySettings)


def load_personalisation(path: Path) -> PersonalisationConfig:
    """Load ``path`` (normally ``config/personalisation.yaml``); missing means the defaults.

    Raises:
        ConfigError: if the file exists but is invalid; the message names it.
    """
    if not path.exists():
        return PersonalisationConfig()
    return load_config(path, PersonalisationConfig)
