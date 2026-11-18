"""GET and PUT /api/preferences, and the preferences applied as each view is served (#337).

The app reads ``sources.yaml`` and ``preferences.yaml`` from the settings' config
directory at start-up, like ``views.yaml``; these tests point it at copies of the
repo's files. Views are passed in, so the repo's ``views.yaml`` doesn't matter.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from newsfeed.adapters.localfs_blobstore import LocalBlobStore
from newsfeed.adapters.sqlite_repository import SqliteRepository
from newsfeed.config import ConfigError, ViewConfig, ViewsConfig
from newsfeed.models import DEFAULT_USER_ID, PreferenceState, ReaderPreferences, VerifiedIdentity
from newsfeed.ports import BlobStore, Repository
from tests.api.support import OWNER_EMAIL, auth, client_for, identities
from tests.fakes import FakeTokenVerifier, InMemoryBlobStore, InMemoryRepository

REPO_CONFIG = Path(__file__).parents[3] / "config"
JSON_HEADERS = {"X-Requested-With": "XMLHttpRequest", "Content-Type": "application/json"}
RUN = "20261117T090000-000000"
VIEW_NAMES = ("news", "feed", "longer")
VIEWS = ViewsConfig(
    views={
        "news": ViewConfig(order="newest", max_items=300),
        "feed": ViewConfig(order="score", max_items=500, ranking="ab"),
        "longer": ViewConfig(order="score", max_items=200),
    }
)

SPORT_ID = "5000000000000003"
PLAIN_ID = "5000000000000002"
SPORT_SCORE = 2.0
SPORT_SIMILARITY = 0.5

# Each item from its own (enabled) source, so diversity never scales a score. The
# sport item and the plain item score the same; the plain one is stored first.
ITEMS: list[dict[str, Any]] = [
    {
        "id": "5000000000000001",
        "source_id": "bbc-news",
        "score": 3.0,
        "score_breakdown": {"source": 1.0, "similarity": 1.0},
    },
    {
        "id": PLAIN_ID,
        "source_id": "guardian-world",
        "score": SPORT_SCORE,
        "score_breakdown": {"source": 1.0, "similarity": 1.0},
    },
    {
        "id": SPORT_ID,
        "source_id": "ars-technica",
        "score": SPORT_SCORE,
        "score_breakdown": {
            "source": 1.0,
            "about:sport": 1.0,
            "demote:sport": 0.2,
            "similarity": SPORT_SIMILARITY,
        },
    },
    {
        "id": "5000000000000004",
        "source_id": "quanta",
        "score": 1.0,
        "score_breakdown": {"source": 1.0, "similarity": 1.0},
    },
]


@pytest.fixture(autouse=True)
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A config directory holding copies of the repo's sources and preferences files."""
    directory = tmp_path / "config"
    directory.mkdir()
    for name in ("sources.yaml", "preferences.yaml"):
        shutil.copy(REPO_CONFIG / name, directory / name)
    monkeypatch.setenv("NEWSFEED_CONFIG_DIR", str(directory))
    return directory


def publish(blobs: BlobStore) -> None:
    """Write ``feed.json`` and every view file, each holding ``ITEMS``."""
    blobs.put("feed.json", json.dumps({"schema_version": 1, "items": []}).encode())
    for name in VIEW_NAMES:
        document = {"run_id": RUN, "generated_at": "2026-11-17T09:00:00Z", "items": ITEMS}
        blobs.put(f"views/{name}.json", json.dumps(document).encode(), "application/json")


def client(repository: Repository | None = None, blobs: BlobStore | None = None) -> Any:
    """A client for an app over ``blobs`` (published) and ``repository``."""
    from newsfeed.api.app import create_app

    store = blobs if blobs is not None else InMemoryBlobStore()
    publish(store)
    app = create_app(
        store,
        repository if repository is not None else InMemoryRepository(),
        FakeTokenVerifier(identities()),
        owner_email=OWNER_EMAIL,
        views=VIEWS,
    )
    return client_for(app)


def body(**changes: Any) -> dict[str, Any]:
    """A valid PUT body with ``changes`` applied."""
    return {"version": 0, "sources": {}, "topics": {}, "setup_done": False} | changes


