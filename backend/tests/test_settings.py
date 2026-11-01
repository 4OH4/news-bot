"""Tests for ``Settings``."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from newsfeed.settings import Settings, load_settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DATA_DIR", "CONFIG_DIR", "FIXTURES_DIR", "CONCURRENCY", "USER_AGENT"):
        monkeypatch.delenv(f"NEWSFEED_{name}", raising=False)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.data_dir == Path("data")
    assert settings.config_dir == Path("config")
    assert settings.storage == "local"
    assert settings.concurrency == 8
    assert settings.fixtures_dir.name == "eval-fixtures"
    assert "github.com" in settings.user_agent


def test_environment_overrides_and_derived_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("NEWSFEED_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("NEWSFEED_CONFIG_DIR", str(tmp_path / "c"))
    monkeypatch.setenv("NEWSFEED_CONCURRENCY", "3")
    monkeypatch.setenv("NEWSFEED_HOST_INTERVAL", "0.5")

    settings = load_settings()

    assert settings.database_path == tmp_path / "d" / "newsfeed.db"
    assert settings.blob_dir == tmp_path / "d" / "blobs"
    assert settings.sources_path == tmp_path / "c" / "sources.yaml"
    assert settings.url_params_path == tmp_path / "c" / "url_params.yaml"
    assert settings.extraction_path == tmp_path / "c" / "extraction.yaml"
    assert settings.interests_path == tmp_path / "c" / "interests.yaml"
    assert (settings.concurrency, settings.host_interval) == (3, 0.5)


def test_invalid_values_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEWSFEED_CONCURRENCY", "0")
    with pytest.raises(ValidationError):
        load_settings()
    monkeypatch.delenv("NEWSFEED_CONCURRENCY")
    monkeypatch.setenv("NEWSFEED_STORAGE", "gcs")
    with pytest.raises(ValidationError):
        load_settings()


def test_dedupe_window_days(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEWSFEED_DEDUPE_WINDOW_DAYS", raising=False)
    assert Settings(_env_file=None).dedupe_window_days == 14  # type: ignore[call-arg]
    monkeypatch.setenv("NEWSFEED_DEDUPE_WINDOW_DAYS", "30")
    assert load_settings().dedupe_window_days == 30
    monkeypatch.setenv("NEWSFEED_DEDUPE_WINDOW_DAYS", "0")
    with pytest.raises(ValidationError):
        load_settings()


def test_retry_backoff_hours_accepts_positive_numbers_and_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEWSFEED_RETRY_BACKOFF_HOURS", "[0.5, 6, 24]")
    assert load_settings().retry_backoff_hours == (0.5, 6, 24)
    monkeypatch.setenv("NEWSFEED_RETRY_BACKOFF_HOURS", "[]")
    assert load_settings().retry_backoff_hours == ()


@pytest.mark.parametrize("value", ["[0]", "[-1]", "[1, -6]", "[1, 0, 24]"])
def test_retry_backoff_hours_rejects_non_positive_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("NEWSFEED_RETRY_BACKOFF_HOURS", value)

    with pytest.raises(ValidationError, match="positive"):
        load_settings()


@pytest.mark.parametrize("value", ["[NaN]", "[Infinity]", "[-Infinity]", "[1e300]", "[1, NaN]"])
def test_retry_backoff_hours_rejects_non_finite_and_overflowing_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("NEWSFEED_RETRY_BACKOFF_HOURS", value)

    with pytest.raises(ValidationError, match="positive"):
        load_settings()


@pytest.mark.parametrize("value", ["Infinity", "-Infinity", "NaN", "1e300", "-1"])
def test_retry_window_days_rejects_non_finite_and_absurd_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("NEWSFEED_RETRY_WINDOW_DAYS", value)

    with pytest.raises(ValidationError):
        load_settings()


def test_retry_window_days_accepts_a_normal_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEWSFEED_RETRY_WINDOW_DAYS", "7.5")

    assert load_settings().retry_window_days == 7.5
