"""``GET /api/feed?view=<name>[&ranking=<mode>]`` ranks score views at read time (#326).

The API reads ``views.yaml`` from ``NEWSFEED_CONFIG_DIR`` at start-up, so each
test writes one there before building the app. View files are written as publish
writes them; items carry only the keys the ranking reads plus ``similarity`` in
the breakdown (the rank stage writes it, #323).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from newsfeed.api.ranking import rank_view
from tests.api.support import RecordingBlobStore, auth, client_for, make_app

RUN_A = "20261115T110000-000000"
RUN_B = "20261115T120000-000000"

VIEWS_YAML = """
views:
  news:
    tags: [news]
    order: newest
    max_items: 300
  feed:
    order: score
    max_items: 500
    ranking: ab
  longer:
    tags: [long-form]
    order: score
    max_items: 200
"""


@pytest.fixture(autouse=True)
def views_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "views.yaml").write_text(VIEWS_YAML)
    monkeypatch.setenv("NEWSFEED_CONFIG_DIR", str(config))


def make_items(count: int, *, similarity: bool = True, one_source: bool = False) -> list[Any]:
    """``count`` items in rules order; similarity runs against the score, so the orders differ."""
    items = []
    for n in range(count):
        breakdown = {"source": 1.0}
        if similarity:
            breakdown["similarity"] = 0.5 + ((n * 7) % count) / count
        items.append(
            {
                "id": f"{n:016x}",
                "title": f"Story {n}",
                "source_id": "loud" if one_source else f"src-{n % 40}",
                "score": float(count - n),
                "score_breakdown": breakdown,
            }
        )
    return items


def put_view(
    blobs: RecordingBlobStore, name: str, items: list[Any], run_id: str = RUN_A
) -> list[Any]:
    document = {"run_id": run_id, "generated_at": "2026-11-15T12:00:00Z", "items": items}
    blobs.put(f"views/{name}.json", json.dumps(document).encode(), "application/json")
    return items


def get(client: Any, **params: Any) -> Any:
    return client.get("/api/feed", params=params, headers=auth())


def served(client: Any, *, limit: int = 50, **params: Any) -> list[dict[str, Any]]:
    """Every item of a view, by following ``next_cursor``."""
    out: list[dict[str, Any]] = []
    cursor = None
    while True:
        query = {**params, "limit": limit, **({"cursor": cursor} if cursor else {})}
        response = get(client, **query)
        assert response.status_code == 200, response.text
        body = response.json()
        out += body["items"]
        cursor = body["next_cursor"]
        if cursor is None:
            return out


def expected(items: list[Any], mode: Any, view: str = "feed", run_id: str = RUN_A) -> list[Any]:
    return rank_view(items, mode=mode, seed=f"{run_id}:{view}")


@pytest.fixture
def blobs() -> RecordingBlobStore:
    return RecordingBlobStore()


def test_the_feed_is_served_interleaved_with_an_arm_on_every_item(
    blobs: RecordingBlobStore,
) -> None:
    items = put_view(blobs, "feed", make_items(30))

    body = served(client_for(make_app(blobs)), view="feed")

    assert body == expected(items, "ab")
    assert {i["arm"] for i in body} == {"rules", "personal"}


def test_a_feed_with_no_similarity_is_served_in_rules_order_without_arms(
    blobs: RecordingBlobStore,
) -> None:
    items = put_view(blobs, "feed", make_items(12, similarity=False))

    body = served(client_for(make_app(blobs)), view="feed")

    assert [i["id"] for i in body] == [i["id"] for i in items]
    assert all("arm" not in i for i in body)


@pytest.mark.parametrize("mode", ["rules", "personal"])
def test_the_ranking_query_serves_one_order_alone_without_arms(
    blobs: RecordingBlobStore, mode: str
) -> None:
    items = put_view(blobs, "feed", make_items(30))

    body = served(client_for(make_app(blobs)), view="feed", ranking=mode)

    assert body == expected(items, mode)
    assert all("arm" not in i for i in body)


def test_the_personal_order_differs_from_the_rules_order_on_this_data(
    blobs: RecordingBlobStore,
) -> None:
    put_view(blobs, "feed", make_items(30))
    client = client_for(make_app(blobs))

    rules = [i["id"] for i in served(client, view="feed", ranking="rules")]
    personal = [i["id"] for i in served(client, view="feed", ranking="personal")]

    assert rules != personal
    assert sorted(rules) == sorted(personal)


def test_an_unknown_ranking_value_is_422(blobs: RecordingBlobStore) -> None:
    put_view(blobs, "feed", make_items(5))

    assert get(client_for(make_app(blobs)), view="feed", ranking="random").status_code == 422


def test_a_newest_view_ignores_the_ranking_query(blobs: RecordingBlobStore) -> None:
    items = put_view(blobs, "news", make_items(10))

    body = served(client_for(make_app(blobs)), view="news", ranking="personal")

    assert body == items


def test_a_score_view_with_no_ranking_setting_is_served_by_rules(
    blobs: RecordingBlobStore,
) -> None:
    items = put_view(blobs, "longer", make_items(10))

    body = served(client_for(make_app(blobs)), view="longer")

    assert body == expected(items, "rules", "longer")
    assert all("arm" not in i for i in body)


def test_three_pages_of_a_120_item_feed_hold_each_item_once_in_the_single_ranking_order(
    blobs: RecordingBlobStore,
) -> None:
    items = put_view(blobs, "feed", make_items(120))

    body = served(client_for(make_app(blobs)), view="feed", limit=50)

    assert len(body) == 120
    assert len({i["id"] for i in body}) == 120
    assert body == expected(items, "ab")


def test_a_new_run_is_ranked_again_with_its_own_seed(blobs: RecordingBlobStore) -> None:
    client = client_for(make_app(blobs))
    put_view(blobs, "feed", make_items(30), RUN_A)
    first = served(client, view="feed")

    newer = put_view(blobs, "feed", make_items(25), RUN_B)
    second = served(client, view="feed")

    assert first != second
    assert second == expected(newer, "ab", run_id=RUN_B)


def test_repeated_requests_for_the_same_run_are_identical_bytes(
    blobs: RecordingBlobStore,
) -> None:
    put_view(blobs, "feed", make_items(60))
    client = client_for(make_app(blobs))

    first = get(client, view="feed", limit=20)
    second = get(client, view="feed", limit=20)

    assert first.content == second.content
    assert first.headers["etag"] == second.headers["etag"]
    assert get(client, view="feed", limit=20).status_code == 200
    again = client.get(
        "/api/feed",
        params={"view": "feed", "limit": 20},
        headers={**auth(), "If-None-Match": first.headers["etag"]},
    )
    assert again.status_code == 304


def test_one_loud_source_cannot_fill_the_top_of_any_order(blobs: RecordingBlobStore) -> None:
    loud = make_items(10, one_source=True)
    quiet = [
        {**it, "id": f"{900 + n:016x}", "source_id": f"q{n}", "score": 1.0}
        for n, it in enumerate(make_items(10))
    ]
    put_view(blobs, "feed", loud + quiet)
    client = client_for(make_app(blobs))

    for mode in ("rules", "personal"):
        top = served(client, view="feed", ranking=mode)[:5]
        assert sum(i["source_id"] == "loud" for i in top) < 5
