"""Similarity reaches the published views through the real pipeline (#323, #326)."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from newsfeed.config import UrlParamsConfig, ViewConfig, ViewsConfig
from newsfeed.models import Source, SourceType
from newsfeed.pipeline import run_pipeline
from newsfeed.wiring import Services
from tests.fakes import FakeExtractor, FakeFetcher, InMemoryBlobStore, InMemoryRepository
from tests.rank.support import NOW
from tests.rank.test_stage_profile import ScriptedEmbedder, article, preseeded_profile

SOURCES = [Source(id="gazette", type=SourceType.RSS, url="https://news.example.com/")]
VIEWS = ViewsConfig(views={"feed": ViewConfig(order="score", max_items=500)})


@pytest.mark.parametrize("path", ["feed.json", "views/feed.json"])
def test_similarity_is_in_the_published_score_breakdown(path: str) -> None:
    repo, blobs = InMemoryRepository(), InMemoryBlobStore()
    repo.save_profile(
        preseeded_profile(built_ago=timedelta(hours=1), feedback_ago=timedelta(hours=2))
    )
    for group in ("alpha", "beta"):
        repo.upsert_article(article(group, "w"))
    services = Services(
        repository=repo,
        blobs=blobs,
        fetcher=FakeFetcher(),
        ingesters={},
        extractor=FakeExtractor(),
        url_rules=UrlParamsConfig(),
        views=VIEWS,
        embedder=ScriptedEmbedder(),
    )

    run_pipeline(services, SOURCES, ingest=False, extract=False, clock=lambda: NOW)

    items = json.loads(blobs.get(blobs.ref_for(path)))["items"]
    factors = {item["title"]: item["score_breakdown"]["similarity"] for item in items}
    assert set(factors) == {"Story alpha w", "Story beta w"}
    assert factors["Story alpha w"] > 1.0
    assert factors["Story beta w"] == 1.0
    assert all("similarity_raw" in item["score_breakdown"] for item in items)
