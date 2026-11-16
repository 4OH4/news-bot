"""Feed views (#288): publish writes ``views/<name>.json`` beside ``feed.json``.

These run ``newsfeed publish`` against the local SQLite repository and filesystem
blob store, with the repo's own ``config/views.yaml``, so they hold whichever
function inside the publish stage builds the views. The CLI has no clock option,
so "now" is the wall clock on both sides: every age below is hours or days away
from a window edge, so a slow run can't cross one.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from newsfeed import cli, wiring
from newsfeed.adapters.localfs_blobstore import LocalBlobStore
from newsfeed.adapters.sqlite_repository import SqliteRepository
from newsfeed.models import Article, ExtractionStatus, Source
from newsfeed.publish.feed import build_feed
from tests.fakes import FakeFetcher

runner = CliRunner()

REPO = Path(__file__).parents[3]
DOMAINS = {
    "bbc-news": "news.example.com",
    "guardian-news": "paper.example.com",
    "aeon": "essays.example.org",
    "techblog": "blog.example.net",
    "off-news": "off.example.com",
    "zero-news": "zero.example.com",
    "former-long": "former.example.org",
}
SOURCES_YAML = """
sources:
  - id: bbc-news
    type: rss
    url: https://news.example.com/rss
    tags: [news]
  - id: guardian-news
    type: rss
    url: https://paper.example.com/rss
    tags: [news]
  - id: aeon
    type: rss
    url: https://essays.example.org/rss
    tags: [long-form]
  - id: techblog
    type: rss
    url: https://blog.example.net/rss
    tags: [tech]
  - id: off-news
    type: rss
    url: https://off.example.com/rss
    enabled: false
    tags: [news, long-form]
  - id: zero-news
    type: rss
    url: https://zero.example.com/rss
    weight: 0
    tags: [news, long-form]
  - id: former-long
    type: rss
    url: https://former.example.org/rss
    tags: [science]
