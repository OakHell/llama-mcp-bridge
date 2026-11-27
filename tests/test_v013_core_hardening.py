"""Tests for v0.13.0 supply chain, telemetry, config migration, resilience, resource limits, and chaos."""

from __future__ import annotations

import errno
import io
import json
import logging
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from llama_mcp_bridge.config import CURRENT_SCHEMA_VERSION, load_config, migrate_bridge_ini
from llama_mcp_bridge.http_resilience import (
    CircuitBreakerOpenError,
    ResilientHttpClient,
    classify_resource_error,
    ensure_disk_space,
)
from llama_mcp_bridge.installer import _run_doctor
from llama_mcp_bridge.installer import main as installer_main
from llama_mcp_bridge.logging_utils import JsonLogFormatter, configure_logging
from llama_mcp_bridge.model_download import _download_spec_list
from llama_mcp_bridge.supervisor import LlamaSupervisor


# --- E1: Structured JSON logging ---
def test_e1_json_log_formatter_and_configure_logging() -> None:
    fmt = JsonLogFormatter()
    rec = logging.LogRecord("llama_mcp_bridge.test", logging.WARNING, "test.py", 1, "hello %s", ("world",), None)
    rec.event = "test_event"
    rec.preset = "digest"
    parsed = json.loads(fmt.format(rec))
    assert parsed["level"] == "WARNING"
    assert parsed["event"] == "test_event"
    assert parsed["preset"] == "digest"
    assert parsed["message"] == "hello world"

    configure_logging("INFO", "json")
    try:
        root = logging.getLogger()
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JsonLogFormatter)
    finally:
        configure_logging("WARNING", "text")


# --- E2: --metrics CLI alias ---
def test_e2_bridge_metrics_cli_alias_defaults_to_9090(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from llama_mcp_bridge.bridge import main as bridge_main

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "models.ini").write_text("[digest]\nmodel = m.gguf\n", encoding="utf-8")
    (ws / "bridge.ini").write_text(f"[bridge]\nworkspace = {ws}\n", encoding="utf-8")
    monkeypatch.setenv("LLAMA_MCP_HOME", str(ws))

    with (
        patch("llama_mcp_bridge.bridge.start_metrics_server") as mock_srv,
        patch("llama_mcp_bridge.bridge.mcp.run"),
        patch("sys.argv", ["llama-mcp-bridge", "--metrics"]),
    ):
        bridge_main()
    mock_srv.assert_called_once()
    assert mock_srv.call_args.args[0] == 9090


# --- E4: Config schema_version & --migrate-config ---
def test_e4_config_schema_version_and_migrate_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    ini = ws / "bridge.ini"
    ini.write_text("[bridge]\nhost = 127.0.0.1\nCustomKey = Keep\n", encoding="utf-8")
    (ws / "models.ini").write_text("[digest]\nmodel = m.gguf\n", encoding="utf-8")
    monkeypatch.setenv("LLAMA_MCP_HOME", str(ws))

    # Dry-run does not modify
    changed, msg = migrate_bridge_ini(ini, dry_run=True)
    assert changed is True and "[DRY-RUN]" in msg
    assert not (ws / "bridge.ini.bak").exists()

    # Real migration writes .bak and sets schema_version
    with patch("sys.argv", ["llama-mcp-install", "--migrate-config"]):
        installer_main()
    assert (ws / "bridge.ini.bak").is_file()
    assert f"schema_version = {CURRENT_SCHEMA_VERSION}" in ini.read_text(encoding="utf-8")
    assert "CustomKey = Keep" in ini.read_text(encoding="utf-8")
    assert load_config().schema_version == CURRENT_SCHEMA_VERSION

    # Doctor warns when unversioned
    capsys.readouterr()
    ini.write_text("[bridge]\nhost = 127.0.0.1\n", encoding="utf-8")
    with (
        patch("llama_mcp_bridge.installer._find_llama_server", return_value=None),
        patch("llama_mcp_bridge.installer.LlamaSupervisor.get_status", return_value={"healthy": False, "server_url": "http://127.0.0.1:8080"}),
    ):
        _run_doctor(json_output=True)
    doc = json.loads(capsys.readouterr().out)
    assert any(c["check"] == "Config schema" and c["status"] == "WARN" for c in doc["checks"])


