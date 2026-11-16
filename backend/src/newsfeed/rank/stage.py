"""The rank stage: score recent articles and store the result.

Reads articles from the ``Repository`` and their cleaned content from the
``BlobStore``, scores each with ``score_article`` and writes ``score`` and
``score_breakdown`` back. Articles are re-scored on every run, so recency stays
current. Errors on one article are logged and counted, never raised.

With an ``Embedder``, articles without a current embedding are embedded in
batches first, and the vector goes into the same write as the score (ADR-0014).

With an ``Embedder``, the stage also keeps the owner's interest profile (#323):
before reading the window it rebuilds the profile from recent feedback when
there is new feedback and the last build is old enough, and after every
article is embedded it adds ``similarity`` and ``similarity_raw`` to the
breakdown of each one with a current embedding. ``score`` stays the rule score.
An article the owner has opened or read gets similarity 1.0 and an
``already_read`` entry instead (#363): it is the closest to the profile by
definition, and a lift would fill the personal order with stories already seen.
The profile is the owner's personal history, so its logs carry counts only.

Also with an ``Embedder``, the stage groups the window's embedded articles into
stories (#324): near-identical embeddings fetched within two days share a
``cluster_id``, and articles in a story covered by two or more sources have
their ``score`` multiplied by a ``coverage`` factor. Clusters are recomputed
from scratch on every run.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import lxml.html

from newsfeed.logging import get_logger
from newsfeed.models import (
    DEFAULT_USER_ID,
    Article,
    FeedbackEvent,
    PipelineStage,
    Profile,
    Source,
    article_id,
)
from newsfeed.ports import Embedder, EmbeddingError
from newsfeed.rank.cluster import ClusterInput, cluster_articles, coverage_factor
from newsfeed.rank.embedding import embedding_text, needs_embedding, vector_problem
from newsfeed.rank.interests import InterestsConfig, check_sources, load_interests
from newsfeed.rank.personalisation import PersonalisationConfig
from newsfeed.rank.profile import build_profile, story_weights
from newsfeed.rank.scoring import score_article
from newsfeed.rank.similarity import similarity_factors, similarity_scores
from newsfeed.wiring import Services

__all__ = ["DEFAULT_WINDOW_HOURS", "RankSummary", "html_to_text", "load_rank_config", "rank_recent"]

log = get_logger(__name__)

DEFAULT_WINDOW_HOURS = 72.0
"""How far back (by ``fetched_at``) articles are re-scored each run."""

DEFAULT_EMBED_BATCH_SIZE = 32
"""Texts per ``Embedder.embed`` call; bounds how many bodies are held at once."""

MAX_PROFILE_BACKFILL = 200
"""Feedback articles a profile rebuild embeds at most, so the history fills in over runs."""

_STAGE = PipelineStage.RANK.value

_SEEN_EVENTS = frozenset({FeedbackEvent.OPEN, FeedbackEvent.READ})
"""Feedback that means the owner has already seen the article (#363)."""


@dataclass(frozen=True)
class RankSummary:
    """Counts from one ``rank_recent`` call."""

    scored: int = 0
    muted: int = 0
    errors: int = 0
    no_body: int = 0
    embedded: int = 0
    embed_errors: int = 0
    profile_rebuilt: int = 0
    with_similarity: int = 0
    clusters_multi: int = 0

    def counts(self) -> dict[str, int]:
        """The counters recorded in ``RunStats.stage_counts["rank"]``."""
        return {
            "scored": self.scored,
            "muted": self.muted,
            "no_body": self.no_body,
            "errors": self.errors,
            "embedded": self.embedded,
            "embed_errors": self.embed_errors,
            "profile_rebuilt": self.profile_rebuilt,
            "with_similarity": self.with_similarity,
            "clusters_multi": self.clusters_multi,
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


def _body_text(services: Services, article: Article, *, private: bool = False) -> str | None:
    """The article's body as text, or None. ``private`` keeps the article out of the log."""
    if article.content_ref is None:
        return None
    try:
        return html_to_text(services.blobs.get(article.content_ref).decode("utf-8", "replace"))
    except Exception as exc:
        # The article is still worth scoring on its title and summary.
        about = {} if private else {"source_id": article.source_id, "url": article.url}
        log.warning(
            "couldn't read article content; scoring without it",
            extra={
                "stage": _STAGE,
                **about,
                "error_type": type(exc).__name__,
                **({} if private else {"error": str(exc)}),
            },
        )
        return None


def _embed(
    embedder: Embedder, batch: list[tuple[Article, str | None]], *, private: bool = False
) -> list[list[float] | None]:
    """One ``embed`` call for ``batch``: a checked vector per article, or None where it failed.

    Never raises. A failed call fails the whole batch; an unusable vector fails
    only its article. Logs never carry a vector, and with ``private`` (articles
    taken from feedback) neither an article's URL nor the error text.
    """
    texts = [embedding_text(article, body) for article, body in batch]
    try:
        vectors = embedder.embed(texts)
        dimensions = embedder.dimensions
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
                **({} if private else {"error": str(exc)[:300]}),
            },
        )
        return [None] * len(batch)
    checked: list[list[float] | None] = []
    for (article, _), vector in zip(batch, vectors, strict=True):
        problem = vector_problem(vector, dimensions)
        if problem is None:
            checked.append([float(v) for v in vector])
            continue
        log.warning(
            "embedder returned an unusable vector",
            extra={
                "stage": _STAGE,
                **({} if private else {"url": article.url}),
                "model": embedder.model,
                "problem": problem,
            },
        )
        checked.append(None)
    return checked


