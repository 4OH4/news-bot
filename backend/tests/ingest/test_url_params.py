"""Tests for per-domain URL parameter rules (#35): the config loader and ``normalise_url``.

All domains and URLs are invented.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from newsfeed.config import ConfigError, DomainParamRules, UrlParamsConfig, load_url_params
from newsfeed.ingest.canonical import canonical_from_html, normalise_url

REPO_URL_PARAMS = Path(__file__).resolve().parents[3] / "config" / "url_params.yaml"

RULES = UrlParamsConfig(
    domains={
        "news.example": DomainParamRules(strip=frozenset({"smid"})),
        "broadcast.example": DomainParamRules(strip_prefixes=("at_",), keep=frozenset({"at_id"})),
        "dev.example": DomainParamRules(keep=frozenset({"ref", "utm_page"})),
        "www.layered.example": DomainParamRules(strip=frozenset({"src"})),
        "layered.example": DomainParamRules(strip=frozenset({"cmp"}), keep=frozenset({"src"})),
    }
)

# (input, expected) pairs for normalise_url with RULES.
RULE_CASES: list[tuple[str, str]] = [
    # strip, for the domain and any subdomain, case-insensitively
    ("https://news.example/a?smid=tw&id=1", "https://news.example/a?id=1"),
    ("https://www.news.example/a?SMID=tw", "https://www.news.example/a"),
    ("https://edition.www.news.example/a?smid=1", "https://edition.www.news.example/a"),
    # Global tracking parameters are still stripped alongside the rule
    ("https://news.example/a?smid=1&utm_source=x&fbclid=y", "https://news.example/a"),
    # Rules match whole labels, and don't leak to other domains
    ("https://badnews.example/a?smid=1", "https://badnews.example/a?smid=1"),
    ("https://news.example.org/a?smid=1", "https://news.example.org/a?smid=1"),
    ("https://other.example/a?smid=1", "https://other.example/a?smid=1"),
    # strip_prefixes, with keep overriding a matching prefix
    (
        "https://www.broadcast.example/a?at_medium=rss&at_campaign=x&page=2",
        "https://www.broadcast.example/a?page=2",
    ),
    ("https://broadcast.example/a?at_id=7&at_medium=rss", "https://broadcast.example/a?at_id=7"),
    # keep overrides the global list (exact name and prefix)
    ("https://dev.example/a?ref=main&fbclid=x", "https://dev.example/a?ref=main"),
    ("https://www.dev.example/a?REF=main", "https://www.dev.example/a?REF=main"),
    (
        "https://dev.example/a?utm_page=2&utm_source=x",
        "https://dev.example/a?utm_page=2",
    ),
    # Without a keep rule, ref is still a global tracker
    ("https://news.example/a?ref=main", "https://news.example/a"),
    # Rules for a host and its parent combine; keep wins across them
    (
        "https://www.layered.example/a?src=feed&cmp=x&id=3",
        "https://www.layered.example/a?id=3&src=feed",
    ),
    ("https://layered.example/a?src=feed&cmp=x", "https://layered.example/a?src=feed"),
    # Ports, case and userinfo don't stop the host matching
    ("HTTPS://News.Example:443/a?smid=1", "https://news.example/a"),
    ("https://user@news.example:8443/a?smid=1", "https://user@news.example:8443/a"),
    # Rules apply to the publisher URL behind an AMP cache, not the cache host
    (
        "https://news-example.cdn.ampproject.org/c/s/news.example/a/amp?smid=1",
        "https://news.example/a",
    ),
    # Hosts with no applicable rule: IP addresses, single labels
    ("http://192.0.2.1/a?smid=1", "http://192.0.2.1/a?smid=1"),
]


@pytest.mark.parametrize(("url", "expected"), RULE_CASES)
def test_normalise_url_applies_domain_rules(url: str, expected: str) -> None:
    assert normalise_url(url, rules=RULES) == expected


@pytest.mark.parametrize("url", [u for u, _ in RULE_CASES] + [e for _, e in RULE_CASES])
def test_normalise_url_is_idempotent_with_rules(url: str) -> None:
    once = normalise_url(url, rules=RULES)
    assert normalise_url(once, rules=RULES) == once


@pytest.mark.parametrize("rules", [None, UrlParamsConfig()])
@pytest.mark.parametrize("url", [u for u, _ in RULE_CASES])
def test_no_rules_is_the_default_behaviour(url: str, rules: UrlParamsConfig | None) -> None:
    assert normalise_url(url, rules=rules) == normalise_url(url)


def test_keep_does_not_affect_amp_flags() -> None:
    rules = UrlParamsConfig(domains={"dev.example": DomainParamRules(keep=frozenset({"amp"}))})
    assert normalise_url("https://dev.example/a?amp=1", rules=rules) == "https://dev.example/a"


def test_canonical_from_html_applies_rules() -> None:
    html = '<html><head><link rel="canonical" href="/a?smid=nl&id=1"></head></html>'
    found = canonical_from_html(html, "https://www.news.example/x", rules=RULES)
    assert found == "https://www.news.example/a?id=1"
    assert canonical_from_html(html, "https://www.news.example/x") == (
        "https://www.news.example/a?id=1&smid=nl"
    )


@pytest.fixture
def records() -> Iterator[list[logging.LogRecord]]:
    """Capture records from the ``newsfeed`` logger, which doesn't propagate to root."""
    captured: list[logging.LogRecord] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    logger = logging.getLogger("newsfeed")
    handler = _Handler(level=logging.DEBUG)
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield captured
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