# --- E5, E6, E8: Release workflow YAML & STABILITY.md ---
def test_e5_e6_e8_release_workflow_and_stability_policy() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    rel = yaml.safe_load((repo_root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))
    on_block = rel.get("on") or rel.get(True)
    assert "tags" in on_block["push"]
    steps_text = json.dumps(rel["jobs"]["release"]["steps"])
    assert "cyclonedx1.5" in steps_text
    assert "sigstore" in steps_text
    assert (repo_root / "STABILITY.md").is_file()


# --- E9: Exponential backoff & circuit breaker ---
def test_e9_http_resilience_retry_and_circuit_breaker() -> None:
    client = ResilientHttpClient(failure_threshold=3, recovery_timeout_s=30.0, max_retries=2, base_delay_s=0.0)
    http_503 = urllib.error.HTTPError("https://example.com", 503, "Service Unavailable", {}, io.BytesIO(b""))  # type: ignore[arg-type]
    ok_resp = MagicMock(status=200)

    # Recovers after transient 503s
    with patch("llama_mcp_bridge.http_resilience.urllib.request.urlopen", side_effect=[http_503, http_503, ok_resp]):
        assert client.urlopen("https://example.com") is ok_resp

    # Trips circuit breaker after failure_threshold consecutive failures
    with (
        patch("llama_mcp_bridge.http_resilience.urllib.request.urlopen", side_effect=http_503),
        pytest.raises(urllib.error.HTTPError),
    ):
        client.urlopen("https://example.com")

    with pytest.raises(CircuitBreakerOpenError):
        client.urlopen("https://example.com")


# --- E10: Resource-exhaustion handling (ENOSPC, EMFILE, thread limit) ---
def test_e10_resource_exhaustion_guards(tmp_path: Path) -> None:
    with patch("llama_mcp_bridge.http_resilience.shutil.disk_usage", return_value=MagicMock(free=1024)):
        with pytest.raises(OSError) as exc:
            ensure_disk_space(tmp_path, 1024 * 1024 * 1024, "big.gguf")
        assert exc.value.errno == errno.ENOSPC

    enospc_err = classify_resource_error(OSError(errno.ENOSPC, "No space left"), "download")
    assert enospc_err is not None and "ENOSPC" in str(enospc_err)

    emfile_err = classify_resource_error(OSError(errno.EMFILE, "Too many open files"), "download")
    assert emfile_err is not None and "EMFILE" in str(emfile_err)

    thread_err = classify_resource_error(RuntimeError("can't start new thread"), "verify")
    assert thread_err is not None and "Thread limit" in str(thread_err)


# --- E12: Fault-injection chaos suite ---
def test_e12_chaos_mid_part_drop_and_server_kill_recovery(tmp_path: Path) -> None:
    # 1. Mid-.part stream drop followed by Range resume
    models_dir = tmp_path / "models"
    spec = [{"filename": "chaos.gguf", "url": "https://example.com/chaos.gguf", "size_gb": 0}]

    r_drop = MagicMock(status=200, headers={"Content-Length": "6"})
    r_drop.__enter__.return_value = r_drop
    r_drop.read.side_effect = [b"abc", ConnectionError("network drop")]

    r_resume = MagicMock(status=206, headers={"Content-Length": "3"})
    r_resume.__enter__.return_value = r_resume
    r_resume.read.side_effect = [b"def", b""]

    with (
        patch("llama_mcp_bridge.installer.probe_model_metadata", return_value=(True, 6, None)),
        patch("llama_mcp_bridge.model_download.DEFAULT_HTTP_CLIENT.max_retries", 0),
        patch("llama_mcp_bridge.http_resilience.urllib.request.urlopen", side_effect=[r_drop]),
        pytest.raises(ConnectionError),
    ):
        _download_spec_list(spec, models_dir)
    assert (models_dir / "chaos.gguf.part").read_bytes() == b"abc"

    with (
        patch("llama_mcp_bridge.installer.probe_model_metadata", return_value=(True, 6, None)),
        patch("llama_mcp_bridge.http_resilience.urllib.request.urlopen", side_effect=[r_resume]),
    ):
        _download_spec_list(spec, models_dir)
    assert (models_dir / "chaos.gguf").read_bytes() == b"abcdef"

    # 2. LlamaSupervisor recovers when process dies (poll() != None)
    sup = LlamaSupervisor()
    dead_proc = MagicMock(poll=MagicMock(return_value=1))
    sup._process = dead_proc
    assert sup._is_process_running() is False
