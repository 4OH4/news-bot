"""What the rank stage sends to the embedder, and which vectors it accepts back.

An embedding places an article by meaning (ADR-0014). The text is the title, the
summary and the opening of the body: enough to say what the article is about,
short enough to embed a run's new articles in a few CPU seconds.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from newsfeed.models import Article

__all__ = ["EMBED_BODY_CHARS", "embedding_text", "needs_embedding", "vector_problem"]

EMBED_BODY_CHARS = 2000
"""How much of the body (after collapsing whitespace) goes into the embedding text."""

_NORM_TOLERANCE = 1e-3
"""How far from 1 a vector's length may be. float32 models land within about 1e-6."""


def _collapse(text: str | None) -> str:
    return " ".join((text or "").split())


def embedding_text(article: Article, body_text: str | None) -> str:
    """The text to embed: title, summary and body opening, separated by blank lines.

    Whitespace inside each part is collapsed and empty parts are left out. The
    title is required on ``Article``, so the result is never empty.
    """
    body = _collapse(body_text)[:EMBED_BODY_CHARS].rstrip()
    parts = (_collapse(article.title), _collapse(article.summary), body)
    return "\n\n".join(part for part in parts if part)


def needs_embedding(article: Article, model: str) -> bool:
    """True if ``article`` has no embedding, or one from a model other than ``model``."""
    return article.embedding is None or article.embedding_model != model


def vector_problem(vector: Sequence[float], dimensions: int) -> str | None:
    """Why ``vector`` can't be stored as a unit-length embedding, or None if it can.

    Checks the length, that every value is finite, and that the norm is close to
    1. A huge but finite value would overflow when packed as float32, so it is
    caught by the norm check rather than failing the article's write.
    """
    if len(vector) != dimensions:
        return f"expected {dimensions} values, got {len(vector)}"
    if not all(math.isfinite(v) for v in vector):
        return "non-finite value"
    norm = math.sqrt(math.fsum(v * v for v in vector))
    if not abs(norm - 1.0) <= _NORM_TOLERANCE:
        return f"norm {norm:.3g} is not 1"
    return None
