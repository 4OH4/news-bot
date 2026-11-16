"""The rank stage: score recent articles and store the result.

Reads articles from the ``Repository`` and their cleaned content from the
``BlobStore``, scores each with ``score_article`` and writes ``score`` and
``score_breakdown`` back. Articles are re-scored on every run, so recency stays
current. Errors on one article are logged and counted, never raised.

With an ``Embedder``, articles without a current embedding are embedded in
batches first, and the vector goes into the same write as the score (ADR-0014).
``backfill_embeddings`` embeds the articles the owner's feedback points at.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import lxml.html

from newsfeed.logging import get_logger
from newsfeed.models import Article, PipelineStage, Source
from newsfeed.ports import Embedder, EmbeddingError
from newsfeed.rank.embedding import embedding_text, needs_embedding, vector_problem
from newsfeed.rank.interests import InterestsConfig, check_sources, load_interests
from newsfeed.rank.scoring import score_article
from newsfeed.wiring import Services

__all__ = [
    "DEFAULT_EMBED_BATCH_SIZE",
    "DEFAULT_WINDOW_HOURS",
    "BackfillSummary",
    "RankSummary",
    "backfill_embeddings",
    "html_to_text",
    "load_rank_config",
    "rank_recent",
]

log = get_logger(__name__)

DEFAULT_WINDOW_HOURS = 72.0
"""How far back (by ``fetched_at``) articles are re-scored each run."""

DEFAULT_EMBED_BATCH_SIZE = 32
"""Texts per ``Embedder.embed`` call; bounds how many bodies are held at once."""

_STAGE = PipelineStage.RANK.value


@dataclass(frozen=True)
class RankSummary:
    """Counts from one ``rank_recent`` call."""

    scored: int = 0
    muted: int = 0
    errors: int = 0
    no_body: int = 0
    embedded: int = 0
    embed_errors: int = 0

    def counts(self) -> dict[str, int]:
        """The counters recorded in ``RunStats.stage_counts["rank"]``."""
        return {
            "scored": self.scored,
            "muted": self.muted,
            "no_body": self.no_body,
            "errors": self.errors,
            "embedded": self.embedded,
            "embed_errors": self.embed_errors,
        }


@dataclass(frozen=True)
class BackfillSummary:
    """Counts from one ``backfill_embeddings`` call. Counts only: no ids or titles."""

    articles: int = 0
    missing: int = 0
    current: int = 0
    embedded: int = 0
    errors: int = 0
    no_body: int = 0

    def counts(self) -> dict[str, int]:
        """The counters, by name. With ``dry_run``, ``embedded`` is what would be."""
        return {
            "articles": self.articles,
            "missing": self.missing,
            "current": self.current,
            "embedded": self.embedded,
            "errors": self.errors,
            "no_body": self.no_body,
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


def _embed(
    embedder: Embedder, batch: list[tuple[Article, str | None]], *, log_urls: bool
) -> list[list[float] | None]:
    """One ``embed`` call for ``batch``: a checked vector per article, or None where it failed.

    Never raises. A failed call fails the whole batch; an unusable vector fails
    only its article. Logs never carry a vector, and carry urls only when
    ``log_urls`` (not for articles taken from feedback: that is reading history).
    """
    texts = [embedding_text(article, body) for article, body in batch]
    try:
        vectors = embedder.embed(texts)
        if len(vectors) != len(texts):
            raise EmbeddingError(f"expected {len(texts)} vectors, got {len(vectors)}")
    except Exception as exc:
        log.warning(
            "embedding failed; continuing without embeddings for this batch",
            extra={
                "stage": _STAGE,
                "model": embedder.model,
                "count": len(texts),
                "error_type": type(exc).__name__,
                "error": str(exc)[:300],
            },
        )
        return [None] * len(batch)
    checked: list[list[float] | None] = []
    for (article, _), vector in zip(batch, vectors, strict=True):
        problem = vector_problem(vector, embedder.dimensions)
        if problem is None:
            checked.append([float(v) for v in vector])
            continue
        extra = {"stage": _STAGE, "model": embedder.model, "problem": problem}
        if log_urls:
            extra |= {"source_id": article.source_id, "url": article.url}
        log.warning("embedder returned an unusable vector", extra=extra)
        checked.append(None)
    return checked


def _with_embedding(article: Article, vector: list[float] | None, model: str) -> dict[str, object]:
    data = article.model_dump()
    if vector is not None:
        data |= {"embedding": vector, "embedding_model": model}
    return data


def rank_recent(
    services: Services,
    config: InterestsConfig,
    *,
    since: datetime,
    sources: Collection[Source] = (),
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    embedder: Embedder | None = None,
    embed_batch_size: int = DEFAULT_EMBED_BATCH_SIZE,
) -> RankSummary:
    """Score every article fetched at or after ``since`` and store the result.

    ``sources`` supplies ``Source.weight``; articles from other sources (ad hoc
    or removed) count with weight 1.0. ``config`` is read-only.

    ``embedder`` defaults to ``services.embedder``. With one, articles lacking an
    embedding from its model are embedded ``embed_batch_size`` at a time, and the
    vector is stored in the same write as the score. Without one, a warning is
    logged and ranking is unchanged. A failed batch or vector is logged and
    counted in ``embed_errors``; those articles are still scored.
    """
    if embed_batch_size < 1:
        raise ValueError("embed_batch_size must be at least 1")
    now = clock()
    by_id = {source.id: source for source in sources}
    if embedder is None:
        embedder = services.embedder
    if embedder is None:
        log.warning(
            "embeddings are off (NEWSFEED_EMBED_MODEL_DIR is not set); ranking without them",
            extra={"stage": _STAGE},
        )
    scored = muted = errors = no_body = embedded = embed_errors = 0
    embed_seconds = 0.0
    pending: list[tuple[Article, str | None]] = []

    def write(article: Article, body: str | None, vector: list[float] | None) -> None:
        nonlocal scored, muted, errors, embedded
        try:
            score, breakdown = score_article(
                article, body, config, by_id.get(article.source_id), now
            )
            data = _with_embedding(article, vector, embedder.model if embedder else "")
            data |= {"score": score, "score_breakdown": breakdown}
            services.repository.upsert_article(Article.model_validate(data))
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
            return
        scored += 1
        muted += "muted" in breakdown
        embedded += vector is not None

    def flush() -> None:
        nonlocal embed_errors, embed_seconds
        assert embedder is not None
        began = time.perf_counter()
        vectors = _embed(embedder, pending, log_urls=True)
        embed_seconds += time.perf_counter() - began
        embed_errors += vectors.count(None)
        for (article, body), vector in zip(pending, vectors, strict=True):
            write(article, body, vector)
        pending.clear()

    for article in services.repository.list_articles(since=since):
        body = _body_text(services, article)
        if body is None:
            no_body += 1
        if embedder is not None and needs_embedding(article, embedder.model):
            pending.append((article, body))
            if len(pending) >= embed_batch_size:
                flush()
        else:
            write(article, body, None)
    if pending:
        flush()
    log.info(
        "ranking finished",
        extra={
            "stage": _STAGE,
            "scored": scored,
            "muted": muted,
            "errors": errors,
            "embedded": embedded,
            "embed_errors": embed_errors,
            "embed_seconds": round(embed_seconds, 3),
            "model": embedder.model if embedder else None,
        },
    )
    return RankSummary(
        scored=scored,
        muted=muted,
        errors=errors,
        no_body=no_body,
        embedded=embedded,
        embed_errors=embed_errors,
    )


def _stored_body(services: Services, article: Article) -> str | None:
    """The article's stored body text, or None. Logs nothing: these come from feedback."""
    if article.content_ref is None:
        return None
    try:
        return html_to_text(services.blobs.get(article.content_ref).decode("utf-8", "replace"))
    except Exception:  # expired or unreadable: embed the title and summary instead
        return None


