"""Tests for v0.14.0 opt-in [enterprise] extra (E3, E7, E11)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from llama_mcp_bridge.config import _CONFIG_CACHE, load_config
from llama_mcp_bridge.metrics import BridgeMetrics, start_metrics_server


def test_enterprise_gate_rejects_unflagged_usage_and_lazy_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default mode must reject enterprise keys and never import llama_mcp_bridge.enterprise."""
    _CONFIG_CACHE.clear()
    monkeypatch.delenv("LLAMA_MCP_ENTERPRISE", raising=False)
    monkeypatch.delenv("LLAMA_MCP_TENANT", raising=False)

    ini = tmp_path / "bridge.ini"
    ini.write_text("[bridge]\naudit_enabled = true\n", encoding="utf-8")
    with pytest.raises(ValueError, match="--enterprise"):
        load_config(tmp_path)

    ini.write_text("[bridge]\nenterprise_mode = true\naudit_enabled = true\n", encoding="utf-8")
    cfg = load_config(tmp_path)
    assert cfg.enterprise_mode is True
    assert cfg.audit_enabled is True


def test_e3_audit_logger_schema_rotation_and_no_raw_prompt(tmp_path: Path) -> None:
    """E3: AuditLogger writes structured JSONL, rotates at max_bytes, and omits raw prompts."""
    from llama_mcp_bridge.enterprise.audit import AuditLogger

    log_file = tmp_path / "logs" / "audit.jsonl"
    secret_prompt = "TOP_SECRET_PROMPT_PAYLOAD_DO_NOT_LOG"
    logger = AuditLogger(log_file, max_bytes=250, backup_count=2)
    try:
        for idx in range(5):
            entry = logger.record(
                request_id=f"req-{idx}",
                workspace=str(tmp_path),
                tenant="acme",
                tool="ask_digest",
                preset="digest",
                input_text=secret_prompt,
                files_accessed=["a.py"],
                duration_ms=12.34,
                status="ok",
                fallback_used=False,
            )
            assert entry["input_sha256"] == hashlib.sha256(secret_prompt.encode("utf-8")).hexdigest()
    finally:
        logger.close()

    assert log_file.exists()
    rotated = tmp_path / "logs" / "audit.jsonl.1"
    assert rotated.exists()
    raw_content = log_file.read_text(encoding="utf-8") + rotated.read_text(encoding="utf-8")
    assert secret_prompt not in raw_content
    first_record = json.loads(log_file.read_text(encoding="utf-8").splitlines()[0])
    for key in (
        "timestamp",
        "request_id",
        "workspace",
        "tenant",
        "tool",
        "preset",
        "input_sha256",
        "files_accessed",
        "duration_ms",
        "status",
        "fallback_used",
    ):
        assert key in first_record


def test_e7_livez_readyz_and_sd_notify(tmp_path: Path) -> None:
    """E7: /livez returns 200 on Standby, /readyz tracks health, and sd_notify emits datagrams."""
    from llama_mcp_bridge.enterprise.probes import sd_notify

    ready_state = {"ok": False, "reason": "standby"}
    server = start_metrics_server(
        0,
        BridgeMetrics(),
        enterprise_mode=True,
        readiness_check=lambda: (bool(ready_state["ok"]), str(ready_state["reason"])),
    )
    port = server.server_address[1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/livez", timeout=2) as resp:
            assert resp.status == 200
            assert json.loads(resp.read().decode("utf-8"))["status"] == "alive"

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz", timeout=2)
        assert exc_info.value.code == 503

        ready_state["ok"] = True
        ready_state["reason"] = "ok"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz", timeout=2) as resp:
            assert resp.status == 200
            assert json.loads(resp.read().decode("utf-8"))["status"] == "ready"
    finally:
        server.shutdown()
        server.server_close()

    if hasattr(socket, "AF_UNIX") and sys.platform != "win32":
        sock_dir = tempfile.mkdtemp(prefix="lmb-", dir="/tmp")
        sock_path = Path(sock_dir) / "n.sock"
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as srv:
                srv.bind(str(sock_path))
                srv.settimeout(2.0)
                assert sd_notify("READY=1", notify_socket=str(sock_path)) is True
                data = srv.recv(1024)
                assert data == b"READY=1"
        finally:
            shutil.rmtree(sock_dir, ignore_errors=True)


def test_e11_tenant_isolation_and_advisory_lock(tmp_path: Path) -> None:
    """E11: Per-tenant workspace uses 0o700 on POSIX and advisory_file_lock coordinates access."""
    from llama_mcp_bridge.enterprise.tenant import advisory_file_lock, resolve_tenant_workspace

    tenant_dir = resolve_tenant_workspace(tmp_path, "team-alpha")
    assert tenant_dir == tmp_path / "tenants" / "team-alpha"
    assert tenant_dir.is_dir()
    if os.name != "nt":
        assert (tenant_dir.stat().st_mode & 0o777) == 0o700

    with pytest.raises(ValueError, match="Invalid tenant name"):
        resolve_tenant_workspace(tmp_path, "../escape")

    if os.name != "nt":
        target_dir = tmp_path / "external_target"
        target_dir.mkdir()
        os.chmod(target_dir, 0o755)
        tenants_dir = tmp_path / "tenants"
        tenants_dir.mkdir(exist_ok=True)
        (tenants_dir / "sym-tenant").symlink_to(target_dir)
        with pytest.raises(ValueError, match="symlinked"):
            resolve_tenant_workspace(tmp_path, "sym-tenant")
        assert (target_dir.stat().st_mode & 0o777) == 0o755

    lock_file = tmp_path / "shared" / ".download.lock"
    with advisory_file_lock(lock_file):
        assert lock_file.exists()
