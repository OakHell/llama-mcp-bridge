"""Structured text and JSON logging configuration for llama-mcp-bridge."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "JsonLogFormatter",
    "configure_logging",
]

_RESERVED_ATTRS = frozenset({
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
})


class JsonLogFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", record.funcName or "log"),
            "message": record.getMessage(),
        }
        for key, val in record.__dict__.items():
            if key not in _RESERVED_ATTRS and not key.startswith("_") and key != "event":
                payload[key] = val
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "WARNING", log_format: str = "text") -> None:
    """Configure root logging with either human-readable text or single-line JSON output."""
    lvl = getattr(logging, level.upper(), logging.WARNING)
    root = logging.getLogger()
    root.setLevel(lvl)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setLevel(lvl)
    if log_format.strip().lower() == "json":
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root.addHandler(handler)
