"""Regression tests for LlamaSupervisor._start_server() CLI arguments."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from llama_mcp_bridge.config import BridgeConfig
from llama_mcp_bridge.presets import resolve_residency_settings
from llama_mcp_bridge.supervisor import LlamaSupervisor, create_supervisor


@pytest.mark.parametrize(
    ("tier", "residency"),
    [
        ("8gb", "stingy"),
        ("8gb", "moderate"),
        ("16gb", "moderate"),
        ("16gb", "snappy"),
        ("24gb+", "snappy"),
    ],
)
def test_start_server_passes_host_port_models_and_residency(
    tmp_path: Path,
    tier: str,
    residency: str,
) -> None:
    """_start_server() must forward --host, --port, --models-preset, --models-dir, and tier+residency."""
    expected_sleep, expected_max = resolve_residency_settings(tier, residency)
    models_ini = tmp_path / "models.ini"
    models_ini.write_text("[*]\n", encoding="utf-8")
    models_dir = tmp_path / "models"
    models_dir.mkdir()

    cfg = BridgeConfig(
        workspace=str(tmp_path),
        host="127.0.0.1",
        port=8099,
        models_ini=str(models_ini),
        models_dir=str(models_dir),
        residency=residency,
        llama_server_bin="llama-server",
    )
    sup = LlamaSupervisor(cfg)
    mock_proc = MagicMock(pid=12345)
    with (
        patch("llama_mcp_bridge.hardware.detect_hardware", return_value=SimpleNamespace(tier=tier)),
        patch("llama_mcp_bridge.supervisor.subprocess.Popen", return_value=mock_proc) as popen,
    ):
        assert sup._start_server() is True

    cmd = popen.call_args.args[0]
    assert "--host" in cmd and cmd[cmd.index("--host") + 1] == "127.0.0.1"
    assert "--port" in cmd and cmd[cmd.index("--port") + 1] == "8099"
    assert "--models-preset" in cmd and cmd[cmd.index("--models-preset") + 1] == str(models_ini)
    assert "--models-dir" in cmd and cmd[cmd.index("--models-dir") + 1] == str(models_dir)
    assert "--models-max" in cmd and cmd[cmd.index("--models-max") + 1] == str(expected_max)
    assert "--sleep-idle-seconds" in cmd and cmd[cmd.index("--sleep-idle-seconds") + 1] == str(expected_sleep)


def test_create_supervisor_forwards_models_and_residency(tmp_path: Path) -> None:
    """create_supervisor() must forward models_ini, models_dir, and residency to _start_server()."""
    models_ini = tmp_path / "models.ini"
    models_ini.write_text("[*]\n", encoding="utf-8")
    models_dir = tmp_path / "models"
    models_dir.mkdir()

    sup = create_supervisor(
        host="127.0.0.1",
        port=8081,
        models_ini=str(models_ini),
        models_dir=str(models_dir),
        residency="snappy",
    )
    mock_proc = MagicMock(pid=54321)
    with (
        patch.dict("os.environ", {"LLAMA_MCP_HOME": str(tmp_path)}),
        patch("llama_mcp_bridge.hardware.detect_hardware", return_value=SimpleNamespace(tier="24gb+")),
        patch("llama_mcp_bridge.supervisor.subprocess.Popen", return_value=mock_proc) as popen,
    ):
        assert sup._start_server() is True

    cmd = popen.call_args.args[0]
    assert "--models-preset" in cmd
    assert "--models-dir" in cmd
    assert "--models-max" in cmd and cmd[cmd.index("--models-max") + 1] == "3"


def test_start_server_omits_nonexistent_models_paths(tmp_path: Path) -> None:
    """Non-existent models_ini / models_dir paths must be omitted cleanly."""
    cfg = BridgeConfig(
        workspace=str(tmp_path),
        models_ini=str(tmp_path / "missing.ini"),
        models_dir=str(tmp_path / "missing_dir"),
        residency="moderate",
    )
    sup = LlamaSupervisor(cfg)
    mock_proc = MagicMock(pid=999)
    with (
        patch("llama_mcp_bridge.hardware.detect_hardware", return_value=SimpleNamespace(tier="8gb")),
        patch("llama_mcp_bridge.supervisor.subprocess.Popen", return_value=mock_proc) as popen,
    ):
        assert sup._start_server() is True

    cmd = popen.call_args.args[0]
    assert "--models-preset" not in cmd
    assert "--models-dir" not in cmd
    assert "--host" in cmd
    assert "--port" in cmd


def test_bridge_ini_arbitrary_models_max_and_sleep_idle_overrides(tmp_path: Path) -> None:
    """bridge.ini models_max and sleep_idle_seconds must override residency defaults."""
    from llama_mcp_bridge.config import _CONFIG_CACHE, load_config

    _CONFIG_CACHE.clear()
    (tmp_path / "bridge.ini").write_text(
        "[bridge]\nresidency = stingy\nmodels_max = 5\nsleep_idle_seconds = 1800\n",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg.models_max == 5
    assert cfg.sleep_idle_seconds == 1800

    sup = LlamaSupervisor(cfg)
    mock_proc = MagicMock(pid=777)
    with (
        patch("llama_mcp_bridge.hardware.detect_hardware", return_value=SimpleNamespace(tier="8gb")),
        patch("llama_mcp_bridge.supervisor.subprocess.Popen", return_value=mock_proc) as popen,
    ):
        assert sup._start_server() is True

    cmd = popen.call_args.args[0]
    assert cmd[cmd.index("--models-max") + 1] == "5"
    assert cmd[cmd.index("--sleep-idle-seconds") + 1] == "1800"


def test_fresh_bridge_ini_writes_llama_server_bin_schema_and_commented_defaults(
    tmp_path: Path,
) -> None:
    """B23: Fresh bridge.ini generation writes llama_server_bin, schema_version=1, and commented defaults."""
    from llama_mcp_bridge.config import CURRENT_SCHEMA_VERSION, load_config
    from llama_mcp_bridge.installer import _run_tui_wizard

    bin_path = tmp_path / "bin" / "llama-server"
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    bin_path.write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_path / "models").mkdir(exist_ok=True)

    profile = SimpleNamespace(
        os_name="linux",
        arch="x86_64",
        gpu_vendor="nvidia",
        backend="cuda",
        vram_gb=8.0,
        ram_gb=32.0,
        tier="8gb",
        recommended_mode="system-bin",
    )
    with (
        patch.dict("os.environ", {"LLAMA_MCP_HOME": str(tmp_path)}),
        patch("llama_mcp_bridge.installer.detect_hardware", return_value=profile),
        patch("llama_mcp_bridge.installer._find_llama_server", return_value=str(bin_path)),
        patch("llama_mcp_bridge.models_cli.select_presets_interactive", return_value=["digest"]),
        patch("llama_mcp_bridge.installer.Prompt.ask", side_effect=[str(tmp_path), str(tmp_path / "models"), "moderate"]),
        patch("llama_mcp_bridge.installer.Confirm.ask", side_effect=lambda q, **kw: "Proceed" in q),
    ):
        _run_tui_wizard()

    ini_text = (tmp_path / "bridge.ini").read_text(encoding="utf-8")
    assert f"schema_version = {CURRENT_SCHEMA_VERSION}" in ini_text
    assert f"llama_server_bin = {bin_path}" in ini_text
    for key in ("# host = 127.0.0.1", "# port = 8080", "# auto_spawn = true", "# models_max = 0"):
        assert key in ini_text
    cfg = load_config(tmp_path)
    assert cfg.schema_version == CURRENT_SCHEMA_VERSION
    assert cfg.llama_server_bin == str(bin_path)


def test_b18_ensure_running_releases_lock_during_poll_and_b19_metal_oom(tmp_path: Path) -> None:
    """B18: ensure_running() must not hold _lock during _poll_until_healthy(); B19: bare metal is not OOM."""
    from llama_mcp_bridge.supervisor import classify_inference_error

    cfg = BridgeConfig(workspace=str(tmp_path), auto_spawn=True)
    sup = LlamaSupervisor(cfg)

    lock_held_during_poll = []

    def _fake_poll() -> bool:
        acquired = sup._lock.acquire(blocking=False)
        lock_held_during_poll.append(not acquired)
        if acquired:
            sup._lock.release()
        return True

    with (
        patch.object(sup, "_ensure_process_exists", return_value=True),
        patch.object(sup, "_poll_until_healthy", side_effect=_fake_poll),
    ):
        assert sup.ensure_running() is True

    assert lock_held_during_poll == [False]

    profile = SimpleNamespace(tier="8gb")
    is_oom_shader, _ = classify_inference_error(
        RuntimeError("500"), "ggml_metal: shader compilation failed", "coder", profile, "moderate"
    )
    assert is_oom_shader is False
    is_oom_real, _ = classify_inference_error(
        RuntimeError("500"), "metal: out of memory", "coder", profile, "moderate"
    )
    assert is_oom_real is True


def test_b20_check_and_reload_models_ini_stat_cache(tmp_path: Path) -> None:
    """B20: check_and_reload_models_ini() caches by (st_mtime_ns, st_size) under _TOOL_REG_LOCK."""
    from llama_mcp_bridge import bridge
    from llama_mcp_bridge.config import _CONFIG_CACHE

    _CONFIG_CACHE.clear()
    models_ini = tmp_path / "models.ini"
    models_ini.write_text("[*]\n[digest]\nmodel = a.gguf\n", encoding="utf-8")
    (tmp_path / "bridge.ini").write_text(
        f"[bridge]\nmodels_ini = {models_ini}\n", encoding="utf-8"
    )
    bridge._MODELS_INI_STATE = None
    bridge._MODELS_INI_STAT_CACHE = None

    with patch.dict("os.environ", {"LLAMA_MCP_HOME": str(tmp_path)}):
        assert bridge.check_and_reload_models_ini() is False
        assert bridge.check_and_reload_models_ini() is False
        assert bridge._MODELS_INI_STAT_CACHE is not None
