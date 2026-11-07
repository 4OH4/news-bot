"""POST /api/feedback: store one event, keep a liked article's HTML (#176)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from newsfeed.adapters.localfs_blobstore import LocalBlobStore
from newsfeed.adapters.sqlite_repository import SqliteRepository
from newsfeed.models import Article, Feedback
from tests.api.support import (
    ARTICLE_HTML,
    ARTICLE_ID,
    STRANGER_TOKEN,
    RecordingBlobStore,
    auth,
    client_for,
    make_app,
)
from tests.fakes import InMemoryRepository

SRC_API = Path(__file__).parents[2] / "src" / "newsfeed" / "api"
EXAMPLE = Path(__file__).parents[3] / "docs" / "feedback.example.json"

EVENT_ID = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"
OTHER_ID = "ffeeddccbbaa99887766554433221100"
JSON_HEADERS = {"X-Requested-With": "XMLHttpRequest", "Content-Type": "application/json"}


def event(**changes: Any) -> dict[str, Any]:
    """A valid ``open`` event, with ``changes`` applied."""
    body: dict[str, Any] = {
        "id": EVENT_ID,
        "article_id": ARTICLE_ID,
        "event": "open",
        "value": None,
        "created_at": "2026-11-07T08:30:00.000Z",
    }
    body.update(changes)
    return body


def like(**changes: Any) -> dict[str, Any]:
    return event(event="like", value=1, **changes)


def post(client: Any, body: Any, headers: dict[str, str] | None = None) -> Any:
    """Post ``body`` as the owner, with the client's headers unless ``headers`` is given."""
    sent = {**auth(), **(JSON_HEADERS if headers is None else headers)}
    content = body if isinstance(body, bytes | str) else json.dumps(body)
    return client.post("/api/feedback", content=content, headers=sent)


class BrokenRepository(InMemoryRepository):
    def save_feedback(self, feedback: Feedback) -> None:
        raise RuntimeError("storage is down")


class BrokenPutBlobs(RecordingBlobStore):
    def __init__(self) -> None:
        super().__init__()
        self.put_calls: list[str] = []

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self.put_calls.append(key)
        if key.startswith("saved/"):
            raise OSError("disk full")
        return super().put(key, data, content_type)


# --- storing events ----------------------------------------------------------


def test_an_open_event_is_stored_with_the_posted_fields(blobs: RecordingBlobStore) -> None:
    repository = InMemoryRepository()
    client = client_for(make_app(blobs, repository=repository))

    response = post(client, event())

    assert response.status_code == 204
    assert response.content == b""
    [stored] = repository.list_feedback()
    assert stored.id == EVENT_ID
    assert stored.article_id == ARTICLE_ID
    assert stored.event == "open"
    assert stored.value is None
    assert stored.created_at == datetime(2026, 11, 7, 8, 30, tzinfo=UTC)


def test_every_entry_in_the_shared_example_file_is_accepted_and_stored(
    blobs: RecordingBlobStore,
) -> None:
    # The client's tests read the same file, so the two sides can't drift.
    entries = json.loads(EXAMPLE.read_text())
    assert entries, "docs/feedback.example.json must not be empty"
    repository = InMemoryRepository()
    client = client_for(make_app(blobs, repository=repository))

    for entry in entries:
        assert post(client, entry).status_code == 204, entry

    stored = {f.id: f for f in repository.list_feedback()}
    assert set(stored) == {entry["id"] for entry in entries}
    for entry in entries:
        assert stored[entry["id"]].article_id == entry["article_id"]
        assert stored[entry["id"]].event == entry["event"]
        assert stored[entry["id"]].value == entry["value"]


def test_the_same_event_id_posted_twice_is_one_row(blobs: RecordingBlobStore) -> None:
    repository = InMemoryRepository()
    client = client_for(make_app(blobs, repository=repository))

    assert post(client, event()).status_code == 204
    assert post(client, event()).status_code == 204

    assert len(repository.list_feedback()) == 1


def test_a_different_event_id_is_a_second_row(blobs: RecordingBlobStore) -> None:
    repository = InMemoryRepository()
    client = client_for(make_app(blobs, repository=repository))

    post(client, event())
    post(client, event(id=OTHER_ID))

    assert len(repository.list_feedback()) == 2


