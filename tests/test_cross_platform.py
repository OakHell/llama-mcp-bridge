import plistlib
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from llama_mcp_bridge.hardware import _probe_windows_wmi, detect_hardware
from llama_mcp_bridge.installer import (
    _extract_archive,
    _generate_service_file,
    _macos_release_asset,
)


def test_windows_wmi_gpu_probe():
    csv_out = '"Name","AdapterRAM"\n"AMD Radeon RX 7600","8589934592"\n'
    with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=csv_out)):
        res = _probe_windows_wmi()
    assert res == ("amd", "AMD Radeon RX 7600", 8.0)


def test_windows_zip_extraction(tmp_path: Path):
    archive = tmp_path / "llama.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("llama-server.exe", "binary")

    dest = tmp_path / "out"
    _extract_archive(archive, dest)
    assert (dest / "llama-server.exe").read_text() == "binary"


def test_macos_release_asset_mapping():
    assert _macos_release_asset("arm64") == "macos-arm64"
    assert _macos_release_asset("x86_64") == "macos-x64"


def test_macos_launchd_plist_valid_xml(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LLAMA_MCP_HOME", str(tmp_path))
    with patch("platform.system", return_value="Darwin"):
        _generate_service_file(dry_run=False)

    plist_path = tmp_path / "com.llama-mcp-bridge.plist"
    assert plist_path.exists()
    data = plistlib.loads(plist_path.read_bytes())
    assert data["Label"] == "com.llama-mcp-bridge"
    assert data["RunAtLoad"] is True


def test_macos_apple_silicon_sysctl():
    with (
        patch("platform.system", return_value="Darwin"),
        patch("platform.machine", return_value="arm64"),
        patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="34359738368\n")),
    ):
        profile = detect_hardware()
    assert profile.os_name == "darwin"
    assert profile.gpu_vendor == "apple"
    assert profile.vram_gb == 24.0
    assert profile.tier == "24gb+"


def test_supervisor_windows_popen_and_stop():
    import subprocess
    from llama_mcp_bridge.config import BridgeConfig
    from llama_mcp_bridge.supervisor import LlamaSupervisor

    sup = LlamaSupervisor(BridgeConfig(llama_server_bin="llama-server"))
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None

    with (
        patch("llama_mcp_bridge.supervisor.os.name", "nt"),
        patch("llama_mcp_bridge.supervisor.sys.platform", "win32"),
        patch("shutil.which", return_value="C:\\bin\\llama-server.exe"),
        patch("llama_mcp_bridge.supervisor.subprocess.Popen", return_value=mock_proc) as mock_popen,
        patch.object(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, create=True),
        patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
    ):
        sup._start_server()
        cmd = mock_popen.call_args.args[0]
        assert cmd[0].endswith("llama-server.exe")
        kwargs = mock_popen.call_args.kwargs
        assert kwargs["creationflags"] == (0x00000200 | 0x08000000)
        assert "start_new_session" not in kwargs

        assert sup.stop() is True
        mock_proc.terminate.assert_called_once()


def test_supervisor_posix_popen():
    from llama_mcp_bridge.config import BridgeConfig
    from llama_mcp_bridge.supervisor import LlamaSupervisor

    sup = LlamaSupervisor(BridgeConfig(llama_server_bin="llama-server"))
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None

    with (
        patch("llama_mcp_bridge.supervisor.os.name", "posix"),
        patch("llama_mcp_bridge.supervisor.sys.platform", "linux"),
        patch("shutil.which", return_value="/usr/bin/llama-server"),
        patch("llama_mcp_bridge.supervisor.subprocess.Popen", return_value=mock_proc) as mock_popen,
    ):
        sup._start_server()
        cmd = mock_popen.call_args.args[0]
        assert cmd[0] == "llama-server"
        kwargs = mock_popen.call_args.kwargs
        assert kwargs.get("start_new_session") is True or kwargs.get("preexec_fn") is not None
