"""``GET /api/feed?view=<name>&limit=<n>&cursor=<c>``: a view served in pages (#288).

The view files are written here as publish writes them (``views/<name>.json``
with ``run_id``, ``generated_at`` and ``items`` in the ``feed.json`` item shape).
The cursor is opaque, so these tests only ever follow ``next_cursor``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from newsfeed.models import Article, Source, SourceType
from newsfeed.publish.feed import build_feed
from tests.api.support import (
    FEED_BYTES,
    FEED_MARKER,
    STRANGER_TOKEN,
    RecordingBlobStore,
    assert_no_content,
    auth,
    client_for,
    make_app,
)

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
SCHEMA_PATH = Path(__file__).parents[3] / "docs" / "feed.schema.json"
VALIDATOR = Draft202012Validator(
    json.loads(SCHEMA_PATH.read_text()), format_checker=Draft202012Validator.FORMAT_CHECKER
)
SOURCES = [Source(id="gazette", type=SourceType.RSS, url="https://news.example.com/rss")]
RUN_A = "20261115T110000-000000"
RUN_B = "20261115T120000-000000"


def view_bytes(run_id: str, count: int, *, prefix: str = "s", title: str = "Story") -> bytes:
    """A view file of ``count`` items, highest score first."""
    articles = [
        Article.model_validate(
            {
                "canonical_url": f"https://news.example.com/{prefix}{n}",
                "url": f"https://news.example.com/{prefix}{n}",
                "source_id": "gazette",
                "title": f"{title} {prefix}{n}",
                "fetched_at": NOW - timedelta(hours=1),
                "score": float(count - n),
                "score_breakdown": {"source": 1.0},
                "extraction": {"status": "ok", "word_count": 400},
            }
        )
        for n in range(count)
    ]
    items = build_feed(articles, SOURCES, now=NOW, run_id=run_id, limit=count).feed["items"]
    assert len(items) == count
    document = {"run_id": run_id, "generated_at": "2026-11-15T12:00:00Z", "items": items}
    return json.dumps(document).encode()


def view_ids(data: bytes) -> list[str]:
    return [item["id"] for item in json.loads(data)["items"]]


def store(blobs: RecordingBlobStore, name: str, data: bytes) -> None:
    blobs.put(f"views/{name}.json", data, "application/json")


def page(client: Any, **params: Any) -> Any:
    return client.get("/api/feed", params=params, headers=auth())


def ok_json(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, (response.status_code, response.text)
    body: dict[str, Any] = response.json()
    return body


@pytest.fixture
def feed_view(blobs: RecordingBlobStore) -> bytes:
    """``views/feed.json`` of 120 items, beside the ``feed.json`` the fixture stores."""
    data = view_bytes(RUN_A, 120)
    store(blobs, "feed", data)
    return data


# --- Paging ---------------------------------------------------------------------------


def test_following_next_cursor_pages_through_120_items_as_50_50_20(
    blobs: RecordingBlobStore, feed_view: bytes
) -> None:
    client = client_for(make_app(blobs))

    first = ok_json(page(client, view="feed", limit=50))
    second = ok_json(page(client, view="feed", limit=50, cursor=first["next_cursor"]))
    third = ok_json(page(client, view="feed", limit=50, cursor=second["next_cursor"]))

    assert [len(p["items"]) for p in (first, second, third)] == [50, 50, 20]
    assert isinstance(first["next_cursor"], str) and first["next_cursor"]
    assert isinstance(second["next_cursor"], str) and second["next_cursor"]
    assert third["next_cursor"] is None
    served = [i["id"] for p in (first, second, third) for i in p["items"]]
    assert served == view_ids(feed_view)


def test_a_page_carries_the_view_files_run_id_and_generated_at(
    blobs: RecordingBlobStore, feed_view: bytes
) -> None:
    body = ok_json(page(client_for(make_app(blobs)), view="feed", limit=5))

    assert body["schema_version"] == 1
    assert body["view"] == "feed"
    assert body["run_id"] == RUN_A
    assert body["generated_at"] == "2026-11-15T12:00:00Z"
    assert body["items"] == json.loads(feed_view)["items"][:5]


def test_the_default_limit_is_50(blobs: RecordingBlobStore, feed_view: bytes) -> None:
    body = ok_json(page(client_for(make_app(blobs)), view="feed"))

    assert len(body["items"]) == 50
    assert body["next_cursor"]


def test_a_limit_of_100_is_served(blobs: RecordingBlobStore, feed_view: bytes) -> None:
    body = ok_json(page(client_for(make_app(blobs)), view="feed", limit=100))

    assert len(body["items"]) == 100


def test_a_view_shorter_than_one_page_has_no_next_cursor(blobs: RecordingBlobStore) -> None:
    store(blobs, "longer", view_bytes(RUN_A, 3))

    body = ok_json(page(client_for(make_app(blobs)), view="longer", limit=50))

    assert len(body["items"]) == 3
    assert body["next_cursor"] is None


def test_a_view_of_exactly_two_pages_has_no_next_cursor_on_the_second(
    blobs: RecordingBlobStore,
) -> None:
    store(blobs, "feed", view_bytes(RUN_A, 100))
    client = client_for(make_app(blobs))

    first = ok_json(page(client, view="feed", limit=50))
    second = ok_json(page(client, view="feed", limit=50, cursor=first["next_cursor"]))

    assert len(second["items"]) == 50
    assert second["next_cursor"] is None


def test_an_empty_view_is_an_empty_page(blobs: RecordingBlobStore) -> None:
    store(blobs, "news", view_bytes(RUN_A, 0))

    body = ok_json(page(client_for(make_app(blobs)), view="news"))

    assert body["items"] == []
    assert body["next_cursor"] is None


def test_a_cursor_from_an_earlier_run_serves_the_same_offset_of_the_current_list(
    blobs: RecordingBlobStore, feed_view: bytes
) -> None:
    client = client_for(make_app(blobs))
    cursor = ok_json(page(client, view="feed", limit=50))["next_cursor"]
    newer = view_bytes(RUN_B, 80, prefix="n")
    store(blobs, "feed", newer)

    body = ok_json(page(client, view="feed", limit=50, cursor=cursor))

    assert body["run_id"] == RUN_B
    assert [i["id"] for i in body["items"]] == view_ids(newer)[50:80]
    assert body["next_cursor"] is None


# --- Caching --------------------------------------------------------------------------


def test_an_unchanged_page_asked_again_with_its_etag_gets_304(
    blobs: RecordingBlobStore, feed_view: bytes
) -> None:
    client = client_for(make_app(blobs))
    first = page(client, view="feed", limit=50)
    assert ok_json(first)["view"] == "feed"
    etag = first.headers["etag"]
    assert etag

    again = client.get(
        "/api/feed",
        params={"view": "feed", "limit": 50},
        headers={**auth(), "If-None-Match": etag},
    )

    assert again.status_code == 304
    assert again.content == b""


def test_a_page_of_a_new_run_gets_a_new_etag(blobs: RecordingBlobStore, feed_view: bytes) -> None:
    client = client_for(make_app(blobs))
    etag = page(client, view="feed", limit=50).headers["etag"]
    store(blobs, "feed", view_bytes(RUN_B, 120, prefix="n"))

    again = client.get(
        "/api/feed",
        params={"view": "feed", "limit": 50},
        headers={**auth(), "If-None-Match": etag},
    )

    assert again.status_code == 200
    assert again.json()["run_id"] == RUN_B


# --- Bad requests ---------------------------------------------------------------------


@pytest.mark.parametrize("name", ["nope", "Feed", "feed_x", "feed.json", "fe ed", "feed\n"])
def test_an_unknown_or_malformed_view_is_404(
    blobs: RecordingBlobStore, feed_view: bytes, name: str
) -> None:
    response = page(client_for(make_app(blobs)), view=name)

    assert response.status_code == 404


@pytest.mark.parametrize("limit", ["0", "101", "-1", "ten"])
def test_a_limit_outside_1_to_100_is_422(
    blobs: RecordingBlobStore, feed_view: bytes, limit: str
) -> None:
    response = page(client_for(make_app(blobs)), view="feed", limit=limit)

    assert response.status_code == 422


def test_a_malformed_cursor_is_400(blobs: RecordingBlobStore, feed_view: bytes) -> None:
    response = page(client_for(make_app(blobs)), view="feed", cursor="%%%")

    assert response.status_code == 400


def test_a_view_with_no_stored_file_is_404_once_a_feed_exists(blobs: RecordingBlobStore) -> None:
    response = page(client_for(make_app(blobs)), view="longer")

    assert response.status_code == 404


def test_a_view_before_the_first_run_has_finished_is_503() -> None:
    response = page(client_for(make_app(RecordingBlobStore())), view="feed")

    assert response.status_code == 503


def test_a_stranger_gets_no_page(blobs: RecordingBlobStore) -> None:
    store(blobs, "feed", view_bytes(RUN_A, 3, title=FEED_MARKER))

    response = client_for(make_app(blobs)).get(
        "/api/feed", params={"view": "feed"}, headers=auth(STRANGER_TOKEN)
    )

    assert response.status_code == 403
    assert_no_content(response.content)


# --- feed.json and the contract ---------------------------------------------------------


def test_no_query_parameters_still_return_feed_json_byte_for_byte(
    blobs: RecordingBlobStore, feed_view: bytes
) -> None:
    response = client_for(make_app(blobs)).get("/api/feed", headers=auth())

    assert response.status_code == 200
    assert response.content == FEED_BYTES


def test_every_page_validates_against_the_feed_schema(
    blobs: RecordingBlobStore, feed_view: bytes
) -> None:
    client = client_for(make_app(blobs))
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, Any] = {"view": "feed", "limit": 50}
        if cursor:
            params["cursor"] = cursor
        body = ok_json(page(client, **params))
        errors = [e.message for e in VALIDATOR.iter_errors(body)]
        assert not errors, errors
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break
        assert pages < 10, "next_cursor never ran out"
    assert pages == 3


def test_the_schema_takes_view_and_next_cursor_without_a_new_schema_version() -> None:
    schema = json.loads(SCHEMA_PATH.read_text())
    base = {"schema_version": 1, "generated_at": "2026-11-15T12:00:00Z", "run_id": RUN_A}

    VALIDATOR.validate({**base, "items": []})
    VALIDATOR.validate({**base, "view": "long-reads", "next_cursor": "abc", "items": []})
    VALIDATOR.validate({**base, "view": "news", "next_cursor": None, "items": []})
    assert not VALIDATOR.is_valid({**base, "view": "Bad_View", "items": []})
    assert schema["properties"]["schema_version"]["const"] == 1
    assert "view" not in schema["required"]
    assert "next_cursor" not in schema["required"]