"""
VIEW_NAMES = ("news", "feed", "longer")


@dataclass
class Env:
    data: Path
    config: Path

    @property
    def blobs(self) -> LocalBlobStore:
        return LocalBlobStore(self.data / "blobs")


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    config = tmp_path / "config"
    config.mkdir()
    (config / "sources.yaml").write_text(SOURCES_YAML)
    (config / "url_params.yaml").write_text("domains: {}\n")
    (config / "extraction.yaml").write_text("domains: {}\n")
    shutil.copy(REPO / "config" / "views.yaml", config / "views.yaml")
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("NEWSFEED_CONFIG_DIR", str(config))
    monkeypatch.setenv("NEWSFEED_DATA_DIR", str(data))

    @contextmanager
    def no_http(settings: object) -> Iterator[FakeFetcher]:
        yield FakeFetcher()

    monkeypatch.setattr(wiring, "open_fetcher", no_http)
    monkeypatch.setattr(cli, "open_fetcher", no_http)
    return Env(data=data, config=config)


def now() -> datetime:
    return datetime.now(UTC)


def article(
    source_id: str,
    name: str,
    *,
    age_hours: float,
    score: float | None = 1.0,
    status: ExtractionStatus = ExtractionStatus.OK,
    muted: bool = False,
    cluster_id: str | None = None,
) -> Article:
    url = f"https://{DOMAINS[source_id]}/{name}"
    at = now() - timedelta(hours=age_hours)
    breakdown = {"source": 1.0, "recency": 0.5}
    if muted:
        breakdown["muted"] = 1.0
    return Article.model_validate(
        {
            "canonical_url": url,
            "url": url,
            "source_id": source_id,
            "title": f"Invented story {name}",
            "fetched_at": at,
            "published_at": at,
            "score": 0.0 if muted else score,
            "score_breakdown": breakdown,
            "extraction": {"status": status, "word_count": 900},
            "cluster_id": cluster_id,
        }
    )


def store_content(env: Env, a: Article) -> Article:
    ref = env.blobs.put(f"articles/{a.id}.html", b"<article><p>Invented text.</p></article>")
    return a.model_copy(update={"content_ref": ref})


def seed(env: Env, articles: Sequence[Article], *, content: bool = True) -> list[Article]:
    """Store ``articles`` (with their cleaned HTML unless ``content`` is False)."""
    saved = [store_content(env, a) if content else a for a in articles]
    with SqliteRepository(env.data / "newsfeed.db") as repo:
        for a in saved:
            repo.upsert_article(a)
    return saved


def configured_sources(env: Env) -> list[Source]:
    raw = yaml.safe_load((env.config / "sources.yaml").read_text())
    return [Source.model_validate(s) for s in raw["sources"]]


def publish() -> None:
    result = runner.invoke(cli.app, ["publish"])
    assert result.exit_code == 0, result.output


def read(env: Env, key: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(env.blobs.get(env.blobs.ref_for(key)))
    return data


def view(env: Env, name: str) -> dict[str, Any]:
    """``views/<name>.json`` as the latest publish wrote it, not a file left from before."""
    document = read(env, f"views/{name}.json")
    assert document["run_id"] == read(env, "feed.json")["run_id"], f"{name} not written this run"
    return document


def ids(document: dict[str, Any]) -> list[str]:
    return [item["id"] for item in document["items"]]


def by_id(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in document["items"]}


# --- Which articles each view holds -------------------------------------------------


def test_news_holds_news_sources_newest_first_and_longer_holds_long_form(env: Env) -> None:
    b_old, b_new, b_mid = seed(
        env,
        [
            article("bbc-news", "b-old", age_hours=5, score=9.0),
            article("bbc-news", "b-new", age_hours=1, score=1.0),
            article("bbc-news", "b-mid", age_hours=3, score=5.0),
        ],
    )
    a_high, a_low = seed(
        env,
        [
            article("aeon", "a-high", age_hours=2, score=2.0),
            article("aeon", "a-low", age_hours=10, score=0.5),
        ],
    )
    (tech,) = seed(env, [article("techblog", "t", age_hours=1, score=4.0)])

    publish()

    assert ids(view(env, "news")) == [b_new.id, b_mid.id, b_old.id]
    assert ids(view(env, "longer")) == [a_high.id, a_low.id]
    every = {b_old.id, b_new.id, b_mid.id, a_high.id, a_low.id, tech.id}
    assert set(ids(view(env, "feed"))) == every


def test_newest_breaks_ties_in_time_by_score_then_id(env: Env) -> None:
    same = now() - timedelta(hours=2)
    rows = [
        article("bbc-news", n, age_hours=2, score=s).model_copy(
            update={"fetched_at": same, "published_at": same}
        )
        for n, s in [("low", 1.0), ("high", 3.0), ("tie-1", 2.0), ("tie-2", 2.0)]
    ]
    low, high, tie_1, tie_2 = seed(env, rows)

    publish()

    assert ids(view(env, "news")) == [high.id, *sorted([tie_1.id, tie_2.id]), low.id]


def test_each_view_file_carries_the_run_id_and_generated_at_of_feed_json(env: Env) -> None:
    seed(env, [article("bbc-news", "b", age_hours=1), article("aeon", "a", age_hours=1)])

    publish()

    feed = read(env, "feed.json")
    for name in VIEW_NAMES:
        document = view(env, name)
        assert document["run_id"] == feed["run_id"], name
        assert document["generated_at"] == feed["generated_at"], name
        assert isinstance(document["items"], list), name


def test_feed_view_holds_all_300_candidates_in_score_order_and_feed_json_is_unchanged(
    env: Env,
) -> None:
    rows = [
        article(
            "techblog" if n % 2 else "bbc-news",
            f"s{n}",
            age_hours=1 + n / 10,
            score=float(n % 37) + n / 1000,
        )
        for n in range(300)
    ]
    saved = seed(env, rows, content=False)
    sources = [s for s in configured_sources(env) if s.enabled]

    publish()

    def expected(limit: int) -> list[dict[str, Any]]:
        built = build_feed(saved, sources, now=now(), run_id="x", limit=limit)
        items: list[dict[str, Any]] = built.feed["items"]
        return items

    feed_view = view(env, "feed")
    assert len(feed_view["items"]) == 300
    assert ids(feed_view) == [i["id"] for i in expected(10_000)]
    assert read(env, "feed.json")["items"] == expected(200)


def test_feed_view_holds_the_top_500_of_700_and_feed_json_the_top_200(env: Env) -> None:
    rows = [
        article("techblog" if n % 2 else "bbc-news", f"s{n}", age_hours=2, score=1000.0 - n)
        for n in range(700)
    ]
    saved = seed(env, rows, content=False)

    publish()

    by_score = [a.id for a in saved]  # scores fall with n, so this is score order
    assert ids(view(env, "feed")) == by_score[:500]
    assert ids(read(env, "feed.json")) == by_score[:200]


def test_news_holds_the_300_newest_of_400_including_ones_older_than_72_hours(env: Env) -> None:
    rows = [article("bbc-news", f"n{n}", age_hours=1 + n * 0.3, score=1.0) for n in range(400)]
    saved = seed(env, rows, content=False)

    publish()

    news = ids(view(env, "news"))
    assert news == [a.id for a in saved[:300]]  # newest first
    assert saved[299].fetched_at < now() - timedelta(hours=72)


def test_longer_holds_at_most_200_long_form_items_by_stored_score(env: Env) -> None:
    # 250 essays from 2 to 20 days old; the 200 newest are read, then ordered by score.
    rows = [
        article("aeon", f"e{n}", age_hours=48 + n * 1.74, score=float((n * 37) % 250) / 100)
        for n in range(250)
    ]
    saved = seed(env, rows, content=False)
    seed(env, [article("techblog", "t", age_hours=1, score=9.0)], content=False)

    publish()

    expected = sorted(saved[:200], key=lambda a: (-(a.score or 0.0), a.id))
    assert ids(view(env, "longer")) == [a.id for a in expected]


def test_an_older_long_form_article_with_a_higher_stored_score_ranks_above_a_newer_one(
    env: Env,
) -> None:
    old, new = seed(
        env,
        [
            article("aeon", "ten-days", age_hours=240, score=0.4),
            article("aeon", "two-days", age_hours=48, score=0.1),
        ],
    )

    publish()

    longer = view(env, "longer")
    assert ids(longer) == [old.id, new.id]
    assert by_id(longer)[old.id]["score"] == 0.4  # as stored, not recomputed


def test_the_previous_view_file_is_not_read_into_the_new_one(env: Env) -> None:
    gone = {"id": "f" * 16, "story_id": "x", "score": 9.0, "source_id": "aeon"}
    old = {"run_id": "prev-run", "generated_at": "2026-11-01T00:00:00Z", "items": [gone]}
    env.blobs.put("views/longer.json", json.dumps(old).encode(), "application/json")
    (fresh,) = seed(env, [article("aeon", "fresh", age_hours=1)])

    publish()

    assert ids(view(env, "longer")) == [fresh.id]


def test_every_view_validates_as_feed_json_items_after_a_real_run(env: Env) -> None:
    seed(
        env,
        [
            article("bbc-news", "b", age_hours=1),
            article("aeon", "a", age_hours=100),
            article("techblog", "t", age_hours=2),
        ],
    )

    result = runner.invoke(cli.app, ["run"])

    assert result.exit_code == 0, result.output
    schema = json.loads((REPO / "docs" / "feed.schema.json").read_text())
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    for name in VIEW_NAMES:
        document = view(env, name)
        validator.validate({"schema_version": 1, **document})
        assert document["items"], name


def test_muted_articles_and_disabled_or_zero_weight_sources_are_in_no_view(env: Env) -> None:
    (good,) = seed(env, [article("bbc-news", "good", age_hours=1)])
    left_out = seed(
        env,
        [
            article("bbc-news", "muted-news", age_hours=1, muted=True),
            article("aeon", "muted-essay", age_hours=1, muted=True),
            article("off-news", "disabled", age_hours=1, score=5.0),
            article("zero-news", "zero", age_hours=1, score=5.0),
            article("aeon", "unscored", age_hours=1, score=None),
        ],
    )

    publish()

    assert good.id in ids(view(env, "news"))
    for name in VIEW_NAMES:
        shown = set(ids(view(env, name)))
        for a in left_out:
            assert a.id not in shown, (name, a.source_id, a.url)


def test_views_come_from_the_views_config(env: Env) -> None:
    (env.config / "views.yaml").write_text(
        "views:\n  tech:\n    tags: [tech]\n    order: newest\n    max_items: 10\n"
    )
    (tech,) = seed(env, [article("techblog", "t", age_hours=1)])
    seed(env, [article("bbc-news", "b", age_hours=1)])

    publish()

    assert ids(view(env, "tech")) == [tech.id]
    assert not env.blobs.exists(env.blobs.ref_for("views/news.json"))


# --- One item per story cluster (#325) ------------------------------------------------


def test_a_cluster_is_one_item_with_coverage_in_feed_json_and_each_view(env: Env) -> None:
    # The best-scored row is the oldest, so the News view must place it by its own time.
    bbc, paper, tech = seed(
        env,
        [
            article("bbc-news", "story-b", age_hours=1, score=2.0, cluster_id="c1"),
            article("guardian-news", "story-g", age_hours=4, score=3.5, cluster_id="c1"),
            article("techblog", "story-t", age_hours=3, score=1.0, cluster_id="c1"),
        ],
    )
    (solo,) = seed(env, [article("bbc-news", "solo", age_hours=2, score=0.5)])

    publish()

    feed_items = by_id(read(env, "feed.json"))
    assert set(feed_items) == {paper.id, solo.id}
    assert feed_items[paper.id]["coverage"] == 3
    assert "coverage" not in feed_items[solo.id]
    assert by_id(view(env, "feed"))[paper.id]["coverage"] == 3
    assert not {bbc.id, tech.id} & set(ids(view(env, "feed")))
    # The News view holds only news sources, so it counts two sources, and the shown
    # row sits at its own (older) time, after the unclustered article.
    news = view(env, "news")
    assert ids(news) == [solo.id, paper.id]
    assert by_id(news)[paper.id]["coverage"] == 2
    schema = json.loads((REPO / "docs" / "feed.schema.json").read_text())
    Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(
        read(env, "feed.json")
    )
