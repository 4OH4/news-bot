"""Tests for ``config/views.yaml`` and its loader (#288).

The loader follows the convention in ``newsfeed.config``: a ``load_<name>(path)``
function that raises ``ConfigError`` naming the file and the problem.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from newsfeed.config import ConfigError, load_sources

REPO = Path(__file__).parents[2]
VIEWS_PATH = REPO / "config" / "views.yaml"

VALID = """
views:
  news:
    tags: [news]
    order: newest
    max_items: 500
  feed:
    order: score
    max_items: 300
"""


def load_views(path: Path) -> object:
    # Imported here so the other config tests still run while the loader is missing.
    from newsfeed.config import load_views as loader

    return loader(path)


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "views.yaml"
    path.write_text(text)
    return path


def test_the_repo_views_config_loads() -> None:
    load_views(VIEWS_PATH)


def test_the_repo_views_config_defines_news_feed_and_longer() -> None:
    views = load_views(VIEWS_PATH).views  # type: ignore[attr-defined]

    shape = {n: (v.tags, v.order, v.max_items) for n, v in views.items()}
    assert shape == {
        "news": (("news",), "newest", 300),
        "feed": (None, "score", 500),
        "longer": (("long-form",), "score", 200),
    }


def test_a_leftover_window_hours_is_rejected_naming_the_key(tmp_path: Path) -> None:
    path = write(tmp_path, VALID + "    window_hours: 72\n")

    with pytest.raises(ConfigError) as caught:
        load_views(path)

    assert "window_hours" in str(caught.value)


@pytest.mark.parametrize("max_items", [0, -1, 1001, 5000])
def test_max_items_outside_1_to_1000_is_rejected(tmp_path: Path, max_items: int) -> None:
    path = write(tmp_path, f"views:\n  feed:\n    order: score\n    max_items: {max_items}\n")

    with pytest.raises(ConfigError) as caught:
        load_views(path)

    assert "max_items" in str(caught.value)


@pytest.mark.parametrize("max_items", [1, 1000])
def test_max_items_of_1_and_1000_are_accepted(tmp_path: Path, max_items: int) -> None:
    load_views(write(tmp_path, f"views:\n  feed:\n    order: score\n    max_items: {max_items}\n"))


def test_a_view_without_max_items_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_views(write(tmp_path, "views:\n  feed:\n    order: score\n"))


def test_a_valid_views_file_loads(tmp_path: Path) -> None:
    load_views(write(tmp_path, VALID))


def test_an_unknown_key_in_a_view_is_rejected_with_a_clear_error(tmp_path: Path) -> None:
    path = write(tmp_path, VALID + "    colour: blue\n")

    with pytest.raises(ConfigError) as caught:
        load_views(path)

    assert str(path) in str(caught.value)
    assert "colour" in str(caught.value)


def test_an_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, VALID + "default_view: feed\n")

    with pytest.raises(ConfigError) as caught:
        load_views(path)

    assert "default_view" in str(caught.value)


@pytest.mark.parametrize("name", ["Top", "top_stories", "top stories", "top/news"])
def test_a_view_name_outside_lowercase_letters_digits_and_dashes_is_rejected(
    tmp_path: Path, name: str
) -> None:
    path = write(tmp_path, VALID + f'  "{name}":\n    order: score\n    max_items: 10\n')

    with pytest.raises(ConfigError):
        load_views(path)


def test_a_dashed_view_name_is_accepted(tmp_path: Path) -> None:
    load_views(write(tmp_path, VALID + "  long-reads:\n    order: score\n    max_items: 200\n"))


def test_an_order_other_than_newest_or_score_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, "views:\n  feed:\n    order: oldest\n    max_items: 10\n")

    with pytest.raises(ConfigError) as caught:
        load_views(path)

    assert str(path) in str(caught.value)


def test_bbc_future_is_tagged_long_form() -> None:
    sources = {s.id: s for s in load_sources(REPO / "config" / "sources.yaml")}

    assert "long-form" in sources["bbc-future"].tags
