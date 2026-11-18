"""The preferences cache, the overrides a PUT stores, and the routes without a config (#337)."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from newsfeed.api.preferences import CACHE_SECONDS, PreferencesService
from newsfeed.config import load_sources
from newsfeed.models import DEFAULT_USER_ID, PreferenceState, ReaderPreferences
from newsfeed.preferences import PreferencesConfig, load_preferences_config
from tests.api.support import OWNER_EMAIL, auth, client_for, identities
from tests.fakes import FakeTokenVerifier, InMemoryBlobStore, InMemoryRepository

REPO_CONFIG = Path(__file__).parents[3] / "config"
JSON_HEADERS = {"X-Requested-With": "XMLHttpRequest", "Content-Type": "application/json"}
WHEN = datetime(2026, 11, 16, 8, 0, tzinfo=UTC)


def config() -> PreferencesConfig:
    return load_preferences_config(
        REPO_CONFIG / "preferences.yaml", load_sources(REPO_CONFIG / "sources.yaml")
    )


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FailingRepository(InMemoryRepository):
    def get_preferences(self, user_id: str) -> ReaderPreferences | None:
        raise RuntimeError("store down")


def test_stored_choices_are_cached_for_30_seconds_and_replaced_on_save() -> None:
    repository = InMemoryRepository()
    clock = Clock()
    service = PreferencesService(config(), repository, clock)
    assert service.stored() is None

    elsewhere = ReaderPreferences(sources={"bbc-news": PreferenceState.HIDE}, updated_at=WHEN)
    repository.save_preferences(elsewhere)  # another instance saving
    clock.now += CACHE_SECONDS - 1
    assert service.stored() is None
    clock.now += 1
    assert service.stored() == elsewhere

    mine = elsewhere.model_copy(update={"version": 1, "sources": {}})
    service.remember(mine)
    assert service.stored() == mine


def test_an_unreadable_store_serves_views_unadjusted() -> None:
    service = PreferencesService(config(), FailingRepository())

    version, adjust = service.adjuster()

    assert adjust({"source_id": "bbc-news", "score_breakdown": {}}) == (False, {})
    assert version < 0  # never the key of a real version's ranking


def test_a_malformed_item_is_left_as_published() -> None:
    repository = InMemoryRepository()
    repository.save_preferences(
        ReaderPreferences(topics={"sport": PreferenceState.MORE}, updated_at=WHEN)
    )
    _, adjust = PreferencesService(config(), repository).adjuster()

    broken = {"source_id": "bbc-news", "score_breakdown": {"about:sport": 1, "demote:sport": "x"}}

    assert adjust(broken) == (False, {})


@pytest.fixture
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "config"
    directory.mkdir()
    for name in ("sources.yaml", "preferences.yaml"):
        shutil.copy(REPO_CONFIG / name, directory / name)
    monkeypatch.setenv("NEWSFEED_CONFIG_DIR", str(directory))
    return directory


def client(repository: InMemoryRepository) -> Any:
    from newsfeed.api.app import create_app

    app = create_app(
        InMemoryBlobStore(),
        repository,
        FakeTokenVerifier(identities()),
        owner_email=OWNER_EMAIL,
    )
    return client_for(app)


def put(api: Any, sent: dict[str, Any]) -> Any:
    return api.put("/api/preferences", content=json.dumps(sent), headers={**auth(), **JSON_HEADERS})


@pytest.mark.usefixtures("config_dir")
def test_a_put_keeps_choices_for_sources_the_menu_no_longer_offers() -> None:
    repository = InMemoryRepository()
    repository.save_preferences(
        ReaderPreferences(
            sources={"retired-source": PreferenceState.HIDE, "bbc-news": PreferenceState.FEWER},
            setup_done_at=WHEN,
            updated_at=WHEN,
            version=2,
        )
    )

    sent = {"version": 2, "sources": {"wired": "more"}, "topics": {}, "setup_done": False}
    response = put(client(repository), sent)

    assert response.status_code == 200
    stored = repository.get_preferences(DEFAULT_USER_ID)
    assert stored is not None
    # bbc-news was left out of the full set sent, so it is back to Show.
    assert stored.sources == {"retired-source": PreferenceState.HIDE, "wired": PreferenceState.MORE}
    assert stored.setup_done_at == WHEN  # set once, never cleared
    assert stored.version == 3


def test_without_a_preferences_file_the_routes_are_503_and_nothing_is_stored() -> None:
    # The package conftest points NEWSFEED_CONFIG_DIR at an empty directory.
    repository = InMemoryRepository()
    api = client(repository)

    assert api.get("/api/preferences", headers=auth()).status_code == 503
    sent = {"version": 0, "sources": {}, "topics": {}, "setup_done": True}
    assert put(api, sent).status_code == 503
    assert repository.get_preferences(DEFAULT_USER_ID) is None
