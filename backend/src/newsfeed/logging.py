"""Structured JSON logging for the pipeline.

Every record is emitted as one JSON object per line on stderr, which Cloud Run
and Cloud Logging parse natively. Context is attached with the stdlib ``extra``
argument, e.g. ``log.info("fetched", extra={"source_id": "bbc", "url": url})``.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

# Attributes every LogRecord has; anything else on a record came from ``extra``.
_RESERVED = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys() | {"message", "asctime"}
)

_ROOT = "newsfeed"


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        """Render ``record`` as JSON, including any ``extra`` context fields."""
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            # "severity" is the field name Cloud Logging maps to log level.
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _RESERVED and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


def _configure_root() -> logging.Logger:
    root = logging.getLogger(_ROOT)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        # Keep our records out of any handlers configured on the global root logger.
        root.propagate = False
    return root


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the ``newsfeed`` hierarchy that emits JSON lines."""
    _configure_root()
    if name != _ROOT and not name.startswith(f"{_ROOT}."):
        name = f"{_ROOT}.{name}"
    return logging.getLogger(name)
