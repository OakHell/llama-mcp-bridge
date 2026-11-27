"""Tests for v0.12.0 streaming, hot-reload, ask_batch, progress, auth, metrics, multi-workspace, warmup, and signing."""

from __future__ import annotations

import asyncio
import json
import threading
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llama_mcp_bridge.bridge import (
    _register_configured_tools,
    _start_progress_heartbeat,
    ask_batch,
    ask_digest,
    ask_preset,
    check_and_reload_models_ini,
    mcp,
    query_llama,
    run_warmup,
)
from llama_mcp_bridge.config import load_config
from llama_mcp_bridge.metrics import BridgeMetrics, start_metrics_server
from llama_mcp_bridge.paths import (
    list_named_workspaces,
    read_named_workspace,
    register_named_workspace,
    resolve_workspace,
)
from llama_mcp_bridge.signing import verify_release_signature
from llama_mcp_bridge.supervisor import LlamaSupervisor


def _setup_workspace(tmp_path: Path, bridge_extra: str = "", models_extra: str = "") -> Path:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "models.ini").write_text(
        "[*]\nparallel = 2\n\n[digest]\nmodel = m.gguf\n\n[coder]\nmodel = c.gguf\n" + models_extra,
        encoding="utf-8",
    )
    (ws / "bridge.ini").write_text(
        f"[bridge]\nworkspace = {ws}\nmodels_ini = {ws / 'models.ini'}\n" + bridge_extra,
        encoding="utf-8",
    )
    return ws


# --- F1: Optional SSE streaming + clean_mcp_output ---
def test_f1_streaming_sse_assembly_and_hygiene(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _setup_workspace(tmp_path)
    monkeypatch.setenv("LLAMA_MCP_HOME", str(ws))

    sse_lines = [
        "event: ping",
        "data: {not-valid-json}",
        'data: {"choices": [{"delta": {"content": "<think>plan</think>"}}]}',
        'data: {"choices": [{"delta": {"content": "Hello "}}]}',
        'data: {"choices": [{"delta": {"content": "stream!"}}]}',
        "data: [DONE]",
    ]
    mock_resp = MagicMock(status_code=200)
    mock_resp.iter_lines.return_value = iter(sse_lines)

    with (
        patch("llama_mcp_bridge.bridge.LlamaSupervisor.ensure_running", return_value=True),
        patch("llama_mcp_bridge.bridge.requests.post", return_value=mock_resp) as mock_post,
    ):
        out = ask_digest("say hi", stream=True)

    assert out == "Hello stream!"
    assert mock_post.call_args.kwargs["json"]["stream"] is True
    assert mock_post.call_args.kwargs["stream"] is True

    # HTTP 500 during streaming still classifies OOM/error cleanly
    err_resp = MagicMock(status_code=500, text="server error")
    with (
        patch("llama_mcp_bridge.bridge.LlamaSupervisor.ensure_running", return_value=True),
        patch("llama_mcp_bridge.bridge.LlamaSupervisor.get_stderr_tail", return_value="cudaMalloc failed: out of memory"),
        patch("llama_mcp_bridge.bridge.requests.post", return_value=err_resp),
    ):
        err_out = ask_digest("say hi", stream=True)
    assert "out of memory" in err_out.lower() or "oom" in err_out.lower()


# --- F2: Hot-reload models.ini + notifications/tools/list_changed ---
def test_f2_hot_reload_models_ini_emits_list_changed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _setup_workspace(tmp_path)
    monkeypatch.setenv("LLAMA_MCP_HOME", str(ws))
    _register_configured_tools(mcp)

    # Modify models.ini to add a new custom preset
    (ws / "models.ini").write_text(
        "[*]\nparallel = 1\n\n[digest]\nmodel = m.gguf\n\n[extra_tool]\nmodel = e.gguf\n",
        encoding="utf-8",
    )
    mock_session = MagicMock()
    mock_session.send_tool_list_changed = MagicMock(return_value=None)
    mock_ctx = MagicMock(session=mock_session)

    reloaded = check_and_reload_models_ini(ctx=mock_ctx, target_mcp=mcp)
    assert reloaded is True
    mock_session.send_tool_list_changed.assert_called_once()
    tools = asyncio.run(mcp.list_tools())
    assert "ask_extra_tool" in {t.name for t in tools}


# --- F6: ask_batch bounded concurrency and per-item error isolation ---
def test_f6_ask_batch_parallel_and_error_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _setup_workspace(tmp_path)
    monkeypatch.setenv("LLAMA_MCP_HOME", str(ws))

    def _fake_query(model: str, system_prompt: str, prompt: str, *args: object, **kwargs: object) -> str:
        if "fail" in prompt:
            return "Error: simulated failure"
        return f"ok:{prompt}"

    with patch("llama_mcp_bridge.bridge.query_llama", side_effect=_fake_query):
        raw = asyncio.run(ask_batch("digest", ["p1", "fail_p2", "p3"]))
    data = json.loads(raw)
    assert data["preset"] == "digest"
    assert data["parallel"] == 2
    assert len(data["results"]) == 3
    assert data["results"][0] == {"index": 0, "ok": True, "output": "ok:p1"}
    assert data["results"][1]["ok"] is False
    assert data["results"][2] == {"index": 2, "ok": True, "output": "ok:p3"}


# --- F7: notifications/progress emission after threshold ---
def test_f7_progress_heartbeat_emits_after_threshold() -> None:
    calls: list[dict[str, object]] = []

    def _report(progress: float, total: float | None = None, message: str = "") -> None:
        calls.append({"progress": progress, "total": total, "message": message})

    ctx = MagicMock(report_progress=_report)
    stop_event = threading.Event()
    t = _start_progress_heartbeat(ctx, stop_event, threshold_s=0.01)
    assert t is not None
    t.join(timeout=0.5)
    stop_event.set()
    assert len(calls) >= 1
    assert calls[0]["progress"] == 10
    assert "Local inference running" in str(calls[0]["message"])


# --- F12: --api-key CLI flag & Authorization: Bearer header ---
def test_f12_api_key_spawn_and_bearer_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _setup_workspace(tmp_path, bridge_extra="api_key = secret-token-123\n")
    monkeypatch.setenv("LLAMA_MCP_HOME", str(ws))

    cfg = load_config()
    assert cfg.api_key == "secret-token-123"

    sup = LlamaSupervisor(cfg)
    mock_proc = MagicMock(pid=999, stderr=None)
    with patch("llama_mcp_bridge.supervisor.subprocess.Popen", return_value=mock_proc) as mock_popen:
        assert sup._start_server() is True
    cmd = mock_popen.call_args.args[0]
    assert "--api-key" in cmd
    assert "secret-token-123" in cmd

    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"choices": [{"message": {"content": "authed"}}]}
    with (
        patch("llama_mcp_bridge.bridge.LlamaSupervisor.ensure_running", return_value=True),
        patch("llama_mcp_bridge.bridge.requests.post", return_value=mock_resp) as mock_post,
    ):
        out = query_llama("digest", "", "hi")
    assert out == "authed"
    assert mock_post.call_args.kwargs["headers"] == {"Authorization": "Bearer secret-token-123"}


