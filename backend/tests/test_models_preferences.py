"""Tests for the reader-preferences contract in ``newsfeed.models`` (#334)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from newsfeed.models import DEFAULT_USER_ID, PreferenceState, ReaderPreferences

NOW = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)


def test_preference_states_are_the_four_menu_choices() -> None:
    assert [s.value for s in PreferenceState] == ["hide", "fewer", "show", "more"]


def test_new_preferences_hold_no_overrides() -> None:
    prefs = ReaderPreferences(updated_at=NOW)
    assert prefs.user_id == DEFAULT_USER_ID
    assert (prefs.sources, prefs.topics) == ({}, {})
    assert (prefs.setup_done_at, prefs.version) == (None, 0)


@pytest.mark.parametrize(
    "fields",
    [
        {"topics": {"Sport!": "fewer"}},
        {"topics": {"Sport": "fewer"}},
        {"sources": {"-bbc": "hide"}},
        {"sources": {"": "hide"}},
        {"topics": {"sport": "mute"}},
        {"version": -1},
        {"updated_at": datetime(2026, 11, 17, 10, 0)},
        {"user_id": "Owner@example.com"},
        {"regions": {"uk": "more"}},
    ],
    ids=[
        "topic-punctuation",
        "topic-uppercase",
        "source-leading-dash",
        "source-empty",
        "unknown-state",
        "negative-version",
        "naive-updated-at",
        "user-id-not-a-slug",
        "no-regions-map",
    ],
)
def test_invalid_preferences_are_rejected(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ReaderPreferences.model_validate({"updated_at": NOW, **fields})


def test_slug_keys_and_state_strings_are_accepted() -> None:
    prefs = ReaderPreferences.model_validate(
        {"updated_at": NOW, "sources": {"bbc-news": "hide"}, "topics": {"ai_policy2": "more"}}
    )
    assert prefs.sources == {"bbc-news": PreferenceState.HIDE}
    assert prefs.topics == {"ai_policy2": PreferenceState.MORE}