def put(api: Any, sent: Any, headers: dict[str, str] | None = None) -> Any:
    content = sent if isinstance(sent, str) else json.dumps(sent)
    return api.put(
        "/api/preferences",
        content=content,
        headers={**auth(), **(JSON_HEADERS if headers is None else headers)},
    )


def view(api: Any, name: str, **params: Any) -> Any:
    return api.get("/api/feed", params={"view": name, **params}, headers=auth())


def item(page: dict[str, Any], item_id: str) -> dict[str, Any]:
    return next(i for i in page["items"] if i["id"] == item_id)


def ids(page: dict[str, Any]) -> list[str]:
    return [i["id"] for i in page["items"]]


def test_with_nothing_stored_the_menu_shows_the_defaults(config_dir: Path) -> None:
    defaults = yaml.safe_load((config_dir / "preferences.yaml").read_text())

    response = client().get("/api/preferences", headers=auth())

    assert response.status_code == 200
    got = response.json()
    assert got["version"] == 0
    assert got["setup_done"] is False
    menu = got["menu"]
    assert [s["label"] for s in menu["sections"]] == [
        "News sources",
        "Tech",
        "Science",
        "More sources",
        "Long Reads",
    ]
    sources = [s for section in menu["sections"] for s in section["sources"]]
    assert {s["state"] for s in [*sources, *menu["topics"]]} == {"show"}
    assert {s["id"] for s in sources if s["starter"]} == set(defaults["starter"]["sources"])
    assert {t["id"] for t in menu["topics"] if t["starter"]} == set(defaults["starter"]["topics"])
    assert menu["multipliers"] == defaults["multipliers"]


def test_a_hidden_source_leaves_every_view_at_once_with_sqlite_and_local_blobs(
    tmp_path: Path,
) -> None:
    with SqliteRepository(tmp_path / "newsfeed.db") as repository:
        api = client(repository, LocalBlobStore(tmp_path / "blobs"))
        before = view(api, "feed")
        assert "5000000000000001" in ids(before.json())  # the bbc-news item, before

        saved = put(api, body(sources={"bbc-news": "hide"}, setup_done=True))

        assert saved.status_code == 200
        assert saved.json()["version"] == 1
        assert saved.json()["setup_done"] is True
        for name in VIEW_NAMES:
            page = view(api, name).json()
            assert page["items"], name
            assert all(i["source_id"] != "bbc-news" for i in page["items"]), name
        assert view(api, "feed").headers["ETag"] != before.headers["ETag"]
        assert api.get("/api/preferences", headers=auth()).json()["version"] == 1
        stored = repository.get_preferences(DEFAULT_USER_ID)
        assert stored is not None
        assert stored.sources == {"bbc-news": PreferenceState.HIDE}
        assert stored.setup_done_at is not None


@pytest.mark.parametrize(
    ("state", "topic_factor", "undemote", "multiple"),
    [("more", 2.0, 5.0, 10.0), ("fewer", 0.5, None, 0.5)],
)
def test_a_topic_setting_scales_its_items_in_score_views_only(
    state: str, topic_factor: float, undemote: float | None, multiple: float
) -> None:
    api = client()
    assert put(api, body(topics={"sport": state})).status_code == 200

    feed = view(api, "feed", ranking="rules").json()

    sport = item(feed, SPORT_ID)
    breakdown = sport["score_breakdown"]
    assert breakdown["pref:topic:sport"] == pytest.approx(topic_factor)
    if undemote is None:
        assert "pref:undemote:sport" not in breakdown
    else:
        assert breakdown["pref:undemote:sport"] == pytest.approx(undemote)
    assert sport["score"] == pytest.approx(SPORT_SCORE * multiple)
    above_plain = ids(feed).index(SPORT_ID) < ids(feed).index(PLAIN_ID)
    assert above_plain is (state == "more")
    assert view(api, "news").json()["items"] == ITEMS


def test_a_hidden_topic_leaves_the_news_view_too() -> None:
    api = client()
    assert put(api, body(topics={"sport": "hide"})).status_code == 200

    news = ids(view(api, "news").json())

    assert SPORT_ID not in news
    assert PLAIN_ID in news


