"""Runtime settings, read from ``NEWSFEED_*`` environment variables (and ``.env``).

Settings say where things live and how hard to push; what to ingest and how to
rank it is configuration in ``config/`` (CLAUDE.md, rule 5).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from newsfeed.eval.snapshot import default_fixtures_dir
from newsfeed.fetching import DEFAULT_USER_AGENT
from newsfeed.fetching.http_fetcher import DEFAULT_MIN_INTERVAL

__all__ = ["Settings", "load_settings"]


class Settings(BaseSettings):
    """Pipeline settings. Each field ``x`` is read from ``NEWSFEED_X``."""

    model_config = SettingsConfigDict(
        env_prefix="NEWSFEED_", env_file=".env", extra="ignore", frozen=True
    )

    data_dir: Path = Field(
        default=Path("data"), description="Where the SQLite database and blobs live."
    )
    config_dir: Path = Field(default=Path("config"), description="Holds sources.yaml and friends.")
    fixtures_dir: Path = Field(
        default_factory=default_fixtures_dir,
        description="Evaluation snapshots, outside the repo (see eval/snapshot.py).",
    )
    storage: Literal["local"] = Field(
        default="local", description="Storage backend; only 'local' exists until Phase 3."
    )
    user_agent: str = Field(default=DEFAULT_USER_AGENT, min_length=1)
    concurrency: int = Field(default=8, ge=1, description="Worker threads in the extract stage.")
    dedupe_window_days: int = Field(
        default=14,
        ge=1,
        description="Ingest dedupes against articles fetched this many days back (bounds reads).",
    )
    host_interval: float = Field(
        default=DEFAULT_MIN_INTERVAL,
        ge=0,
        description="Minimum seconds between requests to one host.",
    )

    @property
    def database_path(self) -> Path:
        """The SQLite database file."""
        return self.data_dir / "newsfeed.db"

    @property
    def blob_dir(self) -> Path:
        """Root of the local blob store."""
        return self.data_dir / "blobs"

    @property
    def sources_path(self) -> Path:
        """``sources.yaml`` in the config directory."""
        return self.config_dir / "sources.yaml"

    @property
    def url_params_path(self) -> Path:
        """``url_params.yaml`` in the config directory."""
        return self.config_dir / "url_params.yaml"

    @property
    def extraction_path(self) -> Path:
        """``extraction.yaml`` in the config directory."""
        return self.config_dir / "extraction.yaml"

    @property
    def interests_path(self) -> Path:
        """``interests.yaml`` in the config directory."""
        return self.config_dir / "interests.yaml"


def load_settings() -> Settings:
    """Read the settings from the environment."""
    return Settings()
