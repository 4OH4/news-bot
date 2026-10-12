"""Tests for ``newsfeed.config``: loading and validating ``config/sources.yaml``."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import Field

from newsfeed.config import ConfigError, ConfigFile, SourcesConfig, load_config, load_sources
from newsfeed.models import SourceType

REPO_SOURCES = Path(__file__).resolve().parents[2] / "config" / "sources.yaml"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "sources.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_repo_sources_file_is_valid() -> None:
    sources = load_sources(REPO_SOURCES)
    assert sources
    assert all(s.url for s in sources if s.type is SourceType.RSS)
    assert len({s.id for s in sources}) == len(sources)


def test_loads_sources_with_defaults_in_file_order(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
sources:
  - id: example-news
    type: rss
    url: https://news.example.com/feed.xml
    tags: [news]
  - id: example-off
    type: rss
    url: https://off.example.com/feed.xml
    weight: 0.5
    enabled: false
    options: {max_items: 10}
""",
    )
    first, second = load_sources(path)

    assert first.id == "example-news"
    assert first.type is SourceType.RSS
    assert first.weight == 1.0
    assert first.enabled is True
    assert first.tags == ["news"]
    assert first.options == {}
    # Disabled sources are returned too; the pipeline decides what to run.
    assert second.enabled is False
    assert second.weight == 0.5
    assert second.options == {"max_items": 10}


def test_empty_source_list_is_allowed(tmp_path: Path) -> None:
    assert load_sources(write(tmp_path, "sources: []\n")) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            """
sources:
  - {id: dup, type: rss, url: https://a.example.com/rss}
  - {id: other, type: rss, url: https://b.example.com/rss}
  - {id: dup, type: rss, url: https://c.example.com/rss}
""",
            "duplicate source ids: dup",
            id="duplicate-ids",
        ),
        pytest.param(
            "sources:\n  - {id: no-url, type: rss}\n",
            "sources[0] (id 'no-url'): source 'no-url': type 'rss' requires a url",
            id="rss-without-url",
        ),
        pytest.param(
            "sources:\n  - {id: weird, type: gopher, url: https://a.example.com/}\n",
            "sources[0] (id 'weird').type: Input should be 'rss', 'google_news', 'hn' or 'reddit'",
            id="unknown-type",
        ),
        pytest.param(
            "sources:\n  - {id: typo, type: rss, url: https://a.example.com/, enable: false}\n",
            "sources[0] (id 'typo').enable: Extra inputs are not permitted",
            id="unknown-field",
        ),
        pytest.param(
            "sources:\n  - {id: Bad Id, type: rss, url: https://a.example.com/}\n",
            "sources[0] (id 'Bad Id').id: String should match pattern",
            id="bad-id",
        ),
        pytest.param(
            "sources:\n  - {type: rss, url: https://a.example.com/}\n",
            "sources[0].id: Field required",
            id="missing-id",
        ),
        pytest.param(
            "sources:\n  - {id: neg, type: rss, url: https://a.example.com/, weight: -1}\n",
            "sources[0] (id 'neg').weight: Input should be greater than or equal to 0",
            id="negative-weight",
        ),
        pytest.param("feeds: []\n", "feeds: Extra inputs are not permitted", id="wrong-top-key"),
        pytest.param("", "sources: Field required", id="empty-file"),
        pytest.param("- id: a\n", "expected a mapping at the top level, got list", id="list"),
        pytest.param("sources: [\n", "invalid YAML", id="invalid-yaml"),
        pytest.param(
            """
sources:
  - id: twice
    type: rss
    url: https://a.example.com/rss
    url: https://b.example.com/rss
""",
            "found duplicate key 'url'",
            id="duplicate-key",
        ),
    ],
)
def test_invalid_config_is_rejected_with_a_clear_message(
    tmp_path: Path, text: str, expected: str
) -> None:
    path = write(tmp_path, text)
    with pytest.raises(ConfigError) as info:
        load_sources(path)
    message = str(info.value)
    assert message.startswith(str(path))
    assert expected in message


def test_every_validation_error_is_reported(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
sources:
  - {id: one, type: gopher, url: https://a.example.com/}
  - {id: two, type: rss}
""",
    )
    with pytest.raises(ConfigError) as info:
        load_sources(path)
    message = str(info.value)
    assert "2 validation errors" in message
    assert "sources[0] (id 'one').type" in message
    assert "sources[1] (id 'two')" in message


def test_merge_keys_are_supported(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
defaults: &news
  type: rss
  tags: [news]
sources:
  - <<: *news
    id: merged-one
    url: https://a.example.com/rss
  - <<: *news
    id: merged-two
    url: https://b.example.com/rss
    tags: [override]
""",
    )

    class WithDefaults(SourcesConfig):
        defaults: dict[str, object] = Field(default_factory=dict)

    first, second = load_config(path, WithDefaults).sources
    assert (first.id, first.type, first.tags) == ("merged-one", SourceType.RSS, ["news"])
    # An explicit key overrides the merged one without counting as a duplicate.
    assert (second.id, second.tags) == ("merged-two", ["override"])


def test_duplicate_keys_are_still_rejected_next_to_a_merge_key(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
defaults: &news {type: rss}
sources:
  - <<: *news
    id: twice
    url: https://a.example.com/rss
    url: https://b.example.com/rss
""",
    )
    with pytest.raises(ConfigError, match="found duplicate key 'url'"):
        load_sources(path)


def test_complex_key_is_a_config_error(tmp_path: Path) -> None:
    path = write(tmp_path, "? [a, b]\n: value\nsources: []\n")
    with pytest.raises(ConfigError, match="found unhashable key"):
        load_sources(path)


def test_missing_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="config file not found"):
        load_sources(tmp_path / "absent.yaml")


def test_load_config_accepts_an_empty_file_when_fields_have_defaults(tmp_path: Path) -> None:
    class Defaults(ConfigFile):
        domains: dict[str, list[str]] = Field(default_factory=dict)

    assert load_config(write(tmp_path, "# nothing yet\n"), Defaults) == Defaults()
