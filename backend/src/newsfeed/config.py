"""Load and validate the YAML configuration in ``config/`` (CLAUDE.md rule 5).

Each config file is described by a Pydantic model and read with ``load_config``,
which turns every kind of bad input (a missing file, invalid YAML, a duplicate
key, a schema violation) into a ``ConfigError`` naming the file and the exact
location of each problem. Config is our own data, so it fails loudly at load
time rather than several stages later (ADR-0002).

To add a config file, define a model for its top-level mapping (subclass
``ConfigFile`` so unknown keys are rejected) and a ``load_<name>(path)``
function that calls ``load_config``. ``load_sources`` is the example.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_core import ErrorDetails

from newsfeed.models import Source

__all__ = [
    "ConfigError",
    "ConfigFile",
    "DomainParamRules",
    "SourcesConfig",
    "UrlParamsConfig",
    "load_config",
    "load_sources",
    "load_url_params",
]


class ConfigError(ValueError):
    """A config file is missing, unreadable or invalid.

    The message names the file and lists every problem found, one per line.
    """


class ConfigFile(BaseModel):
    """Base for the model of a config file's top-level mapping.

    Unknown keys are rejected, so a typo such as ``enable: false`` fails instead
    of being silently ignored (ADR-0002).
    """

    model_config = ConfigDict(extra="forbid")


_MERGE_TAG = "tag:yaml.org,2002:merge"


class _UniqueKeyLoader(yaml.SafeLoader):
    """A ``SafeLoader`` that rejects duplicate mapping keys.

    Plain PyYAML keeps the last of two identical keys without complaint, so a
    source with two ``url:`` lines would load with whichever came second.
    """

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            # Merge keys (``<<: *anchor``) are resolved by ``super()``; they
            # aren't real keys, and constructing one directly fails.
            if key_node.tag == _MERGE_TAG:
                continue
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in seen
            except TypeError:
                # A complex key such as ``? [a, b]`` is unhashable.
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found unhashable key {key!r}",
                    key_node.start_mark,
                ) from None
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _read_yaml(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"{path}: config file not found") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path}: cannot read config file: {exc}") from exc
    try:
        return yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc


def _location(data: Any, loc: tuple[int | str, ...]) -> str:
    """Render a Pydantic error location as ``sources[2] (id 'bbc').url``.

    Where a location passes through a list item that has an ``id``, the id is
    shown too, because "the source with id 'bbc'" is easier to find in a long
    file than "the third source".
    """
    parts: list[str] = []
    node = data
    for item in loc:
        if isinstance(item, int):
            parts.append(f"[{item}]")
            node = node[item] if isinstance(node, list) and 0 <= item < len(node) else None
            if isinstance(node, dict) and isinstance(node.get("id"), str):
                parts.append(f" (id {node['id']!r})")
        else:
            parts.append(f".{item}" if parts else item)
            node = node.get(item) if isinstance(node, dict) else None
    return "".join(parts) or "(top level)"


def _describe(error: ErrorDetails, data: Any) -> str:
    message = error["msg"].removeprefix("Value error, ")
    return f"  {_location(data, error['loc'])}: {message}"


def load_config[M: ConfigFile](path: Path, model: type[M]) -> M:
    """Read the YAML file at ``path`` and validate it against ``model``.

    An empty file is treated as an empty mapping, so a model whose fields all
    have defaults accepts it.

    Raises:
        ConfigError: if the file is missing or unreadable, isn't valid YAML,
            has a duplicate key, or doesn't match ``model``. The message lists
            every validation error with its location.
    """
    data = _read_yaml(path)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level, got {type(data).__name__}")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        details = "\n".join(_describe(error, data) for error in exc.errors())
        count = exc.error_count()
        noun = "error" if count == 1 else "errors"
        raise ConfigError(f"{path}: {count} validation {noun}\n{details}") from None


# --- config/sources.yaml -----------------------------------------------------


class SourcesConfig(ConfigFile):
    """Schema of ``config/sources.yaml``: a list of ``Source`` entries.

    Each entry is validated by ``models.Source`` (id format, known ``type``,
    ``url`` required for ``rss``, no unknown fields); this model adds the
    file-level check that ids are unique.
    """

    sources: list[Source]

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        counts = Counter(source.id for source in self.sources)
        duplicates = sorted(source_id for source_id, n in counts.items() if n > 1)
        if duplicates:
            raise ValueError(f"duplicate source ids: {', '.join(duplicates)}")
        return self


def load_sources(path: Path) -> list[Source]:
    """Load the sources configured in ``path`` (normally ``config/sources.yaml``).

    Every source is returned, including disabled ones, in file order; the
    pipeline decides which to run.

    Raises:
        ConfigError: if the file is missing or invalid (see ``load_config``).
    """
    return load_config(path, SourcesConfig).sources


# --- config/url_params.yaml --------------------------------------------------

# A lowercase ASCII hostname with at least two labels. Single labels ("com")
# are refused because a rule there would apply to a whole top-level domain.
_DOMAIN = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")
# Characters that can't appear in a decoded query parameter name we compare
# against: whitespace and the query delimiters.
_BAD_PARAM_CHARS = re.compile(r"[\s&=#?]")


def _param_names(values: list[str], field: str) -> list[str]:
    """Lowercase parameter names (matching is case-insensitive) and reject bad ones."""
    problems = [repr(v) for v in values if not v or _BAD_PARAM_CHARS.search(v)]
    if problems:
        raise ValueError(
            f"invalid {field} entries {', '.join(problems)}: must be non-empty, "
            "with no whitespace or '&', '=', '#', '?'"
        )
    return [v.lower() for v in values]


class DomainParamRules(BaseModel):
    """Query-parameter rules for one domain and its subdomains (#35).

    ``strip`` names and ``strip_prefixes`` are removed on top of the global
    tracking list in ``ingest.canonical``; ``keep`` names are never removed,
    overriding both. Names are matched case-insensitively.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    strip: frozenset[str] = frozenset()
    strip_prefixes: tuple[str, ...] = ()
    keep: frozenset[str] = frozenset()

    @field_validator("strip", "keep", "strip_prefixes", mode="before")
    @classmethod
    def _names(cls, value: object, info: ValidationInfo) -> object:
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return _param_names(value, info.field_name or "")
        return value  # let Pydantic report the type error

    @model_validator(mode="after")
    def _no_contradictions(self) -> Self:
        both = sorted(self.strip & self.keep)
        if both:
            raise ValueError(f"listed in both strip and keep: {', '.join(both)}")
        return self


class UrlParamsConfig(ConfigFile):
    """Schema of ``config/url_params.yaml``: per-domain query-parameter rules.

    Keys of ``domains`` are lowercase ASCII hostnames (IDNs in their ``xn--``
    form) with at least two labels; a rule also applies to every subdomain.
    """

    domains: dict[str, DomainParamRules] = Field(default_factory=dict)

    @field_validator("domains", mode="after")
    @classmethod
    def _valid_domains(cls, value: dict[str, DomainParamRules]) -> dict[str, DomainParamRules]:
        bad = [repr(domain) for domain in value if not _DOMAIN.fullmatch(domain)]
        if bad:
            raise ValueError(
                f"invalid domain {', '.join(bad)}: use a lowercase hostname such as "
                "'example.com' (no scheme, path, port or wildcard; IDNs as 'xn--')"
            )
        return value


def load_url_params(path: Path) -> UrlParamsConfig:
    """Load the per-domain URL parameter rules in ``path`` (``config/url_params.yaml``).

    Raises:
        ConfigError: if the file is missing or invalid (see ``load_config``).
    """
    return load_config(path, UrlParamsConfig)
