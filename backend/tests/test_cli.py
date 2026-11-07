"""Tests for the ``newsfeed`` command-line entry point, with fakes in place of HTTP."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from newsfeed import cli, wiring
from newsfeed.adapters.sqlite_repository import SqliteRepository
from newsfeed.models import Article, ExtractionStatus
from tests.fakes import FakeFetcher

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

runner = CliRunner()

FIXTURES = Path(__file__).parent / "fixtures"
FEED_URL = "https://news.example.com/feed.xml"
SOURCES_YAML = f"""
sources:
  - id: gazette
    type: rss
    url: {FEED_URL}
  - id: disabled-feed
    type: rss
    url: https://off.example.com/feed.xml
    enabled: false
"""


@pytest.fixture
def fetcher() -> FakeFetcher:
    fake = FakeFetcher(fixtures_dir=FIXTURES)
    fake.add_file(FEED_URL, "feeds/pipeline_feed.xml")
    fake.add_file(
        "https://news.example.com/2026/10/bridge?utm_source=rss", "extract/news_article.html"
    )
    fake.add_file("https://blog.example.org/2026/10/post", "extract/blog_post.html")
    return fake


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fetcher: FakeFetcher) -> Path:
    config = tmp_path / "config"
    config.mkdir()
    (config / "sources.yaml").write_text(SOURCES_YAML)
    (config / "url_params.yaml").write_text("domains: {}\n")
    (config / "extraction.yaml").write_text("domains: {}\n")
    data = tmp_path / "data"
    monkeypatch.setenv("NEWSFEED_CONFIG_DIR", str(config))
    monkeypatch.setenv("NEWSFEED_DATA_DIR", str(data))

    @contextmanager
    def open_fake(settings: object) -> Iterator[FakeFetcher]:
        yield fetcher

    monkeypatch.setattr(wiring, "open_fetcher", open_fake)
    monkeypatch.setattr(cli, "open_fetcher", open_fake)
    return data


def test_run_ingests_and_extracts_into_the_data_dir(env: Path) -> None:
    result = runner.invoke(cli.app, ["run"])

    assert result.exit_code == 0, result.output
    assert "ingest: duplicates=0, entries=4, new=4, sources=1" in result.output
    assert "errors: 2" in result.output  # two pages the fake does not serve
    with SqliteRepository(env / "newsfeed.db") as repo:
        articles = repo.list_articles()
        assert len(articles) == 4
        assert any(a.content_ref and (env / "blobs" / a.content_ref).exists() for a in articles)


def test_ingest_then_extract_are_separate_stages(env: Path) -> None:
    ingested = runner.invoke(cli.app, ["ingest"])

    assert ingested.exit_code == 0, ingested.output
    with SqliteRepository(env / "newsfeed.db") as repo:
        assert {a.extraction.status for a in repo.list_articles()} == {ExtractionStatus.PENDING}

    extracted = runner.invoke(cli.app, ["extract", "--limit", "1"])

    assert extracted.exit_code == 0, extracted.output
    assert "extract: attempted=1" in extracted.output


def _store_pending(env: Path, fetcher: FakeFetcher) -> dict[str, Article]:
    """Pending articles from a disabled source, a removed one and an ad hoc save."""
    articles = {
        source_id: Article(
            canonical_url=f"https://{source_id}.example.com/a",
            url=f"https://{source_id}.example.com/a",
            source_id=source_id,
            title="Pending",
            fetched_at=datetime(2026, 10, 1, tzinfo=UTC),
        )
        for source_id in ("disabled-feed", "removed-feed", "adhoc")
    }
    for article in articles.values():
        fetcher.add_file(article.url, "extract/blog_post.html")
    env.mkdir(parents=True, exist_ok=True)
    with SqliteRepository(env / "newsfeed.db") as repo:
        for article in articles.values():
            repo.upsert_article(article)
    return articles


def test_plain_extract_drains_pending_articles_of_any_source(
    env: Path, fetcher: FakeFetcher
) -> None:
    """Regression: a plain extract must not filter by the enabled sources."""
    articles = _store_pending(env, fetcher)

    result = runner.invoke(cli.app, ["extract"])

    assert result.exit_code == 0, result.output
    with SqliteRepository(env / "newsfeed.db") as repo:
        for article in articles.values():
            stored = repo.get_article(article.id)
            assert stored is not None
            assert stored.extraction.status is not ExtractionStatus.PENDING, article.source_id


def test_extract_with_source_narrows_to_that_source(env: Path, fetcher: FakeFetcher) -> None:
    articles = _store_pending(env, fetcher)

    result = runner.invoke(cli.app, ["extract", "--source", "disabled-feed"])

    assert result.exit_code == 0, result.output
    assert "extract: attempted=1" in result.output
    with SqliteRepository(env / "newsfeed.db") as repo:
        stored = {a.source_id: a.extraction.status for a in repo.list_articles()}
    statuses = {source_id: stored[source_id] for source_id in articles}
    assert statuses["disabled-feed"] is not ExtractionStatus.PENDING
    assert statuses["removed-feed"] is ExtractionStatus.PENDING
    assert statuses["adhoc"] is ExtractionStatus.PENDING


def test_unknown_source_is_rejected(env: Path) -> None:
    result = runner.invoke(cli.app, ["ingest", "--source", "nope"])

    assert result.exit_code == 2
    assert "unknown source" in result.output


def test_since_accepts_a_date(env: Path) -> None:
    result = runner.invoke(cli.app, ["ingest", "--since", "2999-01-01"])

    assert result.exit_code == 0, result.output


def test_missing_config_is_a_clean_error(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEWSFEED_CONFIG_DIR", str(env / "nowhere"))

    result = runner.invoke(cli.app, ["run"])

    assert result.exit_code == 1
    assert "error:" in result.output


def test_extract_url_prints_metadata_and_stores_nothing(env: Path) -> None:
    result = runner.invoke(
        cli.app, ["extract", "--url", "https://news.example.com/2026/10/bridge?utm_source=rss"]
    )

    assert result.exit_code == 0, result.output
    assert "title: Marrowby council approves floating cycle bridge" in result.output
    assert "Councillors in Lower Marrowby" in result.output
    assert not (env / "newsfeed.db").exists()


def test_extract_url_save_stores_the_article(env: Path) -> None:
    url = "https://news.example.com/2026/10/bridge?utm_source=rss"

    result = runner.invoke(cli.app, ["extract", "--url", url, "--save"])

    assert result.exit_code == 0, result.output
    with SqliteRepository(env / "newsfeed.db") as repo:
        (article,) = repo.list_articles()
        assert article.source_id == "adhoc"
        assert article.extraction.status is ExtractionStatus.OK


def test_extract_url_save_reports_the_id_of_a_rekeyed_article(env: Path) -> None:
    """Regression: the bridge page declares another canonical URL, so the run
    re-keys its article; --save on the feed URL must name the id that exists."""
    url = "https://news.example.com/2026/10/bridge?utm_source=rss"
    assert runner.invoke(cli.app, ["run"]).exit_code == 0

    result = runner.invoke(cli.app, ["extract", "--url", url, "--save"])

    assert result.exit_code == 0, result.output
    with SqliteRepository(env / "newsfeed.db") as repo:
        articles = repo.list_articles()
        (bridge,) = [a for a in articles if a.url == url]
    assert len(articles) == 4
    assert bridge.source_id == "gazette"
    assert f"saved article {bridge.id} (ok)" in result.output


def test_extract_url_reports_an_unreachable_page(env: Path) -> None:
    result = runner.invoke(cli.app, ["extract", "--url", "https://news.example.com/nothing"])

    assert result.exit_code == 1
    assert "404" in result.output


def test_extract_url_rejects_conflicting_options(env: Path) -> None:
    result = runner.invoke(cli.app, ["extract", "--url", "https://x.example.com/", "--limit", "3"])

    assert result.exit_code == 2

    result = runner.invoke(cli.app, ["extract", "--save"])

    assert result.exit_code == 2


def test_help_lists_commands() -> None:
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0, result.output
    for command in ("run", "ingest", "extract"):
        assert command in result.output


def test_rank_scores_recent_articles_from_interests_yaml(env: Path) -> None:
    (env.parent / "config" / "interests.yaml").write_text(
        "topics:\n  - {name: bridges, keywords: [bridge], weight: 2}\n"
    )
    assert runner.invoke(cli.app, ["run"]).exit_code == 0
    with SqliteRepository(env / "newsfeed.db") as repo:
        for article in repo.list_articles():
            repo.upsert_article(Article.model_validate({**article.model_dump(), "score": None}))

    result = runner.invoke(cli.app, ["rank"])

    assert result.exit_code == 0, result.output
    assert "rank: " in result.output
    with SqliteRepository(env / "newsfeed.db") as repo:
        articles = repo.list_articles()
        assert all(a.score is not None for a in articles)
        assert any("topic:bridges" in a.score_breakdown for a in articles)


def test_run_and_publish_write_feed_json_to_the_data_dir(env: Path) -> None:
    assert runner.invoke(cli.app, ["run"]).exit_code == 0
    feed_path = env / "blobs" / "feed.json"
    first = json.loads(feed_path.read_bytes())
    assert first["items"]
    feed_path.unlink()

    result = runner.invoke(cli.app, ["publish"])

    assert result.exit_code == 0, result.output
    assert "publish: items=" in result.output
    again = json.loads(feed_path.read_bytes())
    assert [i["id"] for i in again["items"]] == [i["id"] for i in first["items"]]


def test_report_prints_a_summary_after_a_run(env: Path) -> None:
    assert runner.invoke(cli.app, ["run"]).exit_code == 0

    text = runner.invoke(cli.app, ["report"])
    as_json = runner.invoke(cli.app, ["report", "--json"])

    assert text.exit_code == 0, text.output
    assert "Extraction by domain" in text.output
    assert as_json.exit_code == 0, as_json.output
    assert len(json.loads(as_json.output)["run_ids"]) == 1


def test_report_rejects_an_unknown_run_and_conflicting_options(env: Path) -> None:
    assert runner.invoke(cli.app, ["report", "--run", "nope"]).exit_code == 1
    assert runner.invoke(cli.app, ["report", "--run", "x", "--since", "2026-01-01"]).exit_code == 2


# --- newsfeed serve (#175) ---------------------------------------------------
# uvicorn and fastapi are imported inside the fixtures, so the tests above still
# run before the API's dependencies are installed.

_SERVE_ENV = (
    "NEWSFEED_API_AUTH",
    "NEWSFEED_OWNER_EMAIL",
    "NEWSFEED_IAP_AUDIENCE",
    "NEWSFEED_CLIENT_DIR",
    "NEWSFEED_STORAGE",
    "NEWSFEED_GCS_BUCKET",
    "PORT",
)
_INVENTED_OWNER = "owner@example.com"
_FEED = b'{"schema_version": 1, "items": [], "note": "served-feed-marker"}'


@pytest.fixture
def serve_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A data dir holding a feed, no API variables, and no ``.env`` in the working dir."""
    for name in _SERVE_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    data = tmp_path / "data"
    (data / "blobs").mkdir(parents=True)
    (data / "blobs" / "feed.json").write_bytes(_FEED)
    monkeypatch.setenv("NEWSFEED_DATA_DIR", str(data))
    monkeypatch.setenv("NEWSFEED_CONFIG_DIR", str(tmp_path / "config"))
    return data