def _with_embedding(article: Article, vector: list[float] | None, model: str) -> dict[str, object]:
    data = article.model_dump()
    if vector is not None:
        data |= {"embedding": vector, "embedding_model": model}
    return data


def _rebuild_due(
    services: Services,
    profile: Profile | None,
    model: str,
    config: PersonalisationConfig,
    now: datetime,
) -> bool:
    """True when there is no usable profile, or feedback newer than it, and the build is old.

    The age check comes first, so a run between rebuilds reads no feedback.
    Event times are capped at ``now``, so a phone clock set ahead can't make
    ``last_feedback_at`` a future time that later feedback never passes (#363).
    """
    if profile is None or profile.built_at is None:
        return True
    if now - profile.built_at < timedelta(hours=config.profile.rebuild_min_hours):
        return False
    if profile.embedding_model != model:
        return True
    last = profile.last_feedback_at
    since = last or now - timedelta(days=config.profile.window_days)
    events = services.repository.list_feedback(since)
    return any(last is None or min(f.created_at, now) > last for f in events)


def _feedback_stories(
    services: Services, weights: dict[str, float]
) -> tuple[dict[str, Article], dict[str, float], int]:
    """The row holding each weighted article's story, the weight per story, and how many are gone.

    Follows ``declared_canonical`` once to the row it names, when that row
    exists (ADR-0010 decision 6). Articles no longer stored are counted, not raised.
    """
    rows: dict[str, Article] = {}
    story_weight: dict[str, float] = defaultdict(float)
    missing = 0
    for feedback_id in sorted(weights):
        row = services.repository.get_article(feedback_id)
        if row is None:
            missing += 1
            continue
        declared = row.extraction.declared_canonical
        if declared:
            row = services.repository.get_article(article_id(declared)) or row
        rows[row.id] = row
        story_weight[row.id] += weights[feedback_id]
    return rows, dict(story_weight), missing


