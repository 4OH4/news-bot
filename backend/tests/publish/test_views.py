"""Feed views (#288): publish writes ``views/<name>.json`` beside ``feed.json``.

These run ``newsfeed publish`` against the local SQLite repository and filesystem
blob store, with the repo's own ``config/views.yaml``, so they hold whichever
function inside the publish stage builds the views. The CLI has no clock option,
so "now" is the wall clock on both sides: every age below is hours or days away
from a window edge, so a slow run can't cross one.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
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
    published: bool = True,
    status: ExtractionStatus = ExtractionStatus.OK,
    muted: bool = False,
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
            "published_at": at if published else None,
            "score": 0.0 if muted else score,
            "score_breakdown": breakdown,
            "extraction": {"status": status, "word_count": 900},
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


def prior_view(env: Env, name: str, articles: Sequence[Article], run_id: str = "prev-run") -> None:
    """Write a previous ``views/<name>.json`` holding ``articles``, as an earlier run would."""
    every_source = [
        s.model_copy(update={"enabled": True, "weight": 1.0}) for s in configured_sources(env)
    ]
    items = build_feed(
        articles,
        every_source,
        now=now(),
        run_id=run_id,
        window_hours=24 * 60,
        limit=10_000,
    ).feed["items"]
    document = {"run_id": run_id, "generated_at": "2026-11-01T00:00:00Z", "items": items}
    env.blobs.put(f"views/{name}.json", json.dumps(document).encode(), "application/json")


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


def seeding_logged(records: list[logging.LogRecord]) -> bool:
    return any("seed" in r.getMessage().lower() for r in records)


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
        built = build_feed(
            saved, sources, now=now(), run_id="x", has_content=lambda _a: False, limit=limit
        )
        items: list[dict[str, Any]] = built.feed["items"]
        return items

    feed_view = view(env, "feed")
    assert len(feed_view["items"]) == 300
    assert ids(feed_view) == [i["id"] for i in expected(10_000)]
    assert read(env, "feed.json")["items"] == expected(200)


def test_a_long_form_article_ranked_600th_overall_is_in_longer(env: Env) -> None:
    rows = [
        article("techblog" if n % 2 else "bbc-news", f"s{n}", age_hours=2, score=1000.0 - n)
        for n in range(700)
        if n != 599
    ]
    seed(env, rows, content=False)
    (deep,) = seed(env, [article("aeon", "deep", age_hours=2, score=1000.0 - 599)])

    publish()

    assert deep.id in ids(view(env, "longer"))
    assert ids(view(env, "feed")).index(deep.id) == 599
    assert deep.id not in ids(read(env, "feed.json"))


def test_muted_articles_and_disabled_or_zero_weight_sources_are_in_no_view(env: Env) -> None:
    (good,) = seed(env, [article("bbc-news", "good", age_hours=1)])
    left_out = seed(
        env,
        [
            article("bbc-news", "muted-news", age_hours=1, muted=True),
            article("aeon", "muted-essay", age_hours=1, muted=True),
            article("off-news", "disabled", age_hours=1, score=5.0),
            article("zero-news", "zero", age_hours=1, score=5.0),
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
        "views:\n  tech:\n    tags: [tech]\n    order: newest\n    window_hours: 72\n"
    )
    (tech,) = seed(env, [article("techblog", "t", age_hours=1)])
    seed(env, [article("bbc-news", "b", age_hours=1)])

    publish()

    assert ids(view(env, "tech")) == [tech.id]
    assert not env.blobs.exists(env.blobs.ref_for("views/news.json"))


# --- Longer's 14-day window: carried forward from the previous file ---------------


def test_a_carried_long_form_article_from_5_days_ago_stays_with_its_score(env: Env) -> None:
    kept = store_content(env, article("aeon", "five-days", age_hours=5 * 24, score=0.37))
    prior_view(env, "longer", [kept])

    publish()

    item = by_id(view(env, "longer")).get(kept.id)
    assert item is not None, "the carried article was dropped"
    assert item["score"] == 0.37


def test_a_carried_link_item_needs_no_stored_content(env: Env) -> None:
    link = article(
        "aeon", "paywalled", age_hours=5 * 24, score=0.8, status=ExtractionStatus.PAYWALLED
    )
    prior_view(env, "longer", [link])

    publish()

    assert link.id in ids(view(env, "longer"))


@pytest.mark.parametrize(
    "case",
    [
        "published 15 days ago",
        "source no longer tagged long-form",
        "article HTML expired",
        "no published_at",
        "source disabled",
        "source weight zero",
    ],
)
def test_a_carried_item_that_is_no_longer_eligible_is_dropped(env: Env, case: str) -> None:
    keeper = store_content(env, article("aeon", "keeper", age_hours=5 * 24))
    stale = {
        "published 15 days ago": lambda: store_content(
            env, article("aeon", "old", age_hours=15 * 24)
        ),
        "source no longer tagged long-form": lambda: store_content(
            env, article("former-long", "untagged", age_hours=5 * 24)
        ),
        "article HTML expired": lambda: article("aeon", "expired", age_hours=6 * 24),
        "no published_at": lambda: store_content(
            env, article("aeon", "undated", age_hours=4 * 24, published=False)
        ),
        "source disabled": lambda: store_content(
            env, article("off-news", "disabled", age_hours=4 * 24)
        ),
        "source weight zero": lambda: store_content(
            env, article("zero-news", "zero", age_hours=4 * 24)
        ),
    }[case]()
    prior_view(env, "longer", [keeper, stale])
    assert stale.id in ids(read(env, "views/longer.json")), "setup: the old file holds it"

    publish()

    longer = ids(view(env, "longer"))
    assert keeper.id in longer
    assert stale.id not in longer


def test_a_carried_item_dated_in_the_future_is_dropped(env: Env) -> None:
    keeper = store_content(env, article("aeon", "keeper", age_hours=5 * 24))
    future = store_content(env, article("aeon", "tomorrow", age_hours=-24))
    prior_view(env, "longer", [keeper, future])
    assert future.id in ids(read(env, "views/longer.json")), "setup: the old file holds it"

    publish()

    longer = ids(view(env, "longer"))
    assert keeper.id in longer
    assert future.id not in longer


def test_an_article_read_this_run_and_now_muted_is_not_brought_back_from_the_old_file(
    env: Env,
) -> None:
    live = store_content(env, article("aeon", "muted-since", age_hours=48))
    prior_view(env, "longer", [live])
    seed(env, [live.model_copy(update={"score": 0.0, "score_breakdown": {"muted": 1.0}})])

    publish()

    assert live.id not in ids(view(env, "longer"))


def test_a_fresh_row_replaces_the_carried_row_for_the_same_story(env: Env) -> None:
    old = article("aeon", "again", age_hours=48, score=0.2)
    prior_view(env, "longer", [store_content(env, old)])
    (fresh,) = seed(env, [old.model_copy(update={"score": 1.5})])

    publish()

    rows = [i for i in view(env, "longer")["items"] if i["story_id"] == fresh.id]
    assert len(rows) == 1
    assert rows[0]["score"] == 1.5


def test_without_a_previous_longer_file_publish_seeds_14_days_and_logs_it(
    env: Env, log_records: list[logging.LogRecord]
) -> None:
    recent, ten_days = seed(
        env,
        [
            article("aeon", "recent", age_hours=1),
            article("aeon", "ten-days", age_hours=10 * 24),
        ],
    )
    (too_old,) = seed(env, [article("aeon", "fifteen-days", age_hours=15 * 24)])

    publish()

    longer = ids(view(env, "longer"))
    assert recent.id in longer
    assert ten_days.id in longer
    assert too_old.id not in longer
    assert seeding_logged(log_records)


def test_an_unparseable_previous_longer_file_is_seeded_again(
    env: Env, log_records: list[logging.LogRecord]
) -> None:
    env.blobs.put("views/longer.json", b"{not json", "application/json")
    (ten_days,) = seed(env, [article("aeon", "ten-days", age_hours=10 * 24)])

    publish()

    assert ten_days.id in ids(view(env, "longer"))
    assert seeding_logged(log_records)


def test_a_later_run_carries_longer_forward_without_seeding_or_reading_14_days(
    env: Env, log_records: list[logging.LogRecord]
) -> None:
    (ten_days,) = seed(env, [article("aeon", "ten-days", age_hours=10 * 24)])
    publish()
    assert seeding_logged(log_records)
    log_records.clear()
    # Stored after the seeding run and older than the 72-hour read: only a
    # 14-day repository read could find it.
    (unread,) = seed(env, [article("aeon", "nine-days", age_hours=9 * 24)])

    publish()

    longer = ids(view(env, "longer"))
    assert ten_days.id in longer
    assert unread.id not in longer
    assert not seeding_logged(log_records)


def test_news_and_feed_are_not_carried_forward(env: Env) -> None:
    # In the 72-hour window, but no longer in the repository: only carrying
    # the old file forward could bring it back.
    gone = store_content(env, article("bbc-news", "gone", age_hours=48))
    prior_view(env, "news", [gone])
    prior_view(env, "feed", [gone])
    (fresh,) = seed(env, [article("bbc-news", "fresh", age_hours=1)])

    publish()

    assert ids(view(env, "news")) == [fresh.id]
    assert ids(view(env, "feed")) == [fresh.id]
