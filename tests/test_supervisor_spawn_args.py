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
