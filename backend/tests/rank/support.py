"""Shared helpers for the personalisation tests (#323)."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from newsfeed.models import Feedback, FeedbackEvent
from newsfeed.rank.personalisation import PersonalisationConfig, load_personalisation

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)

# A low ``min_events`` so that small tests build a profile.
SMALL_PROFILE = "profile: {min_events: 2, rebuild_min_hours: 6}\n"


def config(tmp_path: Path, text: str = "") -> PersonalisationConfig:
    """The defaults, with the YAML in ``text`` laid over them."""
    path = tmp_path / "personalisation.yaml"
    path.write_text(text)
    return load_personalisation(path)


def unit(*values: float) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def feedback(
    article_id: str,
    event: FeedbackEvent = FeedbackEvent.READ,
    value: float | None = 120,
    *,
    ago: timedelta = timedelta(hours=1),
    at: datetime | None = None,
) -> Feedback:
    return Feedback(article_id=article_id, event=event, value=value, created_at=at or NOW - ago)


def nearest(vector: Sequence[float], centres: Mapping[int, Sequence[float]]) -> int:
    return max(centres, key=lambda i: cosine(vector, centres[i]))
