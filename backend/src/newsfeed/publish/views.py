"""Feed views: one ranked list per way of reading, beside ``feed.json`` (ADR-0012, ADR-0013).

Each view in ``config/views.yaml`` is written to ``views/<name>.json`` as
``{"run_id", "generated_at", "items"}``, items in the ``feed.json`` item shape.
A view is source tags, an order and ``max_items``. A tagged view reads its
sources' newest ``max_items`` articles with one filtered query; the view
without tags uses the articles publish already read for ``feed.json``. Both
pass ``feed.json``'s eligibility rules with no time window, and keep the score
the rank stage stored.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any

from newsfeed.config import ViewConfig
from newsfeed.logging import get_logger
from newsfeed.models import Article, PipelineStage, Source
from newsfeed.publish.feed import build_feed, effective_time
from newsfeed.wiring import Services

__all__ = ["build_view", "publish_views"]

log = get_logger(__name__)

_STAGE = PipelineStage.PUBLISH.value


def _view_sources(view: ViewConfig, sources: Collection[Source]) -> list[Source]:
    """The enabled, weighted sources a view shows: those with any of its tags, or all."""
    wanted = set(view.tags or ())
    return [
        s
        for s in sources
        if s.enabled and s.weight > 0 and (view.tags is None or wanted.intersection(s.tags))
    ]


def build_view(
    view: ViewConfig,
    articles: Sequence[Article],
    sources: Collection[Source],
    *,
    now: datetime,
    run_id: str,
) -> list[dict[str, Any]]:
    """The items of one view from ``articles``, in its order, at most ``max_items``."""
    built = build_feed(
        articles,
        _view_sources(view, sources),
        now=now,
        run_id=run_id,
        window_hours=None,
        limit=len(articles),
    )
    times = {a.id: effective_time(a) for a in articles}
    items: list[dict[str, Any]] = built.feed["items"]
    if view.order == "newest":
        items.sort(key=lambda i: (-times[i["id"]].timestamp(), -i["score"], i["id"]))
    # "score": build_feed already orders items as feed.json does.
    return items[: view.max_items]


def _read(services: Services, view: ViewConfig, sources: Collection[Source]) -> list[Article]:
    """A tagged view's one read: its sources' newest ``max_items`` articles, or none."""
    ids = [s.id for s in _view_sources(view, sources)]
    if not ids:
        return []
    return services.repository.list_articles(source_ids=ids, limit=view.max_items)


def publish_views(
    services: Services,
    sources: Collection[Source],
    *,
    articles: Sequence[Article],
    run_id: str,
    now: datetime,
    generated_at: str,
) -> dict[str, str]:
    """Write every view in ``services.views``, and say how each went.

    ``articles`` is publish's read for ``feed.json``, used by a view without tags.
    Each view's outcome is ``written`` or ``failed``; a failed read or write is
    logged and keeps that view's previous file, and the others are still written.
    """
    outcomes: dict[str, str] = {}
    for name, view in services.views.views.items():
        try:
            reads = articles if view.tags is None else _read(services, view, sources)
            items = build_view(view, reads, sources, now=now, run_id=run_id)
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
        outcomes[name] = "written"
        log.info("view published", extra={"stage": _STAGE, "view": name, "items": len(items)})
    return outcomes
