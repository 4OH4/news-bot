"""The pure scoring function of the rank stage (FR-7).

``score_article`` turns one article and the rules in ``InterestsConfig`` into a
score plus a breakdown of how each rule contributed. It does no I/O: the stage
(``rank.stage``) reads the body text from the ``BlobStore`` and passes it in.

How the components combine::

    relevance = BASE_SCORE + sum(topic:<name> components)
    score     = relevance * product(demote:<name> components)
                * mute_keyword_in_body * source * recency     (0 if muted)

* ``topic:<name>``: for each topic with a positive weight, each field (title,
  summary, body) in which at least one of its keywords matches adds
  ``topic.weight * field weight``. A field counts once however many keywords or
  repeats match.
* ``demote:<name>``: a topic with a negative weight demotes rather than
  subtracts: it multiplies the score by ``1 / (1 + |weight| * w)``, where ``w``
  is the weight of the strongest field it matches in (counted once, not summed
  over fields). With the default field weights, weight -1 matched only in the
  body gives 0.5 and in the title 0.25. The factor never reaches 0, so demoted
  articles keep their relative order (e.g. by recency).
* ``mute_keyword_in_body``: 0.5 when a mute keyword matches only in the body.
  A passing mention in the body halves the score instead of muting.
* ``source``: the multiplier for the article's source: the reader's weight in
  ``interests.yaml`` if given, else ``Source.weight``; 1.0 for an unknown source.
* ``recency``: ``0.5 ** (age / half_life)``, from ``published_at`` (falling back
  to ``fetched_at``); 1.0 for a brand-new article, never above 1.
* ``muted``: present (as 1.0) when a mute keyword matches in the title or
  summary, or the source is muted. The score is then 0, but the other
  components are still recorded so the reader can see what the article would
  have scored. Publish (ADR-0006) drops anything with this key, so it is never
  used for the body-only case.

``BASE_SCORE`` keeps an article with no matching topic above zero, so that with
an empty config the score is ``source * recency``. Breakdown values are the
factors above, so the score can be recomputed from them.
"""

from __future__ import annotations

from datetime import datetime

from newsfeed.models import Article, Source
from newsfeed.rank.interests import InterestsConfig, keyword_pattern

__all__ = ["BASE_SCORE", "BODY_MUTE_FACTOR", "score_article"]

BASE_SCORE = 1.0
# Applied when a mute keyword appears only in the body: one passing mention
# there is weaker evidence than one in the title or summary, so it halves the
# score rather than dropping the article from the feed.
BODY_MUTE_FACTOR = 0.5
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
    multiplier = 1.0
    for topic in config.topics:
        matched = [
            weight for text, weight in fields.values() if text and _matches(topic.keywords, text)
        ]
        if not matched:
            continue
        if topic.weight < 0:
            # Demote by a factor rather than subtracting points, so a demoted
            # article never reaches 0 and keeps its order among the others.
            factor = 1.0 / (1.0 + abs(topic.weight) * max(matched))
            if factor < 1.0:
                breakdown[f"demote:{topic.name}"] = factor
                multiplier *= factor
        elif points := topic.weight * sum(matched):
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

    headline = (article.title, article.summary or "")
    keyword_muted = any(text and _matches(config.mute_keywords, text) for text in headline)
    if not keyword_muted and body_text and _matches(config.mute_keywords, body_text):
        breakdown["mute_keyword_in_body"] = BODY_MUTE_FACTOR
        multiplier *= BODY_MUTE_FACTOR

    if keyword_muted or (rule is not None and rule.mute):
        breakdown["muted"] = 1.0
        return 0.0, breakdown
    return relevance * multiplier * source_factor * recency, breakdown