def test_an_event_for_an_article_with_no_row_is_still_stored(blobs: RecordingBlobStore) -> None:
    repository = InMemoryRepository()
    client = client_for(make_app(blobs, repository=repository))

    assert post(client, event(article_id="aaaaaaaaaaaaaaaa")).status_code == 204

    assert [f.article_id for f in repository.list_feedback()] == ["aaaaaaaaaaaaaaaa"]


# --- who may post ------------------------------------------------------------


def test_no_token_and_another_accounts_token_store_nothing(blobs: RecordingBlobStore) -> None:
    repository = InMemoryRepository()
    client = client_for(make_app(blobs, repository=repository))

    anonymous = client.post("/api/feedback", content=json.dumps(event()), headers=JSON_HEADERS)
    stranger = client.post(
        "/api/feedback",
        content=json.dumps(event()),
        headers={**auth(STRANGER_TOKEN), **JSON_HEADERS},
    )

    assert anonymous.status_code in (401, 403)
    assert stranger.status_code in (401, 403)
    assert repository.list_feedback() == []


def test_a_like_from_another_account_makes_no_saved_copy(blobs: RecordingBlobStore) -> None:
    client = client_for(make_app(blobs))

    client.post(
        "/api/feedback",
        content=json.dumps(like()),
        headers={**auth(STRANGER_TOKEN), **JSON_HEADERS},
    )

    assert not blobs.inner.exists(blobs.ref_for(f"saved/{ARTICLE_ID}.html"))


# --- the cross-site guard ----------------------------------------------------


def test_a_post_without_x_requested_with_is_403_and_stores_nothing(
    blobs: RecordingBlobStore,
) -> None:
    repository = InMemoryRepository()
    client = client_for(make_app(blobs, repository=repository))

    response = post(client, event(), headers={"Content-Type": "application/json"})

    assert response.status_code == 403
    assert repository.list_feedback() == []


def test_a_post_with_the_wrong_x_requested_with_value_is_403(blobs: RecordingBlobStore) -> None:
    repository = InMemoryRepository()
    client = client_for(make_app(blobs, repository=repository))

    response = post(
        client,
        event(),
        headers={"X-Requested-With": "something", "Content-Type": "application/json"},
    )

    assert response.status_code == 403
    assert repository.list_feedback() == []


@pytest.mark.parametrize(
    "content_type",
    ["application/x-www-form-urlencoded", "text/plain", "text/plain;charset=UTF-8"],
)
def test_a_body_that_is_not_json_is_415_and_stores_nothing(
    blobs: RecordingBlobStore, content_type: str
) -> None:
    repository = InMemoryRepository()
    client = client_for(make_app(blobs, repository=repository))

    response = post(
        client,
        event(),
        headers={"X-Requested-With": "XMLHttpRequest", "Content-Type": content_type},
    )

    assert response.status_code == 415
    assert repository.list_feedback() == []


def test_the_guard_answers_before_the_body_is_parsed(blobs: RecordingBlobStore) -> None:
    client = client_for(make_app(blobs))

    no_header = post(client, b"{not json", headers={"Content-Type": "application/json"})
    wrong_type = post(
        client,
        b"{not json",
        headers={"X-Requested-With": "XMLHttpRequest", "Content-Type": "text/plain"},
    )

    assert no_header.status_code == 403
    assert wrong_type.status_code == 415


# --- validation --------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        event(created_at="2026-11-07T08:30:00"),
        {**event(), "unexpected": 1},
        event(event="share"),
        event(id="../../etc/passwd"),
        event(id="0F1E2D3C4B5A69788796A5B4C3D2E1F0"),
        event(id=EVENT_ID + "\n"),
        event(article_id="../secret"),
        event(article_id="0123456789ABCDEF"),
        event(article_id=ARTICLE_ID + "\n"),
        event(value=-1),
        event(value=86401),
        event(value="soon"),
        {k: v for k, v in event().items() if k != "created_at"},
        {k: v for k, v in event().items() if k != "id"},
    ],
    ids=[
        "naive-created-at",
        "unknown-field",
        "unknown-event",
        "path-id",
        "uppercase-id",
        "id-trailing-newline",
        "path-article-id",
        "uppercase-article-id",
        "article-id-trailing-newline",
        "negative-value",
        "value-over-a-day",
        "text-value",
        "missing-created-at",
        "missing-id",
    ],
)
def test_an_invalid_event_is_422_and_stores_nothing(
    blobs: RecordingBlobStore, body: dict[str, Any]
) -> None:
    repository = InMemoryRepository()
    client = client_for(make_app(blobs, repository=repository))

    response = post(client, body)

    assert response.status_code == 422
    assert repository.list_feedback() == []


