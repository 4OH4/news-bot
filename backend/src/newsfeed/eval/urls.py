"""Schema and loader for ``eval/urls.yaml``, the human-curated evaluation set (#16).

The file is a mapping with one key, ``urls``, holding a list of entries::

    urls:
      - id: bbc-2026-10-01-energy-prices
        url: https://www.bbc.co.uk/news/articles/...
        source_id: bbc-news
        expected_title: "Energy prices to rise in January"
        must_contain:
          - "a distinctive sentence fragment from the body"
        word_range: [400, 900]
        paywalled: false
        tags: [news]
        notes: ""

Only ``id`` and ``url`` are needed to take a snapshot; the other fields are the
expectations the scorer (#18) checks. It is loaded with ``config.load_config``,
so unknown keys, duplicate keys and bad values fail loudly with their location.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from newsfeed.config import ConfigError, ConfigFile, load_config

__all__ = ["EvalEntry", "EvalUrlsConfig", "find_urls_file", "load_eval_urls"]

URLS_FILE = Path("eval") / "urls.yaml"
"""Location of the evaluation set relative to the repo root."""


class EvalEntry(BaseModel):
    """One page in the evaluation set and what a clean extraction looks like."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(
        pattern=r"^[a-z0-9][a-z0-9_-]*$",
        description="Stable slug and snapshot file name: lowercase letters, digits, '-', '_'.",
    )
    url: str = Field(description="Article URL; http or https with a host.")
    source_id: str | None = None
    expected_title: str | None = None
    must_contain: list[str] = Field(default_factory=list)
    word_range: tuple[int, int] | None = None
    paywalled: bool = False
    tags: list[str] = Field(default_factory=list)
    notes: str = ""

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("must be an http(s) URL with a host")
        return value

    @field_validator("word_range")
    @classmethod
    def _ordered_range(cls, value: tuple[int, int] | None) -> tuple[int, int] | None:
        if value is not None and not 0 <= value[0] <= value[1]:
            raise ValueError("must be [min, max] with 0 <= min <= max")
        return value


class EvalUrlsConfig(ConfigFile):
    """Schema of ``eval/urls.yaml``: a list of entries with unique ids."""

    urls: list[EvalEntry]

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        counts = Counter(entry.id for entry in self.urls)
        duplicates = sorted(entry_id for entry_id, n in counts.items() if n > 1)
        if duplicates:
            raise ValueError(f"duplicate entry ids: {', '.join(duplicates)}")
        return self


def load_eval_urls(path: Path) -> list[EvalEntry]:
    """Load the evaluation entries in ``path``, in file order.

    Raises:
        ConfigError: if the file is missing or invalid (see ``load_config``).
    """
    try:
        return load_config(path, EvalUrlsConfig).urls
    except ConfigError as exc:
        # A bare list of entries is an easy mistake; say what the fix is.
        if str(exc).endswith("expected a mapping at the top level, got list"):
            raise ConfigError(
                f"{exc}. A top-level 'urls:' key is required: put the list of entries under it"
            ) from None
        raise


def find_urls_file(start: Path) -> Path | None:
    """Return ``eval/urls.yaml`` in ``start`` or its nearest ancestor that has one.

    Lets ``newsfeed eval ...`` find the file whether it runs from the repo root
    or from ``backend/``. Returns None if no ancestor has one.
    """
    start = start.resolve()
    for directory in (start, *start.parents):
        candidate = directory / URLS_FILE
        if candidate.is_file():
            return candidate
    return None