def _rebuild_profile(
    services: Services,
    embedder: Embedder,
    config: PersonalisationConfig,
    *,
    now: datetime,
    window_since: datetime,
    batch_size: int,
) -> Profile:
    """Build the profile from recent feedback and save it.

    Feedback articles with no embedding from ``embedder.model`` are embedded and
    stored, at most ``MAX_PROFILE_BACKFILL`` per rebuild, except those in the
    rank window (fetched at or after ``window_since``): the rank stage embeds
    and writes those itself, after this, so the two writes can't collide.
    """
    began = time.perf_counter()
    repo = services.repository
    events = repo.list_feedback(now - timedelta(days=config.profile.window_days))
    rows, weights, missing = _feedback_stories(services, story_weights(events, config, now))
    vectors: dict[str, list[float]] = {}
    backfill: list[Article] = []
    for story_id, row in rows.items():
        if row.embedding is not None and not needs_embedding(row, embedder.model):
            vectors[story_id] = row.embedding
        elif row.fetched_at < window_since and len(backfill) < MAX_PROFILE_BACKFILL:
            backfill.append(row)
    backfilled = backfill_errors = 0
    for start in range(0, len(backfill), batch_size):
        batch = backfill[start : start + batch_size]
        pairs = [(row, _body_text(services, row, private=True)) for row in batch]
        for row, vector in zip(batch, _embed(embedder, pairs, private=True), strict=True):
            if vector is None:
                backfill_errors += 1
                continue
            try:
                repo.upsert_article(
                    Article.model_validate(_with_embedding(row, vector, embedder.model))
                )
            except Exception as exc:
                backfill_errors += 1
                log.warning(
                    "couldn't store a feedback article's embedding",
                    extra={"stage": _STAGE, "error_type": type(exc).__name__},
                )
                continue
            vectors[row.id] = vector
            backfilled += 1
    profile = build_profile(
        weights,
        vectors,
        config=config,
        now=now,
        model=embedder.model,
        last_feedback_at=max((min(f.created_at, now) for f in events), default=None),
    )
    repo.save_profile(profile)
    log.info(
        "interest profile rebuilt",
        extra={
            "stage": _STAGE,
            "events": len(events),
            "stories": len(rows),
            "missing_articles": missing,
            "without_embedding": len(rows) - len(vectors),
            "backfilled": backfilled,
            "backfill_errors": backfill_errors,
            "centroids": len(profile.centroids),
            "has_negative": profile.negative is not None,
            "seconds": round(time.perf_counter() - began, 3),
        },
    )
    return profile


def _current_profile(
    services: Services,
    embedder: Embedder,
    config: PersonalisationConfig,
    *,
    now: datetime,
    window_since: datetime,
    batch_size: int,
) -> tuple[Profile | None, bool]:
    """The saved profile, rebuilt first when due, and whether it was rebuilt. Raises on failure."""
    profile = services.repository.get_profile(DEFAULT_USER_ID)
    if not _rebuild_due(services, profile, embedder.model, config, now):
        return profile, False
    rebuilt = _rebuild_profile(
        services, embedder, config, now=now, window_since=window_since, batch_size=batch_size
    )
    return rebuilt, True


def _seen_ids(services: Services, since: datetime) -> set[str]:
    """Ids of articles the owner opened or read at or after ``since``."""
    return {
        f.article_id
        for f in services.repository.list_feedback(since)
        if f.user_id == DEFAULT_USER_ID and f.event in _SEEN_EVENTS
    }


def _with_cluster_mates(
    ready: list[tuple[Article, dict[str, object], bool]], seen: set[str]
) -> set[str]:
    """``seen`` plus every article in ``ready`` sharing a story with one of them (#367)."""
    stories = {data.get("cluster_id") for a, data, _ in ready if a.id in seen} - {None}
    return seen | {a.id for a, data, _ in ready if data.get("cluster_id") in stories}


def _similarity(
    ready: list[tuple[Article, dict[str, object], bool]],
    profile: Profile,
    model: str,
    config: PersonalisationConfig,
    seen: Collection[str] = frozenset(),
) -> dict[str, tuple[float, float]]:
    """Per article id in ``ready`` with an embedding from ``model``: (factor, raw).

    Articles in ``seen`` get factor 1.0 and are left out of the percentile
    ranking, so they don't push unread articles down it.
    """
    vectors: dict[str, list[float]] = {}
    for article, data, _ in ready:
        vector = data.get("embedding")
        if data.get("embedding_model") == model and isinstance(vector, list):
            vectors[article.id] = vector
    raw = similarity_scores(vectors, profile, config.similarity.negative_weight)
    factors = similarity_factors(
        {k: r for k, r in raw.items() if k not in seen}, config.similarity.beta
    )
    return {k: (factors.get(k, 1.0), raw[k]) for k in raw}