@pytest.mark.parametrize("literal", ["1e999", "-1e999", "NaN", "Infinity"])
def test_a_non_finite_value_is_422_and_stores_nothing(
    blobs: RecordingBlobStore, literal: str
) -> None:
    repository = InMemoryRepository()
    client = client_for(make_app(blobs, repository=repository))
    raw = json.dumps(event()).replace('"value": null', f'"value": {literal}')
    assert literal in raw

    response = post(client, raw)

    assert response.status_code == 422
    assert repository.list_feedback() == []


@pytest.mark.parametrize("value", [0, 86400, 0.5])
def test_the_edges_of_the_value_range_are_accepted(blobs: RecordingBlobStore, value: float) -> None:
    repository = InMemoryRepository()
    client = client_for(make_app(blobs, repository=repository))

    assert post(client, event(event="read", value=value)).status_code == 204
    assert repository.list_feedback()[0].value == value


def test_a_body_over_4_kib_is_refused_and_stores_nothing(blobs: RecordingBlobStore) -> None:
    repository = InMemoryRepository()
    client = client_for(make_app(blobs, repository=repository))
    padded = json.dumps({**event(), "padding": "x" * 5000})

    response = post(client, padded)

    assert response.status_code in (413, 422)
    assert repository.list_feedback() == []


# --- storage failure ---------------------------------------------------------


def test_a_failing_save_is_503_and_makes_no_saved_copy(blobs: RecordingBlobStore) -> None:
    client = client_for(make_app(blobs, repository=BrokenRepository()))

    response = post(client, like())

    assert response.status_code == 503
    assert not blobs.inner.exists(blobs.ref_for(f"saved/{ARTICLE_ID}.html"))


# --- saved copies ------------------------------------------------------------


def test_a_like_copies_the_article_html_to_saved(blobs: RecordingBlobStore) -> None:
    client = client_for(make_app(blobs))

    assert post(client, like()).status_code == 204

    assert blobs.inner.get(blobs.ref_for(f"saved/{ARTICLE_ID}.html")) == ARTICLE_HTML
    # The original stays where the 30-day rule can expire it.
    assert blobs.inner.exists(blobs.ref_for(f"articles/{ARTICLE_ID}.html"))


def test_a_liked_article_is_still_served_after_the_original_is_gone(
    tmp_path: Path,
) -> None:
    blobs = LocalBlobStore(tmp_path / "blobs")
    blobs.put(f"articles/{ARTICLE_ID}.html", ARTICLE_HTML, "text/html")
    client = client_for(make_app(blobs))

    assert post(client, like()).status_code == 204
    (tmp_path / "blobs" / "articles" / f"{ARTICLE_ID}.html").unlink()

    response = client.get(f"/api/articles/{ARTICLE_ID}", headers=auth())
    assert response.status_code == 200
    assert response.content == ARTICLE_HTML


@pytest.mark.parametrize(
    ("kind", "value"),
    [("open", None), ("read", 30), ("dismiss", 1), ("mute_source", 1), ("like", 0)],
)
def test_only_a_like_with_a_nonzero_value_makes_a_saved_copy(
    blobs: RecordingBlobStore, kind: str, value: float | None
) -> None:
    repository = InMemoryRepository()
    client = client_for(make_app(blobs, repository=repository))

    assert post(client, event(event=kind, value=value)).status_code == 204

    assert not blobs.inner.exists(blobs.ref_for(f"saved/{ARTICLE_ID}.html"))
    assert len(repository.list_feedback()) == 1


