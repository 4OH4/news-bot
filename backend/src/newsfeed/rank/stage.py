"""The rank stage: score recent articles and store the result.

Reads articles from the ``Repository`` and their cleaned content from the
``BlobStore``, scores each with ``score_article`` and writes ``score`` and
``score_breakdown`` back. Articles are re-scored on every run, so recency stays
current. Errors on one article are logged and counted, never raised.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import lxml.html

from newsfeed.logging import get_logger
from newsfeed.models import Article, PipelineStage, Source
from newsfeed.rank.interests import InterestsConfig, check_sources, load_interests
from newsfeed.rank.scoring import score_article
from newsfeed.wiring import Services

__all__ = ["DEFAULT_WINDOW_HOURS", "RankSummary", "html_to_text", "load_rank_config", "rank_recent"]

log = get_logger(__name__)

DEFAULT_WINDOW_HOURS = 72.0
"""How far back (by ``fetched_at``) articles are re-scored each run."""

_STAGE = PipelineStage.RANK.value


@dataclass(frozen=True)
class RankSummary:
    """Counts from one ``rank_recent`` call."""

    scored: int = 0
    muted: int = 0
    errors: int = 0
    no_body: int = 0

    def counts(self) -> dict[str, int]:
        """The counters recorded in ``RunStats.stage_counts["rank"]``."""
        return {
            "scored": self.scored,
            "muted": self.muted,
            "no_body": self.no_body,
            "errors": self.errors,
        }


def html_to_text(html: str) -> str:
    """The text of an HTML fragment, without tags, scripts or styles."""
    if not html.strip():
        return ""
    root = lxml.html.fragment_fromstring(html, create_parent="div")
    for element in root.xpath("//script|//style"):
        element.drop_tree()
    return " ".join(root.text_content().split())


def load_rank_config(path: Path, sources: Collection[Source] = ()) -> InterestsConfig:
    """Load ``interests.yaml`` and log what was loaded.

    A missing file means no rules, which is easy to miss, so it is logged as a
    warning. So are source ids the file mentions but ``sources`` lacks.
    """
    config = load_interests(path)
    extra = {
        "stage": _STAGE,
        "path": str(path),
        "topics": len(config.topics),
        "source_rules": len(config.sources),
    }
    if path.exists():
        log.info("interests loaded", extra=extra)
    else:
        log.warning("interests file not found; ranking on source weight and recency", extra=extra)
    unknown = check_sources(config, sources) if sources else []
    if unknown:
        log.warning(
            "interests file mentions unknown sources",
            extra={"stage": _STAGE, "path": str(path), "unknown_sources": unknown},
        )
    return config


def _body_text(services: Services, article: Article) -> str | None:
    if article.content_ref is None:
        return None
    try:
        return html_to_text(services.blobs.get(article.content_ref).decode("utf-8", "replace"))
    except Exception as exc:
        # The article is still worth scoring on its title and summary.
        log.warning(
            "couldn't read article content; scoring without it",
            extra={
                "stage": _STAGE,
                "source_id": article.source_id,
                "url": article.url,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        return None


def rank_recent(
    services: Services,
    config: InterestsConfig,
    *,
    since: datetime,
    sources: Collection[Source] = (),
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RankSummary:
    """Score every article fetched at or after ``since`` and store the result.

    ``sources`` supplies ``Source.weight``; articles from other sources (ad hoc
    or removed) count with weight 1.0. ``config`` is read-only.
    """
    now = clock()
    by_id = {source.id: source for source in sources}
    scored = muted = errors = no_body = 0
    for article in services.repository.list_articles(since=since):
        try:
            body = _body_text(services, article)
            if body is None:
                no_body += 1
            score, breakdown = score_article(
                article, body, config, by_id.get(article.source_id), now
            )
            services.repository.upsert_article(
                Article.model_validate(
                    {**article.model_dump(), "score": score, "score_breakdown": breakdown}
                )
            )
        except Exception as exc:
            errors += 1
            log.warning(
                "ranking failed",
                extra={
                    "stage": _STAGE,
                    "source_id": article.source_id,
                    "url": article.url,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            continue
        scored += 1
        muted += "muted" in breakdown
    log.info(
        "ranking finished",
        extra={"stage": _STAGE, "scored": scored, "muted": muted, "errors": errors},
    )
    return RankSummary(scored=scored, muted=muted, errors=errors, no_body=no_body)
