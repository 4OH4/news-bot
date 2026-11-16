"""Read-time ranking of a ``score`` view: diversity and the A/B interleaving (ADR-0015).

The stored view file holds publish's rule order. At serve time two orders are built
from it: the rules order (stored ``score``) and the personal order (that times the
breakdown's ``similarity``, ADR-0014). Both get the same reader adjustments and the
same source-diversity rule, so an A/B comparison measures the learned part alone.
In ``ab`` mode the two are merged by team-draft interleaving, and each item carries
the ``arm`` that placed it, which the client sends back with feedback.

Everything here is pure: items are the view file's item dicts, never changed in place.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal

from newsfeed.models import RankingArm

__all__ = [
    "ItemAdjuster",
    "RankingMode",
    "arm_scores",
    "diversify",
    "no_adjustment",
    "rank_view",
    "team_draft_interleave",
]

Item = Mapping[str, Any]
RankingMode = Literal["ab", "rules", "personal"]

ItemAdjuster = Callable[[Item], tuple[bool, Mapping[str, float]]]
"""Reader preferences for one item: (hidden, extra score factors by name)."""

DEFAULT_DECAY = 0.85
SIMILARITY = "similarity"
DIVERSITY = "diversity"


def no_adjustment(item: Item) -> tuple[bool, Mapping[str, float]]:
    """The adjuster used until reader preferences exist: hides nothing, adds nothing."""
    return False, {}


def _breakdown(item: Item) -> Mapping[str, Any]:
    breakdown = item.get("score_breakdown")
    return breakdown if isinstance(breakdown, Mapping) else {}


def _number(value: Any, what: str) -> float:
    """``value`` as a finite float; a bool, string or NaN means a broken view file."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{what} is not a number: {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{what} is not finite: {value!r}")
    return number


def arm_scores(item: Item, factors: Mapping[str, float]) -> tuple[float, float]:
    """(rules, personal) scores: ``score`` times the factors, and that times ``similarity``.

    An item without ``similarity`` (no embedding yet) scores the same on both sides.
    """
    rules = _number(item.get("score"), "score")
    for name, factor in factors.items():
        rules *= _number(factor, name)
    similarity = _number(_breakdown(item).get(SIMILARITY, 1.0), SIMILARITY)
    return rules, rules * similarity


def diversify(
    items: Sequence[Item], scores: Sequence[float], decay: float = DEFAULT_DECAY
) -> list[tuple[Item, float, float]]:
    """Order ``items`` greedily by score, damping repeats of one ``source_id``.

    The k-th item placed from a source already used k times has its score multiplied
    by ``decay ** k``. Returns (item, adjusted score, diversity factor) in placed
    order. Equal adjusted scores keep the input order.
    """
    if len(items) != len(scores):
        raise ValueError("items and scores differ in length")
    # Within one source every remaining item has the same factor, so only the best
    # remaining item of each source can be next: compare source heads, not all items.
    queues: dict[Any, list[int]] = {}
    for index in sorted(range(len(items)), key=lambda i: (-scores[i], i)):
        queues.setdefault(items[index].get("source_id"), []).append(index)
    heads = dict.fromkeys(queues, 0)
    placed: list[tuple[Item, float, float]] = []
    while len(placed) < len(items):
        best: tuple[float, int, Any, float] | None = None
        for source, queue in queues.items():
            used = heads[source]
            if used == len(queue):
                continue
            index = queue[used]
            factor = decay**used
            adjusted = scores[index] * factor
            if best is None or adjusted > best[0] or (adjusted == best[0] and index < best[1]):
                best = (adjusted, index, source, factor)
        assert best is not None  # some queue has items while fewer than all are placed
        adjusted, index, source, factor = best
        heads[source] += 1
        placed.append((items[index], adjusted, factor))
    return placed


def _rules_first(seed: str, round_: int) -> bool:
    """The round's coin: whether the rules side picks first."""
    return hashlib.sha256(f"{seed}:{round_}".encode()).digest()[0] < 128