def test_a_like_taken_back_is_stored_and_leaves_the_saved_copy(
    blobs: RecordingBlobStore,
) -> None:
    repository = InMemoryRepository()
    client = client_for(make_app(blobs, repository=repository))
    post(client, like())

    response = post(client, event(id=OTHER_ID, event="like", value=0))

    assert response.status_code == 204
    assert len(repository.list_feedback()) == 2
    assert blobs.inner.get(blobs.ref_for(f"saved/{ARTICLE_ID}.html")) == ARTICLE_HTML


def test_a_like_does_not_overwrite_an_existing_saved_copy(blobs: RecordingBlobStore) -> None:
    saved = b"<article>the copy kept earlier</article>"
    blobs.put(f"saved/{ARTICLE_ID}.html", saved, "text/html")
    client = client_for(make_app(blobs))

    assert post(client, like()).status_code == 204

    assert blobs.inner.get(blobs.ref_for(f"saved/{ARTICLE_ID}.html")) == saved


def test_a_like_for_an_item_with_no_blob_is_204_and_stored(
    caplog: pytest.LogCaptureFixture,
) -> None:
    blobs = RecordingBlobStore()  # a link item has no articles/<id>.html
    repository = InMemoryRepository()
    client = client_for(make_app(blobs, repository=repository))

    with caplog.at_level(logging.DEBUG):
        response = post(client, like(article_id="bbbbbbbbbbbbbbbb"))

    assert response.status_code == 204
    assert len(repository.list_feedback()) == 1
    assert not blobs.inner.exists(blobs.ref_for("saved/bbbbbbbbbbbbbbbb.html"))
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_failing_copy_is_204_with_the_event_stored_and_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    blobs = BrokenPutBlobs()
    blobs.inner.put(f"articles/{ARTICLE_ID}.html", ARTICLE_HTML, "text/html")
    repository = InMemoryRepository()
    client = client_for(make_app(blobs, repository=repository))

    with caplog.at_level(logging.DEBUG):
        response = post(client, like())

    assert response.status_code == 204
    assert [f.id for f in repository.list_feedback()] == [EVENT_ID]
    assert blobs.put_calls, "the copy should have been attempted"
    assert [r for r in caplog.records if r.levelno == logging.WARNING]


# --- real adapters -----------------------------------------------------------


def _article(article_id_url: str, content_ref: str | None) -> Article:
    return Article.model_validate(
        {
            "canonical_url": article_id_url,
            "url": article_id_url,
            "source_id": "gazette",
            "title": "An invented story",
            "fetched_at": datetime(2026, 11, 7, 7, 0, tzinfo=UTC),
            "content_ref": content_ref,
            "score": 1.0,
        }
    )


def test_a_like_through_sqlite_and_local_blobs_survives_a_pipeline_run(
    tmp_path: Path,
) -> None:
    blobs = LocalBlobStore(tmp_path / "blobs")
    with SqliteRepository(tmp_path / "newsfeed.db") as repository:
        row = _article("https://www.news.example.com/invented-story", None)
        key = f"articles/{row.id}.html"
        ref = blobs.put(key, ARTICLE_HTML, "text/html")
        repository.upsert_article(row.model_copy(update={"content_ref": ref}))
        snapshot = repository.get_article(row.id)  # what a run holds while it works
        assert snapshot is not None
        client = client_for(make_app(blobs, repository=repository))

        response = post(client, like(article_id=row.id))
        assert response.status_code == 204

        # The run finishes and writes its snapshot back, as rank does.
        repository.upsert_article(snapshot.model_copy(update={"score": 2.0}))
        after = repository.get_article(row.id)
        assert after is not None
        assert after.content_ref == ref
        assert after.score == 2.0
        assert [f.id for f in repository.list_feedback()] == [EVENT_ID]

        blobs_dir = tmp_path / "blobs"
        (blobs_dir / "articles" / f"{row.id}.html").unlink()
        served = client.get(f"/api/articles/{row.id}", headers=auth())
        assert served.status_code == 200
        assert served.content == ARTICLE_HTML


def test_a_retried_post_through_sqlite_is_one_row(tmp_path: Path) -> None:
    with SqliteRepository(tmp_path / "newsfeed.db") as repository:
        client = client_for(make_app(LocalBlobStore(tmp_path / "blobs"), repository=repository))

        for _ in range(3):
            assert post(client, event(created_at="2026-11-07T08:30:00.000Z")).status_code == 204

        assert len(repository.list_feedback()) == 1
        assert repository.list_feedback(since=datetime(2026, 11, 7, tzinfo=UTC) - timedelta(1))


