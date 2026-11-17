"""Contract tests for reader preferences (#334).

Every ``Repository`` keeps one ``ReaderPreferences`` record per ``user_id``:
``get_preferences`` returns None until one is saved, and ``save_preferences``
inserts or replaces by ``user_id``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from newsfeed.models import PreferenceState, ReaderPreferences
from newsfeed.ports import Repository
from tests.contract.conftest import RepositoryOpener

T0 = datetime(2026, 11, 17, 10, 0, 0, 123456, tzinfo=UTC)


def owner_preferences(**overrides: object) -> ReaderPreferences:
    """The acceptance example's preferences, with every optional field set."""
    return ReaderPreferences.model_validate(
        {
            "sources": {"bbc-news": "hide", "example-wire": "more"},
            "topics": {"sport": "fewer"},
            "setup_done_at": datetime(2026, 11, 16, 8, 0, tzinfo=UTC),
            "updated_at": T0,
            "version": 4,
            **overrides,
        }
    )


def test_get_preferences_is_none_until_saved(repo: Repository) -> None:
    assert repo.get_preferences("owner") is None


def test_saved_preferences_read_back_equal(repo: Repository) -> None:
    prefs = owner_preferences()
    repo.save_preferences(prefs)

    stored = repo.get_preferences("owner")
    assert stored == prefs
    assert stored is not None
    assert stored.sources["bbc-news"] is PreferenceState.HIDE
    assert stored.topics["sport"] is PreferenceState.FEWER
    assert repo.get_preferences("someone-else") is None


def test_save_preferences_replaces_by_user_id(repo: Repository) -> None:
    repo.save_preferences(owner_preferences())
    guest = owner_preferences(user_id="guest", topics={"science": "more"})
    repo.save_preferences(guest)
    # Replacing, not merging: a key cleared back to the default is gone.
    cleared = ReaderPreferences(updated_at=T0, version=5)
    repo.save_preferences(cleared)

    assert repo.get_preferences("owner") == cleared
    assert repo.get_preferences("guest") == guest


def test_returned_preferences_are_a_copy(repo: Repository) -> None:
    prefs = owner_preferences()
    repo.save_preferences(prefs)
    fetched = repo.get_preferences("owner")
    assert fetched is not None
    fetched.sources["bbc-news"] = PreferenceState.SHOW
    fetched.topics.clear()
    assert repo.get_preferences("owner") == prefs


def test_preferences_persist_across_reopening(open_repository: RepositoryOpener) -> None:
    prefs = owner_preferences()
    open_repository().save_preferences(prefs)
    assert open_repository().get_preferences("owner") == prefs
