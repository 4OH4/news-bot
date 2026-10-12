"""Tests for ``newsfeed.eval.urls``: the ``eval/urls.yaml`` schema and loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from newsfeed.config import ConfigError
from newsfeed.eval.urls import find_urls_file, load_eval_urls


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_full_and_minimal_entries_in_file_order(tmp_path: Path) -> None:
    path = write(
        tmp_path / "urls.yaml",
        """
urls:
  - id: example-2026-10-01-story
    url: https://news.example.com/2026/10/01/story
    source_id: example-news
    expected_title: "An invented headline"
    must_contain:
      - "an invented sentence"
    word_range: [400, 900]
    paywalled: false
    tags: [news]
    notes: ""
  - id: minimal
    url: http://blog.example.org/post
""",
    )

    full, minimal = load_eval_urls(path)

    assert full.id == "example-2026-10-01-story"
    assert full.source_id == "example-news"
    assert full.must_contain == ["an invented sentence"]
    assert full.word_range == (400, 900)
    assert full.tags == ["news"]
    assert minimal.url == "http://blog.example.org/post"
    assert minimal.source_id is None
    assert minimal.must_contain == []
    assert minimal.word_range is None
    assert minimal.paywalled is False


def test_empty_list_is_allowed(tmp_path: Path) -> None:
    assert load_eval_urls(write(tmp_path / "urls.yaml", "urls: []\n")) == []


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            "- id: a\n  url: https://a.example/x\n",
            "expected a mapping at the top level, got list. A top-level 'urls:' key is required",
        ),
        ("just text\n", "expected a mapping at the top level, got str"),
        ("urls:\n  - id: Bad Id\n    url: https://a.example/x\n", "urls[0] (id 'Bad Id').id"),
        ("urls:\n  - id: a\n    url: ftp://a.example/x\n", "http(s) URL"),
        ("urls:\n  - id: a\n    url: https:///no-host\n", "http(s) URL"),
        ("urls:\n  - id: a\n    url: https://a.example/x\n    titel: x\n", "titel"),
        ("urls:\n  - id: a\n    url: https://a.example/x\n    word_range: [9, 1]\n", "min"),
        (
            "urls:\n  - id: a\n    url: https://a.example/x\n  - id: a\n    url: https://b.example/\n",
            "duplicate entry ids: a",
        ),
        ("urls:\n  - id: a\n", "url"),
    ],
)
def test_invalid_files_raise_config_error(tmp_path: Path, text: str, message: str) -> None:
    path = write(tmp_path / "urls.yaml", text)

    with pytest.raises(ConfigError) as excinfo:
        load_eval_urls(path)

    assert message in str(excinfo.value)
    assert str(path) in str(excinfo.value)


def test_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_eval_urls(tmp_path / "urls.yaml")


def test_find_urls_file_searches_parents(tmp_path: Path) -> None:
    urls = write(tmp_path / "eval" / "urls.yaml", "urls: []\n")
    nested = tmp_path / "backend" / "src"
    nested.mkdir(parents=True)

    assert find_urls_file(nested) == urls.resolve()
    assert find_urls_file(tmp_path) == urls.resolve()


def test_find_urls_file_returns_none_when_absent(tmp_path: Path) -> None:
    assert find_urls_file(tmp_path) is None
