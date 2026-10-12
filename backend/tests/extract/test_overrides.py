"""Tests for loading ``config/extraction.yaml`` and matching domain overrides."""

from __future__ import annotations

from pathlib import Path

import pytest

from newsfeed.config import ConfigError
from newsfeed.extract.overrides import (
    EXTRACTOR_NAMES,
    DomainOverride,
    ExtractionConfig,
    Mode,
    Renderer,
    load_extraction_config,
    normalise_domain,
    url_domain,
)

REPO_CONFIG = Path(__file__).resolve().parents[3] / "config" / "extraction.yaml"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "extraction.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_repo_config_is_valid() -> None:
    config = load_extraction_config(REPO_CONFIG)
    for override in config.domains.values():
        assert set(override.order or ()) <= EXTRACTOR_NAMES


def test_loads_every_kind_of_override(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
domains:
  www.Example.com:
    order: [readability, trafilatura]
    min_words: 80
  app.example.net:
    render: playwright
  paywalled.example.org:
    mode: summary_only
""",
    )
    config = load_extraction_config(path)
    assert config.domains == {
        "example.com": DomainOverride(order=["readability", "trafilatura"], min_words=80),
        "app.example.net": DomainOverride(render=Renderer.PLAYWRIGHT),
        "paywalled.example.org": DomainOverride(mode=Mode.SUMMARY_ONLY),
    }
    assert config.domains["example.com"].mode is Mode.EXTRACT


@pytest.mark.parametrize("text", ["", "domains: {}\n", "# only comments\n"])
def test_empty_config_means_no_overrides(tmp_path: Path, text: str) -> None:
    config = load_extraction_config(write(tmp_path, text))
    assert config.domains == {}
    assert config.override_for("https://example.com/a") is None


def test_missing_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_extraction_config(tmp_path / "missing.yaml")


@pytest.mark.parametrize(
    ("yaml_text", "message"),
    [
        (
            "domains:\n  example.com:\n    order: [trafilatora]\n",
            "unknown extractor(s) trafilatora",
        ),
        ("domains:\n  example.com:\n    order: []\n", "at least 1 item"),
        (
            "domains:\n  example.com:\n    order: [readability, readability]\n",
            "listed more than once: readability",
        ),
        ("domains:\n  example.com:\n    render: puppeteer\n", "'playwright'"),
        ("domains:\n  example.com:\n    mode: full\n", "'extract' or 'summary_only'"),
        ("domains:\n  example.com:\n    min_words: 0\n", "greater than or equal to 1"),
        ("domains:\n  example.com:\n    orde: [readability]\n", "Extra inputs are not permitted"),
        (
            "domains:\n  example.com:\n    mode: summary_only\n    min_words: 50\n",
            "summary_only skips extraction, so min_words has no effect",
        ),
        (
            "domains:\n  example.com:\n    mode: summary_only\n    render: playwright\n"
            "    order: [trafilatura]\n",
            "so order, render has no effect",
        ),
        ("domains:\n  https://example.com/news:\n    min_words: 50\n", "is not a domain name"),
        ("domains:\n  example.com:8080:\n    min_words: 50\n", "is not a domain name"),
        ("domains:\n  com:\n    min_words: 50\n", "is not a domain name"),
        ("domains:\n  -bad.example.com:\n    min_words: 50\n", "is not a domain name"),
        ("domains:\n  1: {}\n", "is not a string"),
        (
            "domains:\n  example.com:\n    min_words: 50\n  www.example.com:\n    min_words: 60\n",
            "'www.example.com' duplicates domain 'example.com'",
        ),
        ("domains: [example.com]\n", "valid dictionary"),
        ("domain:\n  example.com: {}\n", "Extra inputs are not permitted"),
    ],
)
def test_invalid_config_is_rejected_with_its_location(
    tmp_path: Path, yaml_text: str, message: str
) -> None:
    path = write(tmp_path, yaml_text)
    with pytest.raises(ConfigError) as excinfo:
        load_extraction_config(path)
    assert str(path) in str(excinfo.value)
    assert message in str(excinfo.value)


def test_duplicate_yaml_keys_are_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, "domains:\n  example.com: {}\n  example.com: {}\n")
    with pytest.raises(ConfigError, match=r"duplicate key 'example\.com'"):
        load_extraction_config(path)


def test_error_location_names_the_domain(tmp_path: Path) -> None:
    path = write(tmp_path, "domains:\n  news.example.com:\n    min_words: -5\n")
    with pytest.raises(ConfigError, match=r"domains\.news\.example\.com\.min_words"):
        load_extraction_config(path)


# --- Matching ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("example.com", "example.com"),
        ("WWW.Example.COM", "example.com"),
        (" www.example.com. ", "example.com"),
        ("www.news.example.com", "news.example.com"),
        ("wwwexample.com", "wwwexample.com"),
    ],
)
def test_normalise_domain(raw: str, expected: str) -> None:
    assert normalise_domain(raw) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.example.com/a?b=c", "example.com"),
        ("http://News.Example.com:8080/a", "news.example.com"),
        ("https://example.com./a", "example.com"),
        ("/relative/path", None),
        ("", None),
        ("https://[::1/broken", None),
    ],
)
def test_url_domain(url: str, expected: str | None) -> None:
    assert url_domain(url) == expected


def test_override_for_matches_subdomains_and_prefers_the_longest_key() -> None:
    parent = DomainOverride(min_words=100)
    child = DomainOverride(min_words=200)
    config = ExtractionConfig.model_validate(
        {"domains": {"example.com": parent, "blog.example.com": child}}
    )
    assert config.override_for("https://example.com/") == parent
    assert config.override_for("https://www.example.com/") == parent
    assert config.override_for("https://shop.example.com/") == parent
    assert config.override_for("https://blog.example.com/") == child
    assert config.override_for("https://a.b.blog.example.com/") == child
    assert config.override_for("https://notexample.com/") is None
    assert config.override_for("https://example.com.attacker.test/") is None
    assert config.override_for("https://example.org/") is None


def test_overrides_are_immutable() -> None:
    override = DomainOverride(min_words=100)
    with pytest.raises(ValueError, match="frozen"):
        override.min_words = 5


def test_null_values_mean_the_defaults(tmp_path: Path) -> None:
    path = write(tmp_path, "domains:\n  example.com:\n    order: null\n    min_words: null\n")
    assert load_extraction_config(path).domains == {"example.com": DomainOverride()}
