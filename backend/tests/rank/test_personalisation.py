"""Tests for loading ``config/personalisation.yaml`` (#323)."""

from __future__ import annotations

from pathlib import Path

import pytest

from newsfeed.config import ConfigError
from newsfeed.rank.personalisation import load_personalisation

COMMITTED = Path(__file__).parents[3] / "config" / "personalisation.yaml"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "personalisation.yaml"
    path.write_text(text)
    return path


def test_a_missing_file_gives_the_defaults(tmp_path: Path) -> None:
    assert load_personalisation(tmp_path / "nope.yaml") == load_personalisation(write(tmp_path, ""))


def test_the_committed_file_loads_and_matches_the_defaults() -> None:
    assert load_personalisation(COMMITTED) == load_personalisation(COMMITTED.parent / "nope.yaml")


@pytest.mark.parametrize(
    "text",
    [
        "signals: {readd: 1.0}",
        "bogus: 1",
        "profile: {half_life_days: .nan}",
        "signals: {like: .inf}",
        "similarity: {beta: -.inf}",
    ],
    ids=["unknown-signal", "unknown-section", "nan", "inf", "-inf"],
)
def test_unknown_keys_and_non_finite_numbers_are_rejected(tmp_path: Path, text: str) -> None:
    with pytest.raises(ConfigError, match=r"personalisation\.yaml"):
        load_personalisation(write(tmp_path, text))
