"""Tests for the A/B results summary, ``ab_summary`` and ``newsfeed inspect ab`` (issue #329).

The issue names ``ab_summary(events, *, now, since, ...)`` and, per event kind,
``rules``, ``personal`` and ``share_personal``. The tests assume those sit on
``summary.opens`` and ``summary.reads`` with ``p_value`` beside them, and that
``summary.verdict`` is the verdict string. Everything else is checked through
the command's output.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from newsfeed.inspect import ab_summary
from newsfeed.models import Feedback, FeedbackEvent, RankingArm
from tests.test_cli_inspect import data_dir, inspect, seed  # noqa: F401  (data_dir is a fixture)

NOW = datetime(2026, 12, 20, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=60)
PERSONAL, RULES = RankingArm.PERSONAL, RankingArm.RULES


def fb(
    article: str,
    event: FeedbackEvent,
    arm: RankingArm | None,
    day: int = 0,
    value: float | None = None,
) -> Feedback:
    return Feedback(
        article_id=article,
        event=event,
        value=value,
        arm=arm,
        created_at=NOW - timedelta(days=day, minutes=5),
    )


def opens(personal: int, rules: int, days: int) -> list[Feedback]:
    """Distinct-article credited opens, spread evenly over ``days`` consecutive days."""
    events = [(f"p{i}", PERSONAL) for i in range(personal)] + [
        (f"r{i}", RULES) for i in range(rules)
    ]
    return [fb(a, FeedbackEvent.OPEN, arm, day=i % days) for i, (a, arm) in enumerate(events)]


def summarise(events: list[Feedback], **kw: int) -> Any:
    return ab_summary(events, now=NOW, since=SINCE, **kw)


def test_counts_share_and_p_value_while_still_collecting() -> None:
    summary = summarise(opens(30, 20, 5))

    assert summary.opens.personal == 30
    assert summary.opens.rules == 20
    assert summary.opens.share_personal == pytest.approx(0.6)
    assert summary.opens.p_value == pytest.approx(0.203, abs=0.001)
    assert summary.verdict == "collecting: 50 of 200 credited opens, 5 of 21 days"


@pytest.mark.parametrize(
    ("personal", "rules", "verdict"),
    [
        (130, 70, "personalised ahead"),
        (70, 130, "rules ahead"),
        (105, 95, "no clear difference"),
    ],
)
def test_verdict_once_the_stopping_rule_is_met(personal: int, rules: int, verdict: str) -> None:
    assert summarise(opens(personal, rules, 22)).verdict == verdict


def test_enough_opens_but_too_few_days_is_still_collecting() -> None:
    assert summarise(opens(150, 60, 10)).verdict.startswith("collecting: 210 of 200")


def test_repeat_opens_of_one_article_count_once_per_arm() -> None:
    events = [fb("a1", FeedbackEvent.OPEN, PERSONAL, day=d) for d in range(3)]

    assert summarise(events).opens.personal == 1


def test_reads_need_the_threshold_seconds_and_an_arm() -> None:
    events = [
        fb("short", FeedbackEvent.READ, PERSONAL, value=12),
        fb("long", FeedbackEvent.READ, PERSONAL, value=45),
        fb("edge", FeedbackEvent.READ, RULES, value=30),
    ]

    summary = summarise(events)

    assert (summary.reads.personal, summary.reads.rules) == (1, 1)
    assert summarise(events, read_seconds=60).reads.personal == 0


def test_events_without_an_arm_are_ignored() -> None:
    events = [
        fb("a", FeedbackEvent.OPEN, None),
        fb("b", FeedbackEvent.READ, None, value=90),
        fb("c", FeedbackEvent.OPEN, RULES),
    ]

    summary = summarise(events)

    assert (summary.opens.personal, summary.opens.rules) == (0, 1)
    assert (summary.reads.personal, summary.reads.rules) == (0, 0)


# --- the command ---------------------------------------------------------------


def run_ab(data: Path, events: list[Feedback], *args: str) -> str:
    seed(data, [], events)
    result = inspect("ab", "--since", "90d", *args)
    assert result.exit_code == 0, result.output
    return result.stdout


def recent(personal: int, rules: int, days: int) -> list[Feedback]:
    """Credited opens ending today, with ids that must never be printed."""
    now = datetime.now(UTC)
    return [
        e.model_copy(
            update={"created_at": now - (NOW - e.created_at), "article_id": "SECRET" + e.article_id}
        )
        for e in opens(personal, rules, days)
    ]


def test_text_output_has_counts_p_value_and_verdict_but_no_article_ids(data_dir: Path) -> None:  # noqa: F811
    out = run_ab(data_dir, recent(30, 20, 5))

    assert "0.203" in out
    assert "collecting: 50 of 200 credited opens, 5 of 21 days" in out
    assert "SECRET" not in out


def test_reports_the_daily_rate_and_the_projected_date_of_the_stopping_rule(
    data_dir: Path,  # noqa: F811
) -> None:
    out = run_ab(data_dir, recent(20, 20, 10))  # 4 credited opens a day, 160 still to find

    expected = datetime.now(UTC).date() + timedelta(days=40)
    found = [date.fromisoformat(d) for d in re.findall(r"\d{4}-\d{2}-\d{2}", out)]
    assert any(abs((d - expected).days) <= 4 for d in found), out


@pytest.mark.parametrize("fmt", ["jsonl", "csv"])
def test_machine_formats_parse_and_hide_article_ids(data_dir: Path, fmt: str) -> None:  # noqa: F811
    out = run_ab(data_dir, recent(30, 20, 5), "--format", fmt)

    assert "SECRET" not in out
    if fmt == "jsonl":
        assert all(isinstance(json.loads(line), dict) for line in out.splitlines() if line)
    else:
        assert len(list(csv.reader(io.StringIO(out)))) > 1


def test_rejects_an_unknown_format(data_dir: Path) -> None:  # noqa: F811
    seed(data_dir, [], [])

    assert inspect("ab", "--format", "xml").exit_code != 0