def backfill_embeddings(
    services: Services,
    embedder: Embedder,
    *,
    since: datetime,
    dry_run: bool = False,
    batch_size: int = DEFAULT_EMBED_BATCH_SIZE,
) -> BackfillSummary:
    """Embed the stored articles that feedback since ``since`` points at, if they need it.

    Each article is embedded from its stored body, or from its title and summary
    once the body has expired. The article is re-read just before its write, so a
    score the rank stage stored meanwhile is kept. With ``dry_run``, nothing is
    embedded or written, and ``embedded`` counts what would be. Logs counts only.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    began = time.perf_counter()
    repository = services.repository
    ids = list(dict.fromkeys(f.article_id for f in repository.list_feedback(since)))
    missing = current = embedded = errors = no_body = 0
    todo: list[Article] = []
    for article_id in ids:
        article = repository.get_article(article_id)
        if article is None:
            missing += 1
        elif needs_embedding(article, embedder.model):
            todo.append(article)
        else:
            current += 1
    if dry_run:
        embedded = len(todo)
        todo = []
    for start in range(0, len(todo), batch_size):
        batch = [(a, _stored_body(services, a)) for a in todo[start : start + batch_size]]
        no_body += sum(body is None for _, body in batch)
        vectors = _embed(embedder, batch, log_urls=False)
        for (article, _), vector in zip(batch, vectors, strict=True):
            if vector is None:
                errors += 1
                continue
            try:
                fresh = repository.get_article(article.id)
                if fresh is None:
                    missing += 1
                    continue
                data = _with_embedding(fresh, vector, embedder.model)
                repository.upsert_article(Article.model_validate(data))
            except Exception as exc:
                errors += 1
                log.warning(
                    "backfill write failed",
                    extra={"stage": _STAGE, "error_type": type(exc).__name__},
                )
                continue
            embedded += 1
    summary = BackfillSummary(
        articles=len(ids),
        missing=missing,
        current=current,
        embedded=embedded,
        errors=errors,
        no_body=no_body,
    )
    log.info(
        "embedding backfill finished",
        extra={
            "stage": _STAGE,
            "model": embedder.model,
            "dry_run": dry_run,
            "seconds": round(time.perf_counter() - began, 3),
            **summary.counts(),
        },
    )
    return summary
