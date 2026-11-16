"""Tests for the text sent to the embedder and the check for a missing embedding."""

from __future__ import annotations

import pytest

from newsfeed.models import Article
from newsfeed.rank.embedding import EMBED_BODY_CHARS, embedding_text, needs_embedding

MODEL = "fake-embedder"
VECTOR = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


def make(**changes: object) -> Article:
    url = "https://news.example.com/a"
    return Article.model_validate(
        {
            "canonical_url": url,
            "url": url,
            "source_id": "gazette",
            "title": "A",
            "fetched_at": "2026-10-18T12:00:00Z",
            **changes,
        }
    )


def test_text_is_title_summary_and_the_first_body_characters() -> None:
    body = "x" * 2000 + "y" * 3000

    text = embedding_text(make(summary="B"), body)

    assert EMBED_BODY_CHARS == 2000
    assert text.startswith("A\n\nB\n\n")
    assert text[len("A\n\nB\n\n") :] == "x" * 2000


@pytest.mark.parametrize(
    ("summary", "body", "expected"),
    [
        (None, None, "A"),
        ("  B \n\n C ", "one\n\n  two\tthree", "A\n\nB C\n\none two three"),
        (None, "   ", "A"),
    ],
)
def test_text_collapses_whitespace_and_is_never_empty(
    summary: str | None, body: str | None, expected: str
) -> None:
    assert embedding_text(make(summary=summary), body) == expected


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, True),
        ({"embedding": VECTOR, "embedding_model": MODEL}, False),
        ({"embedding": VECTOR, "embedding_model": "older-model"}, True),
    ],
)
def test_needs_embedding_when_missing_or_from_another_model(
    changes: dict[str, object], expected: bool
) -> None:
    assert needs_embedding(make(**changes), MODEL) is expected
