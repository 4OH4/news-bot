"""Further A/B summary checks: long records, per-day rows, likes, reads, projection (#329)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from newsfeed.inspect import ab_rows, ab_summary
from newsfeed.models import Feedback, FeedbackEvent, RankingArm
from tests.test_cli_inspect import data_dir  # noqa: F401  (fixture)
from tests.test_inspect_ab import NOW, PERSONAL, RULES, SINCE, fb, fixed_clock, opens, recent  # noqa: F401


def test_default_window_covers_a_long_record(data_dir: Path) -> None:  # noqa: F811
    from tests.test_cli_inspect import inspect, seed

    seed(data_dir, [], recent(130, 70, 40))
    result = inspect("ab", "--format", "jsonl")
    rows = [json.loads(line) for line in str(result.stdout).splitlines() if line]

    assert next(r for r in rows if r["row"] == "verdict")["note"] == "personalised ahead"


def test_days_won_day_rows_and_likes() -> None:
    events = [
        fb("a", FeedbackEvent.OPEN, PERSONAL, day=0),
        fb("b", FeedbackEvent.OPEN, PERSONAL, day=0),
        fb("c", FeedbackEvent.OPEN, RULES, day=0),
        fb("d", FeedbackEvent.OPEN, RULES, day=1),
        fb("e", FeedbackEvent.OPEN, RULES, day=1),
        fb("f", FeedbackEvent.OPEN, PERSONAL, day=1),
        fb("g", FeedbackEvent.OPEN, RULES, day=2),
        fb("h", FeedbackEvent.OPEN, PERSONAL, day=3),
        fb("i", FeedbackEvent.OPEN, PERSONAL, day=3),
        fb("a", FeedbackEvent.LIKE, PERSONAL),
        fb("a", FeedbackEvent.LIKE, PERSONAL, day=1),
        fb("b", FeedbackEvent.LIKE, RULES),
        fb("c", FeedbackEvent.LIKE, None),
    ]

    summary = ab_summary(events, now=NOW, since=SINCE)

    assert summary.days_won == (2, 2)
    assert summary.likes == {RankingArm.RULES: 1, RankingArm.PERSONAL: 1}
    day_rows = [r for r in ab_rows(summary) if r["row"].startswith("day ")]
    assert [(r["rules"], r["personal"]) for r in day_rows] == [(0, 2), (1, 0), (2, 1), (1, 2)]


def test_one_article_read_counts_once_across_sides() -> None:
    events = [
        fb("a", FeedbackEvent.READ, PERSONAL, value=60),
        fb("a", FeedbackEvent.READ, RULES, value=60, day=1),
    ]

    reads = ab_summary(events, now=NOW, since=SINCE).reads

    assert reads.personal + reads.rules == 1


def test_projection_waits_for_the_day_minimum() -> None:
    """Plenty of opens in 5 days: the open minimum is met, so the 21 days decide the date."""
    summary = ab_summary(opens(150, 100, 5), now=NOW, since=SINCE)

    assert summary.projected == NOW.date() + timedelta(days=16)


def test_future_dated_events_do_not_break_the_day_count() -> None:
    event = Feedback(
        article_id="x", event=FeedbackEvent.OPEN, arm=PERSONAL, created_at=NOW + timedelta(days=2)
    )

    summary = ab_summary([event], now=NOW, since=SINCE)

    assert summary.verdict == "collecting: 1 of 200 credited opens, 1 of 21 days"