@pytest.fixture
def uvicorn_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Replace ``uvicorn.run`` so nothing binds a socket; record its arguments."""
    import inspect

    import uvicorn

    real = uvicorn.run
    calls: list[dict[str, object]] = []

    def fake_run(*args: object, **kwargs: object) -> None:
        bound = inspect.signature(real).bind(*args, **kwargs)
        bound.apply_defaults()
        calls.append(dict(bound.arguments))

    monkeypatch.setattr(uvicorn, "run", fake_run)
    return calls


def _served_client(calls: list[dict[str, object]]) -> TestClient:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    assert len(calls) == 1, calls
    app = calls[0]["app"]
    assert isinstance(app, FastAPI), f"serve should pass the app object, got {app!r}"
    return TestClient(app, raise_server_exceptions=False)


def test_serve_refuses_to_start_without_an_owner_email(
    serve_env: Path, uvicorn_calls: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEWSFEED_API_AUTH", "none")

    result = runner.invoke(cli.app, ["serve"])

    assert result.exit_code != 0
    assert uvicorn_calls == []


def test_serve_refuses_to_start_with_iap_and_no_audience(
    serve_env: Path, uvicorn_calls: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fail closed: google-auth skips the audience check when given none.
    monkeypatch.setenv("NEWSFEED_OWNER_EMAIL", _INVENTED_OWNER)

    result = runner.invoke(cli.app, ["serve"])

    assert result.exit_code != 0
    assert uvicorn_calls == []


def test_serve_without_auth_binds_to_localhost_and_serves_the_owner(
    serve_env: Path,
    uvicorn_calls: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client_dir = tmp_path / "dist"
    client_dir.mkdir()
    (client_dir / "index.html").write_text("<!doctype html>client-index-marker")
    monkeypatch.setenv("NEWSFEED_API_AUTH", "none")
    monkeypatch.setenv("NEWSFEED_OWNER_EMAIL", _INVENTED_OWNER)
    monkeypatch.setenv("NEWSFEED_CLIENT_DIR", str(client_dir))
    monkeypatch.setenv("PORT", "9123")

    result = runner.invoke(cli.app, ["serve"])

    assert result.exit_code == 0, result.output
    assert uvicorn_calls[0]["host"] == "127.0.0.1"
    assert uvicorn_calls[0]["port"] == 9123
    client = _served_client(uvicorn_calls)
    feed = client.get("/api/feed")  # no IAP header: the dev verifier accepts it as the owner
    assert feed.status_code == 200
    assert feed.content == _FEED
    assert "client-index-marker" in client.get("/reconnect").text


def test_serve_with_iap_binds_to_all_interfaces_and_refuses_a_missing_jwt(
    serve_env: Path, uvicorn_calls: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEWSFEED_API_AUTH", "iap")
    monkeypatch.setenv("NEWSFEED_OWNER_EMAIL", _INVENTED_OWNER)
    monkeypatch.setenv(
        "NEWSFEED_IAP_AUDIENCE", "/projects/123456789/locations/europe-west2/services/reader-api"
    )

    result = runner.invoke(cli.app, ["serve"])

    assert result.exit_code == 0, result.output
    assert uvicorn_calls[0]["host"] == "0.0.0.0"
    assert uvicorn_calls[0]["port"] == 8080
    response = _served_client(uvicorn_calls).get("/api/feed")
    assert response.status_code == 401
    assert b"served-feed-marker" not in response.content
