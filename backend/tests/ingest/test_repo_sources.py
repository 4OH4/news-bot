"""The ingest-time settings of every source in ``config/sources.yaml`` are valid.

``load_sources`` validates the common schema; each ingester validates its own
``options`` when it runs. This catches a bad option in the repo's config in CI
instead of on the first pipeline run.
"""

from __future__ import annotations

from pathlib import Path

from newsfeed.config import load_sources
from newsfeed.ingest.google_news import GoogleNewsIngester
from newsfeed.ingest.hn import HackerNewsOptions
from newsfeed.models import SourceType

REPO_SOURCES = Path(__file__).resolve().parents[3] / "config" / "sources.yaml"


def test_discovery_sources_have_valid_settings() -> None:
    sources = load_sources(REPO_SOURCES)
    kinds = {s.type for s in sources}
    # The file carries an example of each discovery source type.
    assert {SourceType.GOOGLE_NEWS, SourceType.HN} <= kinds

    for source in sources:
        if source.type is SourceType.GOOGLE_NEWS:
            assert GoogleNewsIngester().feed_url(source).startswith("https://news.google.com/rss")
        elif source.type is SourceType.HN:
            HackerNewsOptions.model_validate(source.options)