def team_draft_interleave(
    rules: Sequence[Item], personal: Sequence[Item], seed: str
) -> list[tuple[Item, RankingArm]]:
    """Merge two orders of the same items by team-draft interleaving (ADR-0015).

    Each round a coin seeded by ``sha256(f"{seed}:{round}")`` picks which side goes
    first; each side then adds its highest-ranked item not yet in the list. Items are
    matched by ``id``. It ends when both sides are used up; a side with nothing left
    simply skips its turn.
    """
    sides = {RankingArm.RULES: rules, RankingArm.PERSONAL: personal}
    positions = dict.fromkeys(sides, 0)
    seen: set[Any] = set()
    merged: list[tuple[Item, RankingArm]] = []

    def next_unplaced(arm: RankingArm) -> Item | None:
        order = sides[arm]
        while positions[arm] < len(order) and order[positions[arm]].get("id") in seen:
            positions[arm] += 1
        return order[positions[arm]] if positions[arm] < len(order) else None

    round_ = 0
    while any(next_unplaced(arm) is not None for arm in sides):
        first, second = (
            (RankingArm.RULES, RankingArm.PERSONAL)
            if _rules_first(seed, round_)
            else (RankingArm.PERSONAL, RankingArm.RULES)
        )
        for arm in (first, second):
            pick = next_unplaced(arm)
            if pick is not None:
                seen.add(pick.get("id"))
                merged.append((pick, arm))
        round_ += 1
    return merged


def _served(
    item: Item, score: float, factor: float, factors: Mapping[str, float], arm: RankingArm | None
) -> dict[str, Any]:
    """A copy of ``item`` as served: the placing order's score, explained in the breakdown."""
    breakdown = dict(_breakdown(item))
    breakdown.update(factors)
    if factor < 1:
        breakdown[DIVERSITY] = factor
    served = dict(item)
    served["score"] = score
    served["score_breakdown"] = breakdown
    if arm is not None:
        served["arm"] = arm.value
    return served


def rank_view(
    items: Sequence[Item],
    *,
    mode: RankingMode,
    seed: str,
    adjust: ItemAdjuster = no_adjustment,
) -> list[dict[str, Any]]:
    """Rank a ``score`` view's items for serving.

    Drops items ``adjust`` hides, scores both orders, diversifies each, and returns
    copies in the order ``mode`` asks for. In ``ab`` mode each item gains ``arm``,
    unless no item has ``similarity``: then both orders are the same by construction,
    so the rules order is served without arms rather than crediting a coin toss.

    Raises:
        TypeError, ValueError: an item's score or a factor is not a finite number.
    """
    kept: list[Item] = []
    factors: list[Mapping[str, float]] = []
    for item in items:
        hidden, extra = adjust(item)
        if not hidden:
            kept.append(item)
            factors.append(extra)
    scored = [arm_scores(item, extra) for item, extra in zip(kept, factors, strict=True)]
    extras = {id(item): extra for item, extra in zip(kept, factors, strict=True)}

    def ordered(side: int) -> list[tuple[Item, float, float]]:
        return diversify(kept, [pair[side] for pair in scored])

    if mode == "ab" and not any(SIMILARITY in _breakdown(item) for item in kept):
        mode = "rules"
    if mode != "ab":
        return [
            _served(item, score, factor, extras[id(item)], None)
            for item, score, factor in ordered(0 if mode == "rules" else 1)
        ]

    by_arm = {RankingArm.RULES: ordered(0), RankingArm.PERSONAL: ordered(1)}
    placed = {
        arm: {id(item): (score, factor) for item, score, factor in order}
        for arm, order in by_arm.items()
    }
    merged = team_draft_interleave(
        [entry[0] for entry in by_arm[RankingArm.RULES]],
        [entry[0] for entry in by_arm[RankingArm.PERSONAL]],
        seed,
    )
    return [_served(item, *placed[arm][id(item)], extras[id(item)], arm) for item, arm in merged]
