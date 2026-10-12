"""Tests for the ``newsfeed`` command-line entry point, with fakes in place of HTTP."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner

from newsfeed import cli, wiring
from newsfeed.adapters.sqlite_repository import SqliteRepository
from newsfeed.models import ExtractionStatus
from tests.fakes import FakeFetcher

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
