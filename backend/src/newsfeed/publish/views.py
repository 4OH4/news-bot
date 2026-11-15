"""Feed views: one ranked list per way of reading, beside ``feed.json`` (#288, ADR-0012).

Each view in ``config/views.yaml`` is written to ``views/<name>.json`` as
``{"run_id", "generated_at", "items"}``, items in the ``feed.json`` item shape.
Candidates come from ``build_feed`` given only the view's sources, so a view
holds what ``feed.json`` could, uncapped and in the view's order.

A view whose window is longer than publish's read is carried forward: this
run's candidates plus the previous file's items that are still eligible.
Without a usable previous file it is seeded once from a read of its window.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Collection, Sequence
from datetime import datetime, timedelta
from typing import Any

from newsfeed.config import ViewConfig
from newsfeed.logging import get_logger
from newsfeed.models import Article, PipelineStage, Source
from newsfeed.ports import BlobNotFoundError, BlobStore
from newsfeed.publish.feed import build_feed, effective_time
from newsfeed.wiring import Services

__all__ = ["VIEW_SAFETY_CAP", "build_view", "content_checks", "publish_views"]

log = get_logger(__name__)

VIEW_SAFETY_CAP = 2000
"""Most items one view holds; reaching it is logged."""

_STAGE = PipelineStage.PUBLISH.value
Row = tuple[dict[str, Any], datetime]


def content_checks(blobs: BlobStore) -> tuple[Callable[[Article], bool], Callable[[str], bool]]:
    """Whether an article's cleaned HTML, or a blob key, is stored; asked once per blob.

    A check that can't be made counts as missing, as in ``feed.json``.
    """
    answers: dict[str, bool] = {}

    def exists(ref: str) -> bool:
        if ref not in answers:
            try:
                answers[ref] = blobs.exists(ref)
            except Exception as exc:
                log.warning("couldn't check content", extra={"stage": _STAGE, "error": str(exc)})
                answers[ref] = False
        return answers[ref]

    def has_content(article: Article) -> bool:
        return article.content_ref is not None and exists(article.content_ref)

    return has_content, lambda key: exists(blobs.ref_for(key))


def _carried(
    previous: Sequence[object],
    *,
    live: Collection[str],
    cutoff: datetime,
    now: datetime,
    has_key: Callable[[str], bool],
    read_ids: Collection[str],
    stories: set[str],
) -> list[Row]:
    """The previous file's items that stay, each with its effective time.

    An item whose article was read this run is left to this run's selection,
    which knows whether it is still a candidate (muted since, for example).
    """
    kept: list[Row] = []
    for item in previous:
        if not isinstance(item, dict):
            continue
        try:
            finite = math.isfinite(item["score"])
            published = datetime.fromisoformat(item["published_at"])
            story, breakdown = item["story_id"], item["score_breakdown"]
        except (KeyError, TypeError, ValueError):
            continue  # not an item this code wrote, or no published_at
        content = item.get("content_path")
        if (
            not finite
            or published.tzinfo is None
            or not (isinstance(story, str) and isinstance(item.get("id"), str))
            or not isinstance(breakdown, dict)
            or published < cutoff
            or published > now  # fresh items are capped at fetch time; same clamp here
            or item.get("source_id") not in live
            or "muted" in breakdown
            or item["id"] in read_ids
            or story in stories
            or item.get("display") not in ("article", "link")
            or (
                item["display"] == "article" and not (isinstance(content, str) and has_key(content))
            )
        ):
            continue
        stories.add(story)
        kept.append((item, published))
    return kept


def build_view(
    name: str,
    view: ViewConfig,
    articles: Sequence[Article],
    sources: Collection[Source],
    *,
    now: datetime,
    run_id: str,
    has_content: Callable[[Article], bool],
    has_key: Callable[[str], bool],
    previous: Sequence[object] = (),
) -> list[dict[str, Any]]:
    """The items of one view, in its order: candidates in ``articles``, plus ``previous``
    items carried forward."""
    wanted = set(view.tags or ())
    matching = [
        s
        for s in sources
        if s.enabled and s.weight > 0 and (view.tags is None or wanted.intersection(s.tags))
    ]
    fresh = build_feed(
        articles,
        matching,
        now=now,
        run_id=run_id,
        has_content=has_content,
        window_hours=view.window_hours,
        limit=len(articles),
    ).feed["items"]
    times = {a.id: effective_time(a) for a in articles}
    rows: list[Row] = [(item, times[item["id"]]) for item in fresh]
    rows += _carried(
        previous,
        live={s.id for s in matching},
        cutoff=now - timedelta(hours=view.window_hours),
        now=now,
        has_key=has_key,
        read_ids=times.keys(),
        stories={item["story_id"] for item in fresh},
    )
    if view.order == "newest":
        rows.sort(key=lambda r: (-r[1].timestamp(), -r[0]["score"], r[0]["id"]))
    else:  # as build_feed orders feed.json
        rows.sort(key=lambda r: (-r[0]["score"], -r[1].timestamp(), r[0]["id"]))
    if len(rows) > VIEW_SAFETY_CAP:
        log.warning(
            "view reached its safety cap",
            extra={"stage": _STAGE, "view": name, "items": len(rows), "cap": VIEW_SAFETY_CAP},
        )
    return [item for item, _ in rows[:VIEW_SAFETY_CAP]]


def _previous_items(blobs: BlobStore, name: str) -> list[object] | None:
    """The items of the stored view file, or None if it is missing or unreadable."""
    try:
        document = json.loads(blobs.get(blobs.ref_for(f"views/{name}.json")))
    except (BlobNotFoundError, ValueError):  # JSON and Unicode errors are ValueErrors
        return None
    items = document.get("items") if isinstance(document, dict) else None
    return items if isinstance(items, list) else None


def publish_views(
    services: Services,
    sources: Collection[Source],
    *,
    articles: Sequence[Article],
    read_window_hours: float,
    run_id: str,
    now: datetime,
    generated_at: str,
) -> dict[str, str]:
    """Write every view in ``services.views``, and say how each went.

    ``articles`` are publish's read of the last ``read_window_hours``. Each
    view's outcome is ``written``, ``seeded`` or ``failed``; a failed view is
    logged and keeps its previous file, and the others are still written.
    """
    has_content, has_key = content_checks(services.blobs)
    seeds: dict[float, list[Article]] = {}
    outcomes: dict[str, str] = {}
    for name, view in services.views.views.items():
        try:
            reads, outcome, carry = articles, "written", view.window_hours > read_window_hours
            previous = _previous_items(services.blobs, name) if carry else None
            if carry and previous is None:
                if view.window_hours not in seeds:
                    since = now - timedelta(hours=view.window_hours)
                    seeds[view.window_hours] = services.repository.list_articles(since=since)
                reads, outcome = seeds[view.window_hours], "seeded"
                log.info(
                    "seeding view from a read of its whole window",
                    extra={"stage": _STAGE, "view": name, "articles": len(reads)},
                )
            items = build_view(
                name,
                view,
                reads,
                sources,
                now=now,
                run_id=run_id,
                has_content=has_content,
                has_key=has_key,
                previous=previous or (),
            )
            document = {"run_id": run_id, "generated_at": generated_at, "items": items}
            data = json.dumps(document, ensure_ascii=False, allow_nan=False).encode("utf-8")
            services.blobs.put(f"views/{name}.json", data, "application/json")
        except Exception as exc:
            outcomes[name] = "failed"
            log.error(
                "view not written; its previous file stays",
                extra={"stage": _STAGE, "view": name, "run_id": run_id, "error": str(exc)},
            )
            continue
        outcomes[name] = outcome
        log.info("view published", extra={"stage": _STAGE, "view": name, "items": len(items)})
    return outcomes
