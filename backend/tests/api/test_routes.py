"""The read routes and the client route, for the owner (#175)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from newsfeed.adapters.localfs_blobstore import LocalBlobStore
from newsfeed.adapters.sqlite_repository import SqliteRepository
from newsfeed.config import UrlParamsConfig
from newsfeed.models import Article, ExtractionStatus, Source, SourceType
from newsfeed.publish.feed import publish_feed
from newsfeed.wiring import Services
from tests.api.support import (
    ARTICLE_HTML,
    ARTICLE_ID,
    ASSET_MARKER,
    FEED_BYTES,
    INDEX_MARKER,
    SAVED_MARKER,
    SECRET_MARKER,
    RecordingBlobStore,
    UntouchableRepository,
    auth,
    client_for,
    make_app,
)
from tests.fakes import FakeExtractor, FakeFetcher

API_CACHE = "private, no-cache"
IMMUTABLE = "private, max-age=31536000, immutable"

# --- GET /api/feed -----------------------------------------------------------


def test_the_owner_gets_the_stored_feed_bytes_with_an_etag(blobs: RecordingBlobStore) -> None:
    response = client_for(make_app(blobs)).get("/api/feed", headers=auth())

    assert response.status_code == 200
    assert response.content == FEED_BYTES
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == API_CACHE
    etag = response.headers["etag"]
    assert etag and not etag.startswith("W/"), "the ETag must be strong"


def test_a_matching_if_none_match_gets_304_with_an_empty_body(blobs: RecordingBlobStore) -> None:
    client = client_for(make_app(blobs))
    etag = client.get("/api/feed", headers=auth()).headers["etag"]

    response = client.get("/api/feed", headers={**auth(), "If-None-Match": etag})

    assert response.status_code == 304
    assert response.content == b""


def test_the_etag_follows_the_feed_bytes(blobs: RecordingBlobStore) -> None:
    client = client_for(make_app(blobs))
    old = client.get("/api/feed", headers=auth()).headers["etag"]
    assert client.get("/api/feed", headers=auth()).headers["etag"] == old

    blobs.put("feed.json", FEED_BYTES + b"\n", "application/json")
    response = client.get("/api/feed", headers={**auth(), "If-None-Match": old})

    assert response.status_code == 200
    assert response.content == FEED_BYTES + b"\n"
    assert response.headers["etag"] != old


def test_no_feed_yet_is_503_not_a_crash() -> None:
    response = client_for(make_app(RecordingBlobStore())).get("/api/feed", headers=auth())

    assert response.status_code == 503
    assert "detail" in response.json()


def test_the_read_routes_need_no_repository_read(blobs: RecordingBlobStore) -> None:
    repository = UntouchableRepository()
    client = client_for(make_app(blobs, repository=repository))

    assert client.get("/api/feed", headers=auth()).status_code == 200
    assert client.get(f"/api/articles/{ARTICLE_ID}", headers=auth()).status_code == 200
    assert repository.calls == []


# --- GET /api/articles/{id} --------------------------------------------------


def test_the_owner_gets_an_articles_html(blobs: RecordingBlobStore) -> None:
    response = client_for(make_app(blobs)).get(f"/api/articles/{ARTICLE_ID}", headers=auth())

    assert response.status_code == 200
    assert response.content == ARTICLE_HTML
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.headers["cache-control"] == API_CACHE
    assert response.headers["etag"]


def test_a_saved_copy_is_preferred_to_the_article_blob(blobs: RecordingBlobStore) -> None:
    saved = f"<p>{SAVED_MARKER}</p>".encode()
    blobs.put(f"saved/{ARTICLE_ID}.html", saved, "text/html")

    response = client_for(make_app(blobs)).get(f"/api/articles/{ARTICLE_ID}", headers=auth())

    assert response.status_code == 200
    assert response.content == saved


def test_a_saved_copy_is_served_when_the_article_blob_expired() -> None:
    store = RecordingBlobStore()
    saved = f"<p>{SAVED_MARKER}</p>".encode()
    store.put(f"saved/{ARTICLE_ID}.html", saved, "text/html")

    response = client_for(make_app(store)).get(f"/api/articles/{ARTICLE_ID}", headers=auth())

    assert response.status_code == 200
    assert response.content == saved


def test_an_article_whose_blob_expired_is_404(blobs: RecordingBlobStore) -> None:
    response = client_for(make_app(blobs)).get("/api/articles/fedcba9876543210", headers=auth())

    assert response.status_code == 404
    assert ARTICLE_HTML not in response.content


@pytest.mark.parametrize(
    "raw_id",
    [
        "..%2Ffeed.json",
        "%2E%2E%2Ffeed.json",
        "%2e%2e",
        "feed.json",
        "0123456789ABCDEF",
        "0123456789abcdeF",
        "0123456789abcde",
        "0123456789abcdef0",
        "0123456789abcdeg",
        "0123456789abcdef%0A",
        "%200123456789abcdef",
    ],
)
def test_a_malformed_article_id_is_404_and_no_blob_is_read(
    blobs: RecordingBlobStore, raw_id: str
) -> None:
    response = client_for(make_app(blobs)).get(f"/api/articles/{raw_id}", headers=auth())

    assert response.status_code == 404
    assert blobs.reads == []


# --- Through the real pipeline -----------------------------------------------


def test_the_api_serves_what_the_real_publish_stage_wrote(tmp_path: Path) -> None:
    now = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    blobs = LocalBlobStore(tmp_path / "blobs")
    with SqliteRepository(tmp_path / "newsfeed.db") as repository:
        url = "https://www.news.example.com/invented-story"
        article = Article.model_validate(
            {
                "canonical_url": url,
                "url": url,
                "source_id": "gazette",
                "title": "An invented story",
                "fetched_at": now - timedelta(hours=1),
                "score": 1.0,
                "score_breakdown": {"source": 1.0},
                "extraction": {"status": ExtractionStatus.OK, "word_count": 300},
            }
        )
        ref = blobs.put(f"articles/{article.id}.html", ARTICLE_HTML, "text/html")
        repository.upsert_article(article.model_copy(update={"content_ref": ref}))
        services = Services(
            repository=repository,
            blobs=blobs,
            fetcher=FakeFetcher(),
            ingesters={},
            extractor=FakeExtractor(),
            url_rules=UrlParamsConfig(),
        )
        sources = [Source(id="gazette", type=SourceType.RSS, url="https://news.example.com/rss")]
        built = publish_feed(services, sources, run_id="20261018T120000-000000", now=now)
        assert built.feed["items"], "the fixture article should be published"

        client = client_for(make_app(blobs, repository=repository))
        feed = client.get("/api/feed", headers=auth())
        content = client.get(f"/api/articles/{article.id}", headers=auth())

    assert feed.status_code == 200
    assert feed.content == (tmp_path / "blobs" / "feed.json").read_bytes()
    assert feed.json()["items"][0]["id"] == article.id
    assert content.status_code == 200
    assert content.content == ARTICLE_HTML


# --- The client route --------------------------------------------------------


@pytest.mark.parametrize("url", ["/", "/reconnect", f"/article/{ARTICLE_ID}", "/index.html"])
def test_app_paths_load_index_html_with_no_cache(client_dir: Path, url: str) -> None:
    response = client_for(make_app(client_dir=client_dir)).get(url, headers=auth())

    assert response.status_code == 200
    assert INDEX_MARKER in response.text
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("url", ["/sw.js", "/manifest.webmanifest"])
def test_the_service_worker_and_manifest_are_no_cache(client_dir: Path, url: str) -> None:
    response = client_for(make_app(client_dir=client_dir)).get(url, headers=auth())

    assert response.status_code == 200
    assert response.content == (client_dir / url.lstrip("/")).read_bytes()
    assert response.headers["cache-control"] == "no-cache"


def test_hashed_assets_are_served_with_their_type_and_cached_for_a_year(client_dir: Path) -> None:
    response = client_for(make_app(client_dir=client_dir)).get(
        "/assets/app-abc123.js", headers=auth()
    )

    assert response.status_code == 200
    assert ASSET_MARKER in response.text
    assert "javascript" in response.headers["content-type"]
    assert response.headers["cache-control"] == IMMUTABLE


@pytest.mark.parametrize("url", ["/api/unknown", "/api/", "/api/feed/extra", "/api/articles"])
def test_an_unknown_api_path_is_404_never_index_html(client_dir: Path, url: str) -> None:
    response = client_for(make_app(client_dir=client_dir)).get(url, headers=auth())

    assert response.status_code == 404
    assert INDEX_MARKER not in response.text


def test_without_a_client_dir_the_client_route_is_404() -> None:
    client = client_for(make_app(client_dir=None))

    for url in ("/", "/reconnect", "/index.html"):
        assert client.get(url, headers=auth()).status_code == 404


@pytest.mark.parametrize(
    "url",
    [
        "/../pyproject.toml",
        "/%2e%2e/secret",
        "/%2e%2e/pyproject.toml",
        "/%2E%2E/secret",
        "/assets/%2e%2e/%2e%2e/secret",
        "/..%2fsecret",
        "/%2e%2e%2fsecret",
        "/escape.txt",
        "/assets/escape.js",
    ],
)
def test_nothing_outside_the_client_dir_is_served(client_dir: Path, url: str) -> None:
    response = client_for(make_app(client_dir=client_dir)).get(url, headers=auth())

    assert SECRET_MARKER not in response.text, f"{url} served a file outside client_dir"
