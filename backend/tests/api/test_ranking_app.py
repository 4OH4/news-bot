"""How ``create_app`` finds the views config and guards the read-time ranking (#326)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import pytest

from newsfeed.api.app import create_app
from newsfeed.config import ConfigError, ViewConfig, ViewsConfig
from tests.api.support import OWNER_EMAIL, RecordingBlobStore, auth, client_for, identities
from tests.fakes import FakeTokenVerifier, InMemoryRepository

RUN = "20261115T110000-000000"


def items(count: int) -> list[dict[str, Any]]:
    """Rules order 0..n-1 from one source; similarity reverses it."""
    return [
        {
            "id": f"{n:016x}",
            "source_id": "one",
            "score": float(count - n),
            "score_breakdown": {"source": 1.0, "similarity": float(n + 1)},
        }
        for n in range(count)
    ]


def put(blobs: RecordingBlobStore, name: str, view_items: list[Any]) -> None:
    document = {"run_id": RUN, "generated_at": "2026-11-15T12:00:00Z", "items": view_items}
    blobs.put(f"views/{name}.json", json.dumps(document).encode(), "application/json")


def app(blobs: RecordingBlobStore, views: ViewsConfig | None = None) -> Any:
    return create_app(
        blobs,
        InMemoryRepository(),
        FakeTokenVerifier(identities()),
        owner_email=OWNER_EMAIL,
        views=views,
    )


def get(client: Any, **params: Any) -> Any:
    return client.get("/api/feed", params=params, headers=auth())


def feed_only(ranking: Literal["ab", "rules"] = "ab") -> ViewsConfig:
    return ViewsConfig(views={"feed": ViewConfig(order="score", max_items=10, ranking=ranking)})


def test_views_passed_in_are_used_instead_of_the_config_dir() -> None:
    blobs = RecordingBlobStore()
    put(blobs, "feed", items(4))

    body = get(client_for(app(blobs, feed_only())), view="feed").json()

    assert all(i["arm"] in ("rules", "personal") for i in body["items"])


def test_a_view_the_config_does_not_name_is_served_as_stored(blobs: RecordingBlobStore) -> None:
    stored = items(4)
    put(blobs, "other", stored)

    body = get(client_for(app(blobs, feed_only())), view="other", ranking="personal").json()

    assert body["items"] == stored


def test_with_no_views_yaml_every_view_is_served_as_stored(blobs: RecordingBlobStore) -> None:
    # The autouse fixture points NEWSFEED_CONFIG_DIR at an empty directory.
    stored = items(4)
    put(blobs, "feed", stored)

    body = get(client_for(app(blobs)), view="feed").json()

    assert body["items"] == stored


def test_an_invalid_views_yaml_fails_at_start_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "views.yaml").write_text("views:\n  feed:\n    order: newest\n    ranking: ab\n")
    monkeypatch.setenv("NEWSFEED_CONFIG_DIR", str(tmp_path))

    with pytest.raises(ConfigError):
        app(RecordingBlobStore())


@pytest.mark.parametrize(
    "bad",
    [
        [{"id": "a", "source_id": "s", "score": "high", "score_breakdown": {}}],
        [{"id": "a", "source_id": "s", "score": 1.0, "score_breakdown": {"similarity": None}}],
        [{"id": "a", "source_id": "s", "score_breakdown": {}}],
        ["not an item"],
    ],
)
def test_a_score_view_that_cannot_be_ranked_is_503(blobs: RecordingBlobStore, bad: Any) -> None:
    put(blobs, "feed", bad)

    response = get(client_for(app(blobs, feed_only())), view="feed")

    assert response.status_code == 503


def test_paging_one_run_ranks_it_once(
    blobs: RecordingBlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    import newsfeed.api.app as app_module

    calls: list[str] = []
    real = app_module.rank_view

    def counting(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs["mode"])
        return real(*args, **kwargs)

    monkeypatch.setattr(app_module, "rank_view", counting)
    put(blobs, "feed", items(30))
    client = client_for(app(blobs, feed_only()))

    first = get(client, view="feed", limit=10).json()
    get(client, view="feed", limit=10, cursor=first["next_cursor"])
    get(client, view="feed", limit=10, ranking="personal")

    assert calls == ["ab", "personal"]
