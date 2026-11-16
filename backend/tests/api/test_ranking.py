"""``newsfeed.api.ranking``: read-time scoring, diversity and team-draft interleaving (#326).

Items are the view file's item dicts. Only the keys the ranking reads are set.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import pytest

from newsfeed.api.ranking import (
    arm_scores,
    diversify,
    no_adjustment,
    rank_view,
    team_draft_interleave,
)

SEEDS = [f"run-{n}:feed" for n in range(60)]


def item(
    ident: str, score: float = 1.0, similarity: float | None = None, source: str | None = None
) -> dict[str, Any]:
    breakdown: dict[str, float] = {"source": 1.0}
    if similarity is not None:
        breakdown["similarity"] = similarity
    return {
        "id": ident,
        "source_id": source or f"src-{ident}",
        "title": f"Story {ident}",
        "score": score,
        "score_breakdown": breakdown,
    }


def ids(ranked: Any) -> list[str]:
    return [entry["id"] if isinstance(entry, Mapping) else entry[0]["id"] for entry in ranked]


def hide_source_x(it: Mapping[str, Any]) -> tuple[bool, Mapping[str, float]]:
    return it["source_id"] == "x", {}


# --- arm_scores / no_adjustment -------------------------------------------------------


def test_no_adjustment_hides_nothing_and_adds_no_factors() -> None:
    assert no_adjustment(item("a")) == (False, {})


def test_arm_scores_are_score_times_factors_and_that_times_similarity() -> None:
    rules, personal = arm_scores(item("a", score=4.0, similarity=0.5), {"topic": 0.5, "mute": 2.0})

    assert rules == pytest.approx(4.0)
    assert personal == pytest.approx(2.0)


def test_arm_scores_treat_a_missing_similarity_as_one() -> None:
    assert arm_scores(item("a", score=3.0), {}) == (pytest.approx(3.0), pytest.approx(3.0))


# --- diversify ------------------------------------------------------------------------


def test_four_equal_items_from_one_source_get_decaying_diversity_factors() -> None:
    items = [item(f"a{n}", source="one") for n in range(4)]

    placed = diversify(items, [1.0] * 4)

    assert [f for _, _, f in placed] == pytest.approx([1.0, 0.85, 0.7225, 0.614125])
    assert [s for _, s, _ in placed] == pytest.approx([1.0, 0.85, 0.7225, 0.614125])


def test_diversify_lets_another_source_overtake_a_repeating_one() -> None:
    items = [item("a1", source="one"), item("a2", source="one"), item("b1", source="two")]

    placed = diversify(items, [10.0, 9.5, 9.0])

    # a2 falls to 9.5 * 0.85 = 8.075, below b1's 9.0.
    assert ids(placed) == ["a1", "b1", "a2"]


def test_diversify_with_decay_one_only_sorts_by_score() -> None:
    items = [item("a", source="one"), item("b", source="one")]

    placed = diversify(items, [1.0, 2.0], decay=1.0)

    assert ids(placed) == ["b", "a"]
    assert [f for _, _, f in placed] == [1.0, 1.0]


# --- team_draft_interleave ------------------------------------------------------------

RULES = [item(f"R{n}") for n in range(1, 5)]
PERSONAL = [RULES[2], RULES[0], RULES[3], RULES[1]]  # R3, R1, R4, R2


def interleave(seed: str) -> list[tuple[str, str]]:
    return [(it["id"], str(arm)) for it, arm in team_draft_interleave(RULES, PERSONAL, seed)]


def first_picker(seed: str) -> str:
    return interleave(seed)[0][1]


def test_when_rules_picks_first_the_list_starts_r1_then_r3_and_holds_each_item_once() -> None:
    seed = next(s for s in SEEDS if first_picker(s) == "rules")

    result = interleave(seed)

    assert result[:2] == [("R1", "rules"), ("R3", "personal")]
    assert sorted(i for i, _ in result) == ["R1", "R2", "R3", "R4"]


def test_when_personal_picks_first_the_list_starts_r3_then_r1() -> None:
    seed = next(s for s in SEEDS if first_picker(s) == "personal")

    assert interleave(seed)[:2] == [("R3", "personal"), ("R1", "rules")]


def test_the_coin_is_fair_across_seeds() -> None:
    firsts = {first_picker(s) for s in SEEDS}

    assert firsts == {"rules", "personal"}


@pytest.mark.parametrize("seed", SEEDS[:10])
def test_each_pick_is_the_best_unplaced_item_of_the_side_that_placed_it(seed: str) -> None:
    placed: set[str] = set()
    for ident, arm in interleave(seed):
        order = RULES if arm == "rules" else PERSONAL
        assert ident == next(it["id"] for it in order if it["id"] not in placed)
        placed.add(ident)


def test_the_same_seed_gives_the_same_interleaving_and_another_can_differ() -> None:
    assert interleave(SEEDS[0]) == interleave(SEEDS[0])
    assert len({tuple(interleave(s)) for s in SEEDS}) > 1


def test_lists_with_the_same_items_end_when_both_are_used_up() -> None:
    big_rules = [item(f"R{n}") for n in range(30)]
    big_personal = list(reversed(big_rules))

    result = team_draft_interleave(big_rules, big_personal, "seed")

    assert sorted(it["id"] for it, _ in result) == sorted(it["id"] for it in big_rules)


# --- rank_view ------------------------------------------------------------------------


def view_items() -> list[dict[str, Any]]:
    """Rules order a, b, c, d; personal order c, a, d, b (distinct sources, so no diversity)."""
    return [
        item("a", score=8.0, similarity=0.9),
        item("b", score=6.0, similarity=0.5),
        item("c", score=5.0, similarity=1.5),
        item("d", score=4.0, similarity=1.2),
    ]


def test_rules_mode_serves_the_stored_order_with_no_arm() -> None:
    ranked = rank_view(view_items(), mode="rules", seed="s")

    assert ids(ranked) == ["a", "b", "c", "d"]
    assert all("arm" not in r for r in ranked)
    assert [r["score"] for r in ranked] == pytest.approx([8.0, 6.0, 5.0, 4.0])


def test_personal_mode_serves_score_times_similarity_with_no_arm() -> None:
    ranked = rank_view(view_items(), mode="personal", seed="s")

    # a 7.2, b 3.0, c 7.5, d 4.8
    assert ids(ranked) == ["c", "a", "d", "b"]
    assert all("arm" not in r for r in ranked)
    assert [r["score"] for r in ranked] == pytest.approx([7.5, 7.2, 4.8, 3.0])


def test_ab_mode_marks_every_item_with_the_arm_that_placed_it_and_scores_it_that_way() -> None:
    ranked = rank_view(view_items(), mode="ab", seed="s")

    assert sorted(ids(ranked)) == ["a", "b", "c", "d"]
    rules_score = {"a": 8.0, "b": 6.0, "c": 5.0, "d": 4.0}
    personal_score = {"a": 7.2, "b": 3.0, "c": 7.5, "d": 4.8}
    for r in ranked:
        assert r["arm"] in ("rules", "personal")
        expected = rules_score if r["arm"] == "rules" else personal_score
        assert r["score"] == pytest.approx(expected[r["id"]])


def test_ab_mode_is_deterministic_for_a_seed() -> None:
    assert rank_view(view_items(), mode="ab", seed="s") == rank_view(
        view_items(), mode="ab", seed="s"
    )


def test_ab_with_similarity_one_everywhere_has_equal_orders_and_still_marks_arms() -> None:
    items = [item(f"i{n}", score=10.0 - n, similarity=1.0) for n in range(5)]

    ranked = rank_view(items, mode="ab", seed="s")

    assert ids(ranked) == [f"i{n}" for n in range(5)]
    assert all(r["arm"] in ("rules", "personal") for r in ranked)


def test_ab_with_no_similarity_anywhere_serves_the_rules_order_without_arms() -> None:
    items = [item(f"i{n}", score=10.0 - n) for n in range(5)]

    ranked = rank_view(items, mode="ab", seed="s")

    assert ids(ranked) == [f"i{n}" for n in range(5)]
    assert all("arm" not in r for r in ranked)


def test_one_item_with_similarity_is_enough_to_run_the_comparison() -> None:
    items = [item("a", score=2.0), item("b", score=1.0, similarity=1.0)]

    assert all("arm" in r for r in rank_view(items, mode="ab", seed="s"))


@pytest.mark.parametrize("mode", ["ab", "rules", "personal"])
def test_items_the_adjuster_hides_are_never_served(
    mode: Literal["ab", "rules", "personal"],
) -> None:
    items = [*view_items(), item("hidden", score=99.0, similarity=2.0, source="x")]

    ranked = rank_view(items, mode=mode, seed="s", adjust=hide_source_x)

    assert "hidden" not in ids(ranked)
    assert sorted(ids(ranked)) == ["a", "b", "c", "d"]


def test_adjuster_factors_scale_the_score_and_are_recorded_in_the_breakdown() -> None:
    def halve_b(it: Mapping[str, Any]) -> tuple[bool, Mapping[str, float]]:
        return False, ({"topic-pref": 0.5} if it["id"] == "b" else {})

    ranked = rank_view(view_items(), mode="rules", seed="s", adjust=halve_b)

    by_id = {r["id"]: r for r in ranked}
    assert by_id["b"]["score"] == pytest.approx(3.0)
    assert by_id["b"]["score_breakdown"]["topic-pref"] == 0.5
    assert "topic-pref" not in by_id["a"]["score_breakdown"]


def test_a_repeated_source_gets_a_diversity_entry_and_a_lower_score_in_every_order() -> None:
    items = [item(f"s{n}", score=10.0 - n, similarity=1.0, source="loud") for n in range(3)]

    modes: tuple[Literal["rules", "personal"], ...] = ("rules", "personal")
    for mode in modes:
        ranked = rank_view(items, mode=mode, seed="s")
        assert "diversity" not in ranked[0]["score_breakdown"]
        assert ranked[1]["score_breakdown"]["diversity"] == pytest.approx(0.85)
        assert ranked[2]["score_breakdown"]["diversity"] == pytest.approx(0.7225)
        assert ranked[1]["score"] == pytest.approx(9.0 * 0.85)


def test_rank_view_returns_copies_and_leaves_the_input_untouched() -> None:
    items = view_items()
    before = [dict(it, score_breakdown=dict(it["score_breakdown"])) for it in items]

    ranked = rank_view(items, mode="ab", seed="s")

    assert items == before
    assert all(r is not o for r in ranked for o in items)