def _cluster(
    ready: list[tuple[Article, dict[str, object], bool]],
    model: str,
    config: PersonalisationConfig,
) -> int:
    """Set ``cluster_id`` on each article in ``ready`` with an embedding from ``model``.

    Where a story has two or more distinct sources, multiplies the score by
    the coverage factor and records ``coverage`` and ``coverage_sources``.
    Muted articles stay at 0 and don't count towards coverage. Returns the
    number of such stories.
    """
    settings = config.clustering
    members: list[ClusterInput] = []
    for article, data, _ in ready:
        vector = data.get("embedding")
        if data.get("embedding_model") == model and isinstance(vector, list):
            members.append(ClusterInput(article.id, article.fetched_at, article.source_id, vector))
    cluster_of = cluster_articles(
        members, threshold=settings.threshold, max_gap_hours=settings.max_gap_hours
    )
    sources: dict[str, set[str]] = defaultdict(set)
    for article, data, _ in ready:
        cluster_id = cluster_of.get(article.id)
        breakdown = data["score_breakdown"]
        assert isinstance(breakdown, dict)
        # Set (even None) so a stale id from an earlier run never survives.
        data["cluster_id"] = cluster_id
        if cluster_id is not None and "muted" not in breakdown:
            sources[cluster_id].add(article.source_id)
    for article, data, _ in ready:
        cluster_id = cluster_of.get(article.id)
        count = len(sources[cluster_id]) if cluster_id is not None else 0
        breakdown = data["score_breakdown"]
        assert isinstance(breakdown, dict)
        if count < 2 or "muted" in breakdown:
            continue
        factor = coverage_factor(count, settings.coverage_gamma)
        score = data["score"]
        assert isinstance(score, float)
        data["score"] = score * factor
        breakdown |= {"coverage": factor, "coverage_sources": float(count)}
    return sum(len(v) >= 2 for v in sources.values())


