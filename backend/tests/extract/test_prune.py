"""Tests for ``strip_recent_articles``."""

from __future__ import annotations

from newsfeed.extract.prune import strip_recent_articles


def test_returns_pages_without_the_block_unchanged() -> None:
    html = "<html><body><p>Hello</p></body></html>"
    assert strip_recent_articles(html) is html


def test_removes_the_block_and_keeps_the_rest() -> None:
    html = (
        "<html><body><p>Post text</p>"
        '<div class="x recent-articles"><h2>Recent articles</h2><ul><li>Other</li></ul></div>'
        "<p>After</p></body></html>"
    )
    out = strip_recent_articles(html)
    assert "Other" not in out
    assert "Post text" in out
    assert "After" in out


def test_only_matches_whole_class_tokens() -> None:
    html = '<html><body><div class="not-recent-articles-x"><p>keep</p></div></body></html>'
    assert "keep" in strip_recent_articles(html)


def test_nested_matches_do_not_fail() -> None:
    html = (
        '<html><body><div class="recent-articles"><div class="recent-posts">x</div></div>'
        "<p>keep</p></body></html>"
    )
    out = strip_recent_articles(html)
    assert "keep" in out
    assert "recent-" not in out
