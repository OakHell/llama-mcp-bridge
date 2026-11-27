"""Unit tests for enterprise/probes.py (B21) and enterprise/tenant.py (B22)."""

from __future__ import annotations

import builtins
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

from llama_mcp_bridge.enterprise.probes import check_readiness
from llama_mcp_bridge.enterprise.tenant import advisory_file_lock


def test_b21_check_readiness_catches_exceptions_and_returns_503() -> None:
    """B21: check_readiness must catch health_check() exceptions and return 503."""

    def _boom() -> tuple[bool, str]:
        raise RuntimeError("supervisor crashed")

    code, payload = check_readiness(_boom)
    assert code == 503
    assert payload["status"] == "error"
    assert "supervisor crashed" in payload["reason"]


def test_b22_advisory_file_lock_windows_initializes_empty_file(tmp_path: Path) -> None:
    """B22: On Windows, advisory_file_lock must ensure >=1 byte before msvcrt.locking."""
    lock_file = tmp_path / "empty.lock"
    fake_msvcrt = types.SimpleNamespace(LK_LOCK=1, LK_UNLCK=0, locking=MagicMock())
    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "msvcrt":
            return fake_msvcrt
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    with (
        patch("llama_mcp_bridge.enterprise.tenant.os.name", "nt"),
        patch("builtins.__import__", side_effect=_fake_import),
        advisory_file_lock(lock_file),
    ):
        assert lock_file.stat().st_size >= 1

    assert fake_msvcrt.locking.call_count == 2


def test_s9_audit_hashes_files_accessed_and_n14_enterprise_exports(tmp_path: Path) -> None:
    """S9: AuditLogger hashes files_accessed; N14: enterprise.__all__ exports submodules."""
    import llama_mcp_bridge.enterprise as ent
    from llama_mcp_bridge.enterprise.audit import AuditLogger

    assert hasattr(ent, "audit")
    assert hasattr(ent, "probes")
    assert hasattr(ent, "tenant")

    logger = AuditLogger(tmp_path / "audit.jsonl")
    try:
        entry = logger.record(
            request_id="r1",
            workspace=str(tmp_path),
            tenant="t1",
            tool="ask_digest",
            preset="digest",
            input_text="hello",
            files_accessed=["/secret/dir/file.py"],
            duration_ms=1.0,
            status="ok",
        )
    finally:
        logger.close()

    assert entry["files_accessed"] != ["/secret/dir/file.py"]
    assert len(entry["files_accessed"][0]) == 16
