"""The publish stage with views (#288): a failed view, the safety cap, and paging end to end."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from newsfeed.config import UrlParamsConfig, ViewConfig, ViewsConfig
from newsfeed.models import Article, PipelineStage, Source, SourceType
from newsfeed.pipeline import RunTally, publish_stage
from newsfeed.publish import views as views_module
from newsfeed.wiring import Services
from tests.api.support import auth, client_for, make_app
from tests.fakes import FakeExtractor, FakeFetcher, InMemoryBlobStore, InMemoryRepository

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
RUN_ID = "20261115T120000-000000"
DOCS = Path(__file__).parents[3] / "docs"
VALIDATOR = Draft202012Validator(json.loads((DOCS / "feed.schema.json").read_text()))
SOURCES = [Source(id="gazette", type=SourceType.RSS, url="https://a.example.com/", tags=["news"])]
VIEWS = ViewsConfig(views={"news": ViewConfig(tags=("news",), order="newest", window_hours=72)})


class ViewWritesFail(InMemoryBlobStore):
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        if key.startswith("views/"):
            raise OSError("bucket refused the write")
        return super().put(key, data, content_type)


def run(blobs: InMemoryBlobStore, stories: int) -> RunTally:
    repository = InMemoryRepository()
    for i in range(stories):
        url, at = f"https://a.example.com/story-{i}", NOW - timedelta(hours=1, minutes=i)
        row = {"canonical_url": url, "url": url, "source_id": "gazette", "title": f"Invented {i}"}
        stamps = {"fetched_at": at, "published_at": at, "score": 1.0 + i}
        repository.upsert_article(Article.model_validate(row | stamps))
    parts = {"fetcher": FakeFetcher(), "extractor": FakeExtractor(), "ingesters": {}}
    services = Services(repository, blobs, url_rules=UrlParamsConfig(), views=VIEWS, **parts)
    tally = RunTally()
    publish_stage(services, SOURCES, tally, run_id=RUN_ID, now=NOW, window_hours=72)
    return tally


def test_a_failed_view_write_keeps_feed_json_and_counts_a_stage_error() -> None:
    blobs = ViewWritesFail()

    stats = run(blobs, 3).stats(RUN_ID, NOW, NOW)

    assert len(json.loads(blobs.get(blobs.ref_for("feed.json")))["items"]) == 3
    assert stats.error_count == 1
    assert stats.stage_counts[PipelineStage.PUBLISH]["views_failed"] == 1


def test_a_view_over_the_safety_cap_is_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(views_module, "VIEW_SAFETY_CAP", 4)
    blobs = InMemoryBlobStore()

    run(blobs, 6)

    assert len(json.loads(blobs.get(blobs.ref_for("views/news.json")))["items"]) == 4


def test_a_published_view_pages_through_the_api_and_every_page_is_valid() -> None:
    blobs = InMemoryBlobStore()
    run(blobs, 7)
    client = client_for(make_app(blobs))
    seen: list[str] = []
    params: dict[str, object] = {"view": "news", "limit": 3}
    while params.get("cursor", "") is not None:
        body = client.get("/api/feed", params=params, headers=auth()).json()
        VALIDATOR.validate(body)
        seen += [item["id"] for item in body["items"]]
        params["cursor"] = body["next_cursor"]

    view = json.loads(blobs.get(blobs.ref_for("views/news.json")))
    assert seen == [item["id"] for item in view["items"]] and len(seen) == 7
    VALIDATOR.validate(json.loads((DOCS / "feed.page.example.json").read_text()))


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


@pytest.mark.parametrize(
    "cursor", [_b64("run-1:-5"), _b64("no offset"), _b64("run:٣"), _b64("run:1\n"), "x" * 300]
)
def test_a_cursor_that_decodes_to_something_else_is_400(cursor: str) -> None:
    blobs = InMemoryBlobStore()
    run(blobs, 1)

    response = client_for(make_app(blobs)).get(
        "/api/feed", params={"view": "news", "cursor": cursor}, headers=auth()
    )

    assert response.status_code == 400