# --- the API writes no article rows ------------------------------------------


def test_nothing_in_the_api_package_writes_article_rows() -> None:
    sources = list(SRC_API.rglob("*.py"))
    assert sources, "found no API sources to scan"
    offenders = [
        f"{path.name}: {name}"
        for path in sources
        for name in ("upsert_article", "delete_article")
        if name in path.read_text()
    ]
    assert offenders == []
    assert (SRC_API / "feedback.py").is_file(), "the feedback route belongs in api/feedback.py"


# --- #240: gaps found in review ------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        event(event="read", value="1"),
        event(event="read", value="30.5"),
        event(event="like", value=True),
        event(created_at=1794040200),
        event(created_at=1794040200.5),
    ],
    ids=["string-value", "decimal-string-value", "boolean-value", "epoch-int", "epoch-float"],
)
def test_a_value_or_time_of_the_wrong_json_type_is_422_and_stores_nothing(
    blobs: RecordingBlobStore, body: dict[str, Any]
) -> None:
    repository = InMemoryRepository()
    client = client_for(make_app(blobs, repository=repository))

    response = post(client, body)

    assert response.status_code == 422
    assert repository.list_feedback() == []
    assert not blobs.inner.exists(blobs.ref_for(f"saved/{ARTICLE_ID}.html"))


def test_a_body_over_the_limit_with_a_declared_length_is_exactly_413(
    blobs: RecordingBlobStore,
) -> None:
    repository = InMemoryRepository()
    client = client_for(make_app(blobs, repository=repository))
    padded = json.dumps({**like(), "padding": "x" * 5000})

    response = post(client, padded)

    assert response.status_code == 413
    assert repository.list_feedback() == []
    assert not blobs.inner.exists(blobs.ref_for(f"saved/{ARTICLE_ID}.html"))


def test_a_chunked_body_over_the_limit_is_exactly_413_and_stores_nothing(
    blobs: RecordingBlobStore,
) -> None:
    repository = InMemoryRepository()
    client = client_for(make_app(blobs, repository=repository))
    # An iterator body is sent without Content-Length, so only the running count can stop it.
    pieces = [b'{"padding": "', b"x" * 3000, b"x" * 3000, b'"}']

    response = client.post(
        "/api/feedback", content=iter(pieces), headers={**auth(), **JSON_HEADERS}
    )

    assert response.status_code == 413
    assert repository.list_feedback() == []


def _on_event_loop() -> bool:
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class LoopWatchingRepository(InMemoryRepository):
    """Records whether save_feedback ran on the event loop's thread."""

    def __init__(self) -> None:
        super().__init__()
        self.on_loop: list[bool] = []

    def save_feedback(self, feedback: Feedback) -> None:
        self.on_loop.append(_on_event_loop())
        super().save_feedback(feedback)


class LoopWatchingBlobs(RecordingBlobStore):
    """Records whether each blob call ran on the event loop's thread."""

    def __init__(self) -> None:
        super().__init__()
        self.on_loop: list[bool] = []

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self.on_loop.append(_on_event_loop())
        return super().put(key, data, content_type)

    def get(self, ref: str) -> bytes:
        self.on_loop.append(_on_event_loop())
        return super().get(ref)

    def exists(self, ref: str) -> bool:
        self.on_loop.append(_on_event_loop())
        return super().exists(ref)


def test_the_store_calls_do_not_block_the_event_loop() -> None:
    blobs = LoopWatchingBlobs()
    blobs.inner.put(f"articles/{ARTICLE_ID}.html", ARTICLE_HTML, "text/html")
    blobs.on_loop.clear()
    repository = LoopWatchingRepository()
    client = client_for(make_app(blobs, repository=repository))

    assert post(client, like()).status_code == 204

    assert repository.on_loop == [False], "save_feedback ran on the event loop"
    assert blobs.on_loop, "the like should have touched the blob store"
    assert not any(blobs.on_loop), "a blob call ran on the event loop"