@pytest.mark.parametrize(
    ("ranking", "similarity"), [("rules", 1.0), ("personal", SPORT_SIMILARITY)]
)
def test_both_ab_sides_use_the_topic_factor(ranking: str, similarity: float) -> None:
    api = client()
    assert put(api, body(topics={"sport": "more"})).status_code == 200

    sport = item(view(api, "feed", ranking=ranking).json(), SPORT_ID)
    interleaved = item(view(api, "feed").json(), SPORT_ID)

    assert sport["score_breakdown"]["pref:topic:sport"] == pytest.approx(2.0)
    assert sport["score"] == pytest.approx(SPORT_SCORE * 10.0 * similarity)
    assert interleaved["score_breakdown"]["pref:topic:sport"] == pytest.approx(2.0)


def test_a_stale_version_is_409_with_the_current_preferences() -> None:
    repository = InMemoryRepository()
    current = ReaderPreferences(
        sources={"bbc-news": PreferenceState.FEWER},
        updated_at=datetime(2026, 11, 16, 8, 0, tzinfo=UTC),
        version=3,
    )
    repository.save_preferences(current)
    api = client(repository)

    response = put(api, body(version=0, sources={"bbc-news": "hide"}))

    assert response.status_code == 409
    got = response.json()
    payload = got.get("detail", got) if isinstance(got.get("detail"), dict) else got
    assert payload["version"] == 3
    sources = [s for section in payload["menu"]["sections"] for s in section["sources"]]
    assert next(s for s in sources if s["id"] == "bbc-news")["state"] == "fewer"
    assert repository.get_preferences(DEFAULT_USER_ID) == current


@pytest.mark.parametrize(
    ("sent", "headers", "status"),
    [
        (body(sources={"not-a-source": "hide"}), None, 422),
        (body(sources={"bbc-news": "lots"}), None, 422),
        (body(topics={"not-a-topic": "more"}), None, 422),
        (body(regions={"uk": "more"}), None, 422),
        (body(version="0"), None, 422),
        (body(sources={"bbc-news": "hide"}), {"Content-Type": "application/json"}, 403),
        (
            json.dumps(body(sources={"bbc-news": "hide"})),
            {"X-Requested-With": "XMLHttpRequest", "Content-Type": "text/plain"},
            415,
        ),
        (json.dumps(body()) + " " * 17 * 1024, None, 413),
    ],
    ids=[
        "unknown-source",
        "unknown-state",
        "unknown-topic",
        "regions",
        "version-as-string",
        "no-x-requested-with",
        "not-json",
        "over-16-kb",
    ],
)
def test_a_refused_put_stores_nothing(
    sent: Any, headers: dict[str, str] | None, status: int
) -> None:
    repository = InMemoryRepository()
    api = client(repository)

    response = put(api, sent, headers)

    assert response.status_code == status
    assert repository.get_preferences(DEFAULT_USER_ID) is None


def test_everything_back_to_show_stores_no_overrides_and_serves_views_as_stored() -> None:
    repository = InMemoryRepository()
    api = client(repository)
    baseline = {name: view(api, name).json() for name in VIEW_NAMES}
    first = put(api, body(sources={"bbc-news": "hide"}, topics={"sport": "more"}))
    assert first.status_code == 200

    reset = put(api, body(version=1, sources={"bbc-news": "show"}, topics={"sport": "show"}))

    assert reset.status_code == 200
    stored = repository.get_preferences(DEFAULT_USER_ID)
    assert stored is not None
    assert (stored.sources, stored.topics) == ({}, {})
    for name in VIEW_NAMES:
        assert view(api, name).json() == baseline[name], name


def test_user_id_for_the_owner_is_the_default_user() -> None:
    from newsfeed.api.preferences import user_id_for

    owner = VerifiedIdentity(email=OWNER_EMAIL, email_verified=True)

    assert user_id_for(owner) == DEFAULT_USER_ID


def test_a_broken_preferences_config_fails_start_up(config_dir: Path) -> None:
    from newsfeed.api.app import create_app

    (config_dir / "preferences.yaml").write_text("multipliers: {hide: 1}\n")

    with pytest.raises(ConfigError):
        create_app(
            InMemoryBlobStore(),
            InMemoryRepository(),
            FakeTokenVerifier(identities()),
            owner_email=OWNER_EMAIL,
            views=VIEWS,
        )
