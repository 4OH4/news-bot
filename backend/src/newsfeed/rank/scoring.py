"""The pure scoring function of the rank stage (FR-7).

``score_article`` turns one article and the rules in ``InterestsConfig`` into a
score plus a breakdown of how each rule contributed. It does no I/O: the stage
(``rank.stage``) reads the body text from the ``BlobStore`` and passes it in.

How the components combine::

    relevance = BASE_SCORE + sum(topic:<name> components)
    score     = max(relevance, 0) * source * recency      (0 if muted)

* ``topic:<name>``: for each topic, each field (title, summary, body) in which
  at least one of its keywords matches adds ``topic.weight * field weight``.
  A field counts once however many keywords or repeats match. Negative topic
  weights demote.
* ``source``: the multiplier for the article's source: the reader's weight in
  ``interests.yaml`` if given, else ``Source.weight``; 1.0 for an unknown source.
* ``recency``: ``0.5 ** (age / half_life)``, from ``published_at`` (falling back
  to ``fetched_at``); 1.0 for a brand-new article, never above 1.
* ``muted``: present (as 1.0) when a mute keyword matches or the source is
  muted. The score is then 0, but the other components are still recorded so
  the reader can see what the article would have scored.

``BASE_SCORE`` keeps an article with no matching topic above zero, so that with
an empty config the score is ``source * recency``. Breakdown values are the
factors above, so the score can be recomputed from them.
"""

from __future__ import annotations

from datetime import datetime

from newsfeed.models import Article, Source
from newsfeed.rank.interests import InterestsConfig, keyword_pattern

__all__ = ["BASE_SCORE", "score_article"]

BASE_SCORE = 1.0
_SECONDS_PER_HOUR = 3600.0


def _matches(keywords: list[str], text: str) -> bool:
    return any(keyword_pattern(keyword).search(text) for keyword in keywords)


def score_article(
    article: Article,
    body_text: str | None,
    config: InterestsConfig,
    source: Source | None,
    now: datetime,
) -> tuple[float, dict[str, float]]:
    """Score ``article`` and explain the score.

    Args:
        article: the article; only its text fields and dates are read.
        body_text: the article body as plain text, or None when it has none.
        config: the interest rules; not modified.
        source: the article's configured source, or None if it isn't one.
        now: the reference time for recency (timezone-aware).

    Returns:
        ``(score, breakdown)``; see the module docstring for how they relate.
    """
    fields = {
        "title": (article.title, config.fields.title),
        "summary": (article.summary or "", config.fields.summary),
        "body": (body_text or "", config.fields.body),
    }
    breakdown: dict[str, float] = {}

    relevance = BASE_SCORE
    for topic in config.topics:
        points = sum(
            topic.weight * weight
            for text, weight in fields.values()
            if text and _matches(topic.keywords, text)
        )
        if points:
            breakdown[f"topic:{topic.name}"] = points
            relevance += points

    rule = config.sources.get(article.source_id)
    if rule is not None and not rule.mute and rule.weight is not None:
        source_factor = rule.weight
    else:
        source_factor = source.weight if source is not None else 1.0
    breakdown["source"] = source_factor

    reference = article.published_at or article.fetched_at
    age_hours = max((now - reference).total_seconds(), 0.0) / _SECONDS_PER_HOUR
    recency = 0.5 ** (age_hours / config.recency.half_life_hours)
    breakdown["recency"] = recency

    muted = (rule is not None and rule.mute) or any(
        text and _matches(config.mute_keywords, text) for text, _ in fields.values()
    )
    if muted:
        breakdown["muted"] = 1.0
        return 0.0, breakdown
    return max(relevance, 0.0) * source_factor * recency, breakdown
