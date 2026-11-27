"""Rotating JSONL audit logger for MCP tool invocations (E3)."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

__all__ = ["AuditLogger"]


class AuditLogger:
    """Thread-safe rotating JSONL audit logger that excludes raw prompt payloads."""

    def __init__(
        self,
        log_path: Path | str,
        max_bytes: int = 10_485_760,
        backup_count: int = 5,
    ) -> None:
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._logger = logging.getLogger(f"llama_mcp_bridge.audit.{id(self)}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        self._handler = RotatingFileHandler(
            self.log_path,
            maxBytes=max(1, max_bytes),
            backupCount=max(1, backup_count),
            encoding="utf-8",
        )
        self._handler.setFormatter(logging.Formatter("%(message)s"))
        self._logger.addHandler(self._handler)

    def record(
        self,
        *,
        request_id: str,
        workspace: str,
        tenant: str,
        tool: str,
        preset: str,
        input_text: str,
        files_accessed: list[str] | tuple[str, ...] = (),
        duration_ms: float,
        status: str,
        fallback_used: bool = False,
    ) -> dict[str, Any]:
        """Write a single JSONL audit record without storing raw prompt text."""
        input_sha256 = hashlib.sha256(input_text.encode("utf-8", errors="replace")).hexdigest()
        entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "request_id": request_id,
            "workspace": workspace,
            "tenant": tenant,
            "tool": tool,
            "preset": preset,
            "input_sha256": input_sha256,
            "files_accessed": list(files_accessed),
            "duration_ms": round(duration_ms, 2),
            "status": status,
            "fallback_used": fallback_used,
        }
        self._logger.info(json.dumps(entry, sort_keys=True))
        return entry

    def close(self) -> None:
        """Flush and close the underlying RotatingFileHandler."""
        self._handler.flush()
        self._handler.close()
        self._logger.removeHandler(self._handler)
