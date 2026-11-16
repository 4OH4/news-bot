"""Known values for the A/B statistics helpers (issue #329)."""

from __future__ import annotations

import pytest

from newsfeed.inspect import binomial_p_value, wilson_interval


@pytest.mark.parametrize(
    ("k", "n", "expected"),
    [(5, 10, 1.0), (30, 50, 0.2026), (0, 10, 2 / 1024), (9, 10, 22 / 1024), (3, 3, 0.25)],
)
def test_binomial_p_value_matches_known_values(k: int, n: int, expected: float) -> None:
    assert binomial_p_value(k, n) == pytest.approx(expected, abs=1e-4)


def test_empty_sample_has_no_p_value_or_interval() -> None:
    assert binomial_p_value(0, 0) is None
    assert wilson_interval(0, 0) is None


def test_wilson_interval_known_value() -> None:
    low, high = wilson_interval(30, 50) or (0, 0)

    assert (low, high) == (pytest.approx(0.462, abs=0.002), pytest.approx(0.724, abs=0.002))