# --- F13: Prometheus /metrics sidecar endpoint ---
def test_f13_metrics_prometheus_scrape_endpoint() -> None:
    metrics = BridgeMetrics()
    metrics.record_request("digest", "ok", 0.42)
    metrics.record_model_load("digest")
    metrics.record_oom("coder")

    srv = start_metrics_server(0, metrics=metrics, host="127.0.0.1")
    port = srv.server_address[1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=3) as resp:
            body = resp.read().decode("utf-8")
        assert 'llama_mcp_requests_total{preset="digest",status="ok"} 1' in body
        assert 'llama_mcp_model_loads_total{preset="digest"} 1' in body
        assert 'llama_mcp_oom_events_total{preset="coder"} 1' in body
    finally:
        srv.shutdown()
        srv.server_close()


# --- F14: allowed_presets access control ---
def test_f14_allowed_presets_filtering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _setup_workspace(tmp_path, bridge_extra="allowed_presets = digest\n")
    monkeypatch.setenv("LLAMA_MCP_HOME", str(ws))

    registered = _register_configured_tools(mcp)
    assert "ask_digest" in registered
    assert "ask_coder" not in registered

    denied = ask_preset("coder", "hello")
    assert "Access denied" in denied


# --- F15: Named multi-workspace resolution ---
def test_f15_named_workspace_registration_and_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    ws_work = tmp_path / "work_ai"
    ws_work.mkdir()
    monkeypatch.delenv("LLAMA_MCP_HOME", raising=False)
    monkeypatch.delenv("LLAMA_MCP_WORKSPACE", raising=False)

    with patch("llama_mcp_bridge.paths.Path.home", return_value=fake_home):
        register_named_workspace("work", ws_work, platform_name="linux")
        assert read_named_workspace("work", platform_name="linux") == ws_work.resolve()
        assert list_named_workspaces(platform_name="linux") == {"work": ws_work.resolve()}
        assert resolve_workspace(platform_name="linux", workspace_name="work") == ws_work.resolve()
        assert resolve_workspace(platform_name="linux", workspace_name="nonexistent") == fake_home / ".llama-mcp-bridge"


