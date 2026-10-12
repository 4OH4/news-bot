"""Extraction evaluation: the URL set, page snapshots and (later) scoring.

The evaluation runs against saved page snapshots so results are deterministic
and sites aren't fetched repeatedly (PROJECT_PLAN.md §7). Snapshots are
third-party content, so they live outside the repo in ``NEWSFEED_FIXTURES_DIR``.
"""
