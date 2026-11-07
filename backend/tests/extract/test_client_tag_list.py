"""The reader's DOMPurify allowlist must match the backend sanitiser exactly (#182)."""

from __future__ import annotations

import json
from pathlib import Path

from newsfeed.extract.sanitize import ALLOWED_ATTRIBUTES, ALLOWED_TAGS

ALLOWED_TAGS_JSON = Path(__file__).resolve().parents[3] / "client/src/reader/allowed-tags.json"


def _client_lists() -> dict[str, list[str]]:
    return json.loads(ALLOWED_TAGS_JSON.read_text(encoding="utf-8"))


def test_client_tag_list_has_exactly_the_tags_and_attributes_keys() -> None:
    assert set(_client_lists()) == {"tags", "attributes"}


def test_client_tags_equal_backend_allowed_tags() -> None:
    assert set(_client_lists()["tags"]) == set(ALLOWED_TAGS)


def test_client_attributes_equal_union_of_backend_allowed_attributes() -> None:
    backend = set().union(*ALLOWED_ATTRIBUTES.values())
    assert set(_client_lists()["attributes"]) == backend


def test_client_lists_have_no_duplicates() -> None:
    lists = _client_lists()
    assert len(lists["tags"]) == len(set(lists["tags"]))
    assert len(lists["attributes"]) == len(set(lists["attributes"]))