def rank_recent(
    services: Services,
    config: InterestsConfig,
    *,
    since: datetime,
    sources: Collection[Source] = (),
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    embedder: Embedder | None = None,
    embed_batch_size: int = DEFAULT_EMBED_BATCH_SIZE,
    personalisation: PersonalisationConfig | None = None,
) -> RankSummary:
    """Score every article fetched at or after ``since`` and store the result.

    ``sources`` supplies ``Source.weight``; articles from other sources (ad hoc
    or removed) count with weight 1.0. ``config`` is read-only.

    ``embedder`` defaults to ``services.embedder``. With one, articles lacking an
    embedding from its model are embedded ``embed_batch_size`` at a time, and the
    vector is stored in the same write as the score. Without one, a warning is
    logged and ranking is unchanged. A failed batch or vector is logged and
    counted in ``embed_errors``; those articles are still scored.

    With an embedder, the interest profile is rebuilt first when due (see
    ``personalisation``, which defaults to the built-in weights), and once every
    article is scored and embedded, those with a current embedding get
    ``similarity`` and ``similarity_raw`` in their breakdown, if the profile has
    centroids from the same model. Articles are written once, after that. A
    failed profile step is logged and counted in ``errors``; ranking goes on
    without similarity.
    """
    if embed_batch_size < 1:
        raise ValueError("embed_batch_size must be at least 1")
    now = clock()
    by_id = {source.id: source for source in sources}
    personalisation = personalisation or PersonalisationConfig()
    if embedder is None:
        embedder = services.embedder
    if embedder is None:
        log.warning(
            "embeddings are off (NEWSFEED_EMBED_MODEL_DIR is not set); ranking without them",
            extra={"stage": _STAGE},
        )
    scored = muted = errors = no_body = embedded = embed_errors = with_similarity = 0
    embed_seconds = 0.0
    profile: Profile | None = None
    rebuilt = False
    profile_began = time.perf_counter()
    if embedder is not None:
        try:
            # Before the window snapshot below, so the rank writes can't
            # overwrite embeddings the rebuild stores.
            profile, rebuilt = _current_profile(
                services,
                embedder,
                personalisation,
                now=now,
                window_since=since,
                batch_size=embed_batch_size,
            )
        except Exception as exc:
            errors += 1
            log.warning(
                "interest profile step failed; ranking without similarity",
                extra={"stage": _STAGE, "error_type": type(exc).__name__},
            )
    profile_seconds = time.perf_counter() - profile_began
    pending: list[tuple[Article, str | None]] = []
    # (article as read, its new row, whether this run embedded it)
    ready: list[tuple[Article, dict[str, object], bool]] = []

    def prepare(article: Article, body: str | None, vector: list[float] | None) -> None:
        nonlocal errors
        try:
            score, breakdown = score_article(
                article, body, config, by_id.get(article.source_id), now
            )
        except Exception as exc:
            errors += 1
            _log_rank_failure(article, exc)
            return
        data = _with_embedding(article, vector, embedder.model if embedder else "")
        data |= {"score": score, "score_breakdown": breakdown}
        ready.append((article, data, vector is not None))

    def flush() -> None:
        nonlocal embed_errors, embed_seconds
        assert embedder is not None
        began = time.perf_counter()
        vectors = _embed(embedder, pending)
        embed_seconds += time.perf_counter() - began
        embed_errors += vectors.count(None)
        for (article, body), vector in zip(pending, vectors, strict=True):
            prepare(article, body, vector)
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
            prepare(article, body, None)
    if pending:
        flush()

    clusters_multi = 0
    if embedder is not None:
        clusters_multi = _cluster(ready, embedder.model, personalisation)

    # Similarity ranks each article among all of this run's, so it waits for every batch.
    similarity: dict[str, tuple[float, float]] = {}
    seen: set[str] = set()
    if (
        embedder is not None
        and profile is not None
        and profile.centroids
        and profile.embedding_model == embedder.model
    ):
        try:
            seen = _seen_ids(services, since)
            # A second version of a story already read gets no lift either.
            seen = _with_cluster_mates(ready, seen)
        except Exception as exc:
            # Without the list, a seen article would get a lift; skip similarity instead.
            errors += 1
            log.warning(
                "couldn't read feedback for already-read articles; ranking without similarity",
                extra={"stage": _STAGE, "error_type": type(exc).__name__},
            )
        else:
            similarity = _similarity(ready, profile, embedder.model, personalisation, seen)
    for article, data, new_vector in ready:
        breakdown = data["score_breakdown"]
        assert isinstance(breakdown, dict)
        if article.id in similarity:
            factor, raw = similarity[article.id]
            breakdown |= {"similarity": factor, "similarity_raw": raw}
            if article.id in seen:
                breakdown["already_read"] = 1.0
        try:
            services.repository.upsert_article(Article.model_validate(data))
        except Exception as exc:
            errors += 1
            _log_rank_failure(article, exc)
            continue
        scored += 1
        muted += "muted" in breakdown
        embedded += new_vector
        with_similarity += article.id in similarity
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
            "profile_rebuilt": int(rebuilt),
            "profile_seconds": round(profile_seconds, 3),
            "with_similarity": with_similarity,
            "clusters_multi": clusters_multi,
        },
    )
    return RankSummary(
        scored=scored,
        muted=muted,
        errors=errors,
        no_body=no_body,
        embedded=embedded,
        embed_errors=embed_errors,
        profile_rebuilt=int(rebuilt),
        with_similarity=with_similarity,
        clusters_multi=clusters_multi,
    )


def _log_rank_failure(article: Article, exc: Exception) -> None:
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
