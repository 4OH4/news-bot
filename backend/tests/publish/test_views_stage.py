"""The publish stage with views (#288, #297): reads, a failed view, max_items, and paging."""

from __future__ import annotations

import base64
import json
from collections.abc import Collection
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from newsfeed.config import UrlParamsConfig, ViewConfig, ViewsConfig
from newsfeed.models import Article, ExtractionStatus, PipelineStage, Source, SourceType
from newsfeed.pipeline import RunTally, publish_stage
from newsfeed.wiring import Services
from tests.api.support import auth, client_for, make_app
from tests.fakes import FakeExtractor, FakeFetcher, InMemoryBlobStore, InMemoryRepository

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
RUN_ID = "20261115T120000-000000"
DOCS = Path(__file__).parents[3] / "docs"
VALIDATOR = Draft202012Validator(json.loads((DOCS / "feed.schema.json").read_text()))
SOURCES = [
    Source(id="gazette", type=SourceType.RSS, url="https://a.example.com/", tags=["news"]),
    Source(id="essays", type=SourceType.RSS, url="https://b.example.com/", tags=["long-form"]),
]
VIEWS = ViewsConfig(views={"news": ViewConfig(tags=("news",), order="newest", max_items=300)})
DEFAULT_VIEWS = ViewsConfig(
    views={
        "news": ViewConfig(tags=("news",), order="newest", max_items=300),
        "feed": ViewConfig(order="score", max_items=500),
        "longer": ViewConfig(tags=("long-form",), order="score", max_items=200),
    }
)


class RecordingRepository(InMemoryRepository):
    """Remembers each ``list_articles`` call; a read of ``fail_for`` raises."""

    def __init__(self, fail_for: str | None = None) -> None:
        super().__init__()
        self.reads: list[dict[str, object]] = []
        self.fail_for = fail_for

    def list_articles(
        self,
        *,
        status: ExtractionStatus | None = None,
        since: datetime | None = None,
        limit: int | None = None,
        source_ids: Collection[str] | None = None,
    ) -> list[Article]:
        self.reads.append(
            {
                "since": since,
                "limit": limit,
                "source_ids": None if source_ids is None else set(source_ids),
            }
        )
        if self.fail_for and source_ids and self.fail_for in source_ids:
            raise RuntimeError("index not ready")
        return super().list_articles(status=status, since=since, limit=limit, source_ids=source_ids)

    def read_for(self, source_ids: set[str] | None) -> list[dict[str, object]]:
        return [r for r in self.reads if r["source_ids"] == source_ids]


class ViewWritesFail(InMemoryBlobStore):
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        if key.startswith("views/"):
            raise OSError("bucket refused the write")
        return super().put(key, data, content_type)


def run(
    blobs: InMemoryBlobStore,
    stories: int,
    *,
    repository: InMemoryRepository | None = None,
    sources: list[Source] = SOURCES,
    views: ViewsConfig = VIEWS,
) -> RunTally:
    repository = repository or InMemoryRepository()
    for i in range(stories):
        url, at = f"https://a.example.com/story-{i}", NOW - timedelta(hours=1, minutes=i)
        row = {"canonical_url": url, "url": url, "source_id": "gazette", "title": f"Invented {i}"}
        stamps = {"fetched_at": at, "published_at": at, "score": 1.0 + i}
        repository.upsert_article(Article.model_validate(row | stamps))
    parts = {"fetcher": FakeFetcher(), "extractor": FakeExtractor(), "ingesters": {}}
    services = Services(repository, blobs, url_rules=UrlParamsConfig(), views=views, **parts)
    tally = RunTally()
    publish_stage(services, sources, tally, run_id=RUN_ID, now=NOW, window_hours=72)
    return tally


def test_a_failed_view_write_keeps_feed_json_and_counts_a_stage_error() -> None:
    blobs = ViewWritesFail()

    stats = run(blobs, 3).stats(RUN_ID, NOW, NOW)

    assert len(json.loads(blobs.get(blobs.ref_for("feed.json")))["items"]) == 3
    assert stats.error_count == 1
    assert stats.stage_counts[PipelineStage.PUBLISH]["views_failed"] == 1


def test_a_view_is_cut_to_its_max_items() -> None:
    blobs = InMemoryBlobStore()
    views = ViewsConfig(views={"news": ViewConfig(tags=("news",), order="newest", max_items=4)})

    run(blobs, 6, views=views)

    assert len(json.loads(blobs.get(blobs.ref_for("views/news.json")))["items"]) == 4


def test_the_default_views_make_three_reads_one_shared_by_feed_json_and_the_feed_view() -> None:
    repository = RecordingRepository()

    run(InMemoryBlobStore(), 3, repository=repository, views=DEFAULT_VIEWS)

    assert len(repository.reads) == 3
    (shared,) = repository.read_for(None)
    (news,) = repository.read_for({"gazette"})
    (longer,) = repository.read_for({"essays"})
    assert shared["since"] == NOW - timedelta(hours=72)
    assert (news["since"], news["limit"]) == (None, 300)
    assert (longer["since"], longer["limit"]) == (None, 200)


def test_a_failed_read_for_one_view_keeps_its_file_and_the_rest_are_written() -> None:
    blobs = InMemoryBlobStore()
    blobs.put("views/news.json", b'{"run_id": "old", "generated_at": "x", "items": []}')

    tally = run(blobs, 3, repository=RecordingRepository(fail_for="gazette"), views=DEFAULT_VIEWS)

    assert json.loads(blobs.get(blobs.ref_for("views/news.json")))["run_id"] == "old"
    assert json.loads(blobs.get(blobs.ref_for("feed.json")))["run_id"] == RUN_ID
    for name in ("feed", "longer"):
        assert json.loads(blobs.get(blobs.ref_for(f"views/{name}.json")))["run_id"] == RUN_ID
    counts = tally.stats(RUN_ID, NOW, NOW).stage_counts[PipelineStage.PUBLISH]
    assert counts["views_failed"] == 1


def test_a_tag_with_only_a_disabled_source_is_an_empty_view_and_makes_no_read() -> None:
    blobs = InMemoryBlobStore()
    repository = RecordingRepository()
    disabled = SOURCES[0].model_copy(update={"enabled": False})

    run(blobs, 2, repository=repository, sources=[disabled, SOURCES[1]], views=DEFAULT_VIEWS)

    assert json.loads(blobs.get(blobs.ref_for("views/news.json")))["items"] == []
    assert repository.read_for({"gazette"}) == []


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
