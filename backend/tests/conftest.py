"""Shared test fixtures."""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest


@pytest.fixture
def log_records() -> Iterator[list[logging.LogRecord]]:
    """Capture records from the ``newsfeed`` logger, which doesn't propagate to root."""
    captured: list[logging.LogRecord] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = _Handler(level=logging.DEBUG)
    logger = logging.getLogger("newsfeed")
    logger.addHandler(handler)
    try:
        yield captured
    finally:
        logger.removeHandler(handler)
