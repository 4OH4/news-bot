"""Tests for ``newsfeed.ingest.dedupe``. All articles are invented."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from newsfeed.ingest.dedupe import dedupe
from newsfeed.models import Article, article_id

FETCHED = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)

A = "https://example.com/a"
B = "https://example.com/b"
C = "https://example.com/c"


def make_article(
    canonical_url: str, source_id: str = "example", published_at: datetime | None = None
) -> Article:
    """Build an article; ``source_id`` tells duplicates apart in assertions."""
    return Article(
        canonical_url=canonical_url,
        url=canonical_url,
        source_id=source_id,
        title=f"Invented headline from {source_id}",
        published_at=published_at,
        fetched_at=FETCHED,
    )


def at(hour: int) -> datetime:
    """A UTC time on a fixed invented day."""
    return datetime(2026, 10, 11, hour, 0, tzinfo=UTC)


def test_empty_batch() -> None:
    assert dedupe([], set()) == []


def test_unique_articles_are_kept_in_order() -> None:
    articles = [make_article(B), make_article(A), make_article(C)]
    assert dedupe(articles, set()) == articles


def test_existing_ids_are_dropped() -> None:
    a, b = make_article(A), make_article(B)
    assert dedupe([a, b], {article_id(A)}) == [b]


def test_accepts_any_iterable_and_collection() -> None:
    a, b = make_article(A), make_article(B)
    assert dedupe(iter([a, b]), [article_id(B)]) == [a]
    assert dedupe((a, b), frozenset({article_id(A), article_id(B)})) == []


def test_earliest_published_occurrence_wins() -> None:
    late = make_article(A, "late", at(11))
    early = make_article(A, "early", at(8))
    middle = make_article(A, "middle", at(9))
    assert dedupe([late, early, middle], set()) == [early]


def test_published_times_with_offsets_compare_as_instants() -> None:
    # 09:30+02:00 is 07:30 UTC, so it is earlier than 08:00 UTC.
    offset = make_article(
        A, "offset", datetime(2026, 10, 11, 9, 30, tzinfo=timezone(timedelta(hours=2)))
    )
    utc = make_article(A, "utc", at(8))
    assert dedupe([utc, offset], set()) == [offset]


def test_dated_beats_undated() -> None:
    undated = make_article(A, "undated")
    dated = make_article(A, "dated", at(10))
    assert dedupe([undated, dated], set()) == [dated]
    assert dedupe([dated, undated], set()) == [dated]


def test_ties_keep_the_first_occurrence() -> None:
    first, second = make_article(A, "first", at(9)), make_article(A, "second", at(9))
    assert dedupe([first, second], set()) == [first]

    undated_first, undated_second = make_article(A, "first"), make_article(A, "second")
    assert dedupe([undated_first, undated_second], set()) == [undated_first]


def test_output_follows_first_appearance_of_each_id() -> None:
    b_late = make_article(B, "b-late", at(12))
    a = make_article(A, "a", at(10))
    b_early = make_article(B, "b-early", at(7))
    c = make_article(C, "c", at(9))
    assert dedupe([b_late, a, b_early, c], set()) == [b_early, a, c]


def test_existing_ids_and_batch_duplicates_together() -> None:
    a1, a2 = make_article(A, "a1", at(9)), make_article(A, "a2", at(8))
    b1, b2 = make_article(B, "b1", at(9)), make_article(B, "b2", at(8))
    assert dedupe([a1, b1, a2, b2], {article_id(A)}) == [b2]
