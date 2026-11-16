"""``ranking`` in ``config/views.yaml`` (#326)."""

from __future__ import annotations

from pathlib import Path

import pytest

from newsfeed.config import ConfigError, load_views

VIEWS_PATH = Path(__file__).parents[2] / "config" / "views.yaml"


def load(tmp_path: Path, body: str) -> dict[str, str]:
    path = tmp_path / "views.yaml"
    path.write_text("views:\n  v:\n" + body)
    return {name: view.ranking for name, view in load_views(path).views.items()}  # type: ignore[attr-defined]


def test_the_feed_view_is_set_to_ab_and_the_others_to_rules() -> None:
    views = load_views(VIEWS_PATH).views

    assert {name: view.ranking for name, view in views.items()} == {
        "news": "rules",
        "feed": "ab",
        "longer": "rules",
    }


def test_ranking_defaults_to_rules_on_a_score_view(tmp_path: Path) -> None:
    assert load(tmp_path, "    order: score\n    max_items: 5\n") == {"v": "rules"}


def test_ranking_ab_is_accepted_on_a_score_view(tmp_path: Path) -> None:
    assert load(tmp_path, "    order: score\n    max_items: 5\n    ranking: ab\n") == {"v": "ab"}


@pytest.mark.parametrize(
    "body",
    [
        "    order: newest\n    max_items: 5\n    ranking: ab\n",
        "    order: score\n    max_items: 5\n    ranking: personal\n",
    ],
)
def test_ranking_is_rejected_on_a_newest_view_and_for_other_values(
    tmp_path: Path, body: str
) -> None:
    with pytest.raises(ConfigError):
        load(tmp_path, body)