# --- F17: --warmup startup preloading ---
def test_f17_run_warmup_preloads_presets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _setup_workspace(tmp_path, bridge_extra="warmup_presets = digest,coder\n")
    monkeypatch.setenv("LLAMA_MCP_HOME", str(ws))
    cfg = load_config()

    mock_resp = MagicMock(status_code=200)
    with (
        patch("llama_mcp_bridge.bridge.LlamaSupervisor.ensure_running", return_value=True),
        patch("llama_mcp_bridge.bridge.requests.post", return_value=mock_resp) as mock_post,
    ):
        warmed = run_warmup(cfg)
    assert warmed == ["digest", "coder"]
    assert mock_post.call_count == 2
    assert mock_post.call_args.kwargs["json"]["max_tokens"] == 1


# --- F20: Release signature verification ---
def test_f20_verify_release_signature_paths(tmp_path: Path) -> None:
    archive = tmp_path / "llama-bin.tar.gz"
    archive.write_bytes(b"archive-bytes")
    assets = [{"name": "llama-bin.tar.gz.sig", "browser_download_url": "https://example.com/a.sig"}]

    # Disabled
    res_skip = verify_release_signature(archive, assets, verify_signatures=False)
    assert res_skip.method == "skipped"

    # Missing upstream signature asset -> warning, verified=False
    res_none = verify_release_signature(archive, [], verify_signatures=True)
    assert res_none.verified is False and res_none.method == "none"

    # Missing cosign/gpg binary -> warning, verified=False
    with patch("llama_mcp_bridge.signing.shutil.which", return_value=None):
        res_tool = verify_release_signature(archive, assets, verify_signatures=True)
    assert res_tool.verified is False and res_tool.method == "missing-tool"

    # Valid signature via gpg
    mock_dl = MagicMock()
    mock_dl.__enter__.return_value = mock_dl
    mock_dl.read.return_value = b"sigdata"
    with (
        patch("llama_mcp_bridge.signing.shutil.which", side_effect=lambda b: "/usr/bin/gpg" if b == "gpg" else None),
        patch("llama_mcp_bridge.signing.urllib.request.urlopen", return_value=mock_dl),
        patch("llama_mcp_bridge.signing.subprocess.run", return_value=MagicMock(returncode=0)),
    ):
        res_ok = verify_release_signature(archive, assets, verify_signatures=True)
    assert res_ok.verified is True and res_ok.method == "gpg"

    # Mismatch raises RuntimeError
    with (
        patch("llama_mcp_bridge.signing.shutil.which", side_effect=lambda b: "/usr/bin/gpg" if b == "gpg" else None),
        patch("llama_mcp_bridge.signing.urllib.request.urlopen", return_value=mock_dl),
        patch("llama_mcp_bridge.signing.subprocess.run", return_value=MagicMock(returncode=1, stderr="BADSIG")),
        pytest.raises(RuntimeError, match="FAILED"),
    ):
        verify_release_signature(archive, assets, verify_signatures=True)


# --- F22: Per-call timeout_s override ---
def test_f22_per_call_timeout_s_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = _setup_workspace(tmp_path)
    monkeypatch.setenv("LLAMA_MCP_HOME", str(ws))

    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"choices": [{"message": {"content": "fast"}}]}
    with (
        patch("llama_mcp_bridge.bridge.LlamaSupervisor.ensure_running", return_value=True),
        patch("llama_mcp_bridge.bridge.requests.post", return_value=mock_resp) as mock_post,
    ):
        out = ask_digest("hi", timeout_s=12.5)
    assert out == "fast"
    assert mock_post.call_args.kwargs["timeout"] == 12.5

    # timeout_s=0 falls back to preset default (90.0)
    with (
        patch("llama_mcp_bridge.bridge.LlamaSupervisor.ensure_running", return_value=True),
        patch("llama_mcp_bridge.bridge.requests.post", return_value=mock_resp) as mock_post2,
    ):
        ask_digest("hi", timeout_s=0)
    assert mock_post2.call_args.kwargs["timeout"] == 90.0
