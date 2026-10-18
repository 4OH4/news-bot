"""Per-domain extraction overrides from ``config/extraction.yaml`` (FR-5, FR-6).

Most domains use the default extractor chain. A domain listed in the file can:

* ``order``: try only these extractors, in this order;
* ``render: playwright``: also try the JavaScript renderer (#15), after the
  static extractors;
* ``mode: summary_only``: skip extraction and keep the feed summary;
* ``min_words``: use a different word count threshold for ``ok``;
* ``paywall: true``: the domain answers subscriber-only pages with HTTP 403, so
  the extract stage records a 403 from it as ``paywalled`` rather than
  ``failed`` (HTTP 402 is ``paywalled`` on every domain).

Domain keys are matched against the article URL's host with ``www.`` stripped,
and a key also matches its subdomains: ``example.com`` applies to
``news.example.com``. When several keys match, the most specific (longest)
one wins on its own; overrides are not merged across levels.

The file is loaded with ``newsfeed.config.load_config``, so a typo or a bad
value fails at startup with the file and location named (ADR-0002).
"""

from __future__ import annotations

import re
from collections import Counter
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from newsfeed.config import ConfigFile, load_config

__all__ = [
    "EXTRACTOR_NAMES",
    "DomainOverride",
    "ExtractionConfig",
    "Mode",
    "Renderer",
    "load_extraction_config",
    "normalise_domain",
    "url_domain",
]

EXTRACTOR_NAMES: Final[frozenset[str]] = frozenset({"trafilatura", "readability", "newspaper4k"})
"""Names an ``order`` list may use: the static extractors' ``Extractor.name``."""

# Lowercase DNS labels separated by dots; at least two labels, so a bare "com"
# (which would match every .com host) is rejected.
_DOMAIN_RE: Final = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")


class Mode(StrEnum):
    """What the extract stage does for a domain."""

    EXTRACT = "extract"
    """Run the extractor chain (the default)."""
    SUMMARY_ONLY = "summary_only"
    """Don't extract; the article keeps its feed summary (status ``skipped``)."""


class Renderer(StrEnum):
    """JavaScript renderers a domain can ask for."""

    PLAYWRIGHT = "playwright"


class DomainOverride(BaseModel):
    """Extraction settings for one domain and its subdomains."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    order: Annotated[list[str], Field(min_length=1)] | None = Field(
        default=None,
        description="Extractor names to try, in order. Default: the chain's own order.",
    )
    render: Renderer | None = Field(
        default=None, description="Also try this renderer after the static extractors."
    )
    mode: Mode = Mode.EXTRACT
    min_words: Annotated[int, Field(ge=1)] | None = Field(
        default=None, description="Word count at or above which a result is ok."
    )
    paywall: bool = Field(
        default=False,
        description="HTTP 403 from this domain means a subscriber-only page (status paywalled).",
    )

    @field_validator("order")
    @classmethod
    def _known_unique_names(cls, order: list[str] | None) -> list[str] | None:
        if order is None:
            return None
        unknown = sorted(set(order) - EXTRACTOR_NAMES)
        if unknown:
            known = ", ".join(sorted(EXTRACTOR_NAMES))
            raise ValueError(f"unknown extractor(s) {', '.join(unknown)}; known: {known}")
        repeated = sorted(name for name, n in Counter(order).items() if n > 1)
        if repeated:
            raise ValueError(f"extractor(s) listed more than once: {', '.join(repeated)}")
        return order

    @model_validator(mode="after")
    def _summary_only_alone(self) -> Self:
        if self.mode is Mode.SUMMARY_ONLY:
            extra = [
                name
                for name, value in (
                    ("order", self.order),
                    ("render", self.render),
                    ("min_words", self.min_words),
                    # summary_only never fetches, so there is no 403 to interpret.
                    ("paywall", self.paywall or None),
                )
                if value is not None
            ]
            if extra:
                raise ValueError(
                    f"mode summary_only skips extraction, so {', '.join(extra)} has no effect"
                )
        return self


class ExtractionConfig(ConfigFile):
    """Schema of ``config/extraction.yaml``: overrides keyed by domain."""

    domains: dict[str, DomainOverride] = Field(default_factory=dict)

    @field_validator("domains", mode="before")
    @classmethod
    def _normalise_keys(cls, domains: object) -> object:
        if not isinstance(domains, dict):
            return domains  # Let the type check report it.
        normalised: dict[str, object] = {}
        for key, value in domains.items():
            if not isinstance(key, str):
                raise ValueError(f"domain {key!r} is not a string")
            domain = normalise_domain(key)
            if not _DOMAIN_RE.match(domain):
                raise ValueError(
                    f"{key!r} is not a domain name (expected e.g. 'example.com', "
                    "without a scheme, path or port)"
                )
            if domain in normalised:
                raise ValueError(f"{key!r} duplicates domain {domain!r}")
            normalised[domain] = value
        return normalised

    def override_for(self, url: str) -> DomainOverride | None:
        """The override for ``url``'s host, or None if no domain key matches it.

        A key matches the host itself and its subdomains; the longest matching
        key wins.
        """
        host = url_domain(url)
        if host is None:
            return None
        labels = host.split(".")
        # Walk from the full host up to its parent domains: most specific first.
        for i in range(len(labels) - 1):
            override = self.domains.get(".".join(labels[i:]))
            if override is not None:
                return override
        return None


def normalise_domain(domain: str) -> str:
    """Lowercase ``domain`` and strip surrounding space, a trailing dot and ``www.``."""
    domain = domain.strip().lower().rstrip(".")
    return domain.removeprefix("www.")


def url_domain(url: str) -> str | None:
    """The normalised host of ``url`` (see ``normalise_domain``), or None if it has none."""
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return normalise_domain(host) if host else None


def load_extraction_config(path: Path) -> ExtractionConfig:
    """Load the overrides in ``path`` (normally ``config/extraction.yaml``).

    An empty file means no overrides.

    Raises:
        ConfigError: if the file is missing or invalid (see ``load_config``).
    """
    return load_config(path, ExtractionConfig)