def test_stripped_parameters_are_logged_at_debug(records: list[logging.LogRecord]) -> None:
    url = "https://news.example/a?smid=1&utm_source=x&id=2"
    normalise_url(url, rules=RULES)

    [record] = [r for r in records if r.getMessage() == "stripped query parameters"]
    assert record.levelno == logging.DEBUG
    assert record.__dict__["url"] == url
    assert record.__dict__["stage"] == "ingest"
    assert record.__dict__["params"] == ["smid", "utm_source"]


def test_nothing_is_logged_when_nothing_is_stripped(records: list[logging.LogRecord]) -> None:
    normalise_url("https://dev.example/a?ref=main&id=2", rules=RULES)
    assert not [r for r in records if r.getMessage() == "stripped query parameters"]


# --- load_url_params ----------------------------------------------------------


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "url_params.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_repo_url_params_file_is_valid_and_has_no_guessed_rules() -> None:
    # Rules may only be added on evidence from real runs (#35); update this
    # test alongside the first evidence-backed rule.
    assert load_url_params(REPO_URL_PARAMS) == UrlParamsConfig()


def test_loads_rules_and_lowercases_names(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
domains:
  news.example:
    strip: [SMID]
  broadcast.example:
    strip_prefixes: [At_]
    keep: [at_id]
  dev.example:
    keep: [ref]
  empty.example: {}
""",
    )
    config = load_url_params(path)

    assert config.domains["news.example"] == DomainParamRules(strip=frozenset({"smid"}))
    assert config.domains["broadcast.example"].strip_prefixes == ("at_",)
    assert config.domains["broadcast.example"].keep == {"at_id"}
    assert config.domains["dev.example"].keep == {"ref"}
    assert config.domains["empty.example"] == DomainParamRules()
    assert normalise_url("https://www.news.example/a?smid=1", rules=config) == (
        "https://www.news.example/a"
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("", None, id="empty-file"),
        pytest.param("domains:\n", "domains: Input should be a valid dictionary", id="null"),
        pytest.param(
            "domains:\n  news.example:\n",
            "domains.news.example: Input should be a valid dictionary",
            id="null-rule",
        ),
        pytest.param(
            "domains:\n  news.example: {strips: [smid]}\n",
            "domains.news.example.strips: Extra inputs are not permitted",
            id="unknown-key",
        ),
        pytest.param("rules: {}\n", "rules: Extra inputs are not permitted", id="wrong-top-key"),
        pytest.param(
            "domains:\n  news.example: {strip: smid}\n",
            "domains.news.example.strip: Input should be a valid",
            id="not-a-list",
        ),
        pytest.param(
            "domains:\n  news.example: {strip: [1]}\n",
            "domains.news.example.strip",
            id="not-a-string",
        ),
        pytest.param(
            "domains:\n  news.example: {strip_prefixes: ['']}\n",
            "invalid strip_prefixes entries ''",
            id="empty-prefix",
        ),
        pytest.param(
            "domains:\n  news.example: {strip: ['a b', 'x=1']}\n",
            "invalid strip entries 'a b', 'x=1'",
            id="bad-name",
        ),
        pytest.param(
            "domains:\n  news.example: {strip: [smid, Ref], keep: [ref]}\n",
            "domains.news.example: listed in both strip and keep: ref",
            id="strip-and-keep",
        ),
        pytest.param(
            "domains:\n  News.Example: {strip: [smid]}\n",
            "invalid domain 'News.Example'",
            id="uppercase-domain",
        ),
        pytest.param(
            "domains:\n  https://news.example/: {}\n  '*.news.example': {}\n  com: {}\n",
            "invalid domain 'https://news.example/', '*.news.example', 'com'",
            id="not-hostnames",
        ),
        pytest.param(
            "domains:\n  news.example: {}\n  news.example: {}\n",
            "found duplicate key 'news.example'",
            id="duplicate-domain",
        ),
    ],
)
def test_invalid_url_params_fail_loudly(tmp_path: Path, text: str, expected: str | None) -> None:
    path = write(tmp_path, text)
    if expected is None:  # an empty file means "no rules"
        assert load_url_params(path) == UrlParamsConfig()
        return
    with pytest.raises(ConfigError) as info:
        load_url_params(path)
    message = str(info.value)
    assert message.startswith(str(path))
    assert expected in message


def test_missing_url_params_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="config file not found"):
        load_url_params(tmp_path / "absent.yaml")
