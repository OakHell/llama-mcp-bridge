import io
import json
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_t1_select_llama_release_asset_amd_rocm():
    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.installer import _select_llama_release_asset

    amd = SystemProfile("linux", "x86_64", "amd", "RX 7900", 24.0, "", "ubuntu", "24gb+")
    assets = [
        {"name": "llama-b1-bin-ubuntu-vulkan-x64.tar.gz", "browser_download_url": "https://x/v.tgz"},
        {"name": "llama-b1-bin-ubuntu-rocm-x64.tar.gz", "browser_download_url": "https://x/r.tgz"},
    ]
    sel = _select_llama_release_asset(assets, amd)
    assert sel is not None
    assert "rocm" in sel["name"]


def test_t2_detect_hardware_bsd():
    from llama_mcp_bridge.hardware import detect_hardware

    with (
        patch("platform.system", return_value="FreeBSD"),
        patch("platform.machine", return_value="amd64"),
        patch("llama_mcp_bridge.hardware._probe_nvidia", return_value=("RTX 3070", 8.0, "sm_86")),
    ):
        prof = detect_hardware()
    assert prof.os_name == "freebsd"
    assert prof.gpu_vendor == "nvidia"
    assert prof.tier == "8gb"


def test_t3_probe_windows_wmi_64bit_registry_over_4gb():
    from llama_mcp_bridge.hardware import _probe_windows_wmi

    sixteen_gb = str(16 * 1024**3)
    with patch(
        "subprocess.run",
        return_value=MagicMock(returncode=0, stdout=f"AMD Radeon RX 7800 XT,{sixteen_gb}\n"),
    ):
        res = _probe_windows_wmi()
    assert res == ("amd", "AMD Radeon RX 7800 XT", 16.0)


def test_t4_t5_t6_m7_extract_archive_security_bounds(tmp_path: Path):
    from llama_mcp_bridge.installer import _extract_archive

    # T4: Zip Slip path traversal
    bad_zip = tmp_path / "slip.zip"
    with zipfile.ZipFile(bad_zip, "w") as zf:
        zf.writestr("../evil.txt", "pwn")
    with pytest.raises(ValueError, match="traversal"):
        _extract_archive(bad_zip, tmp_path / "out1")

    # T4: Tar path traversal
    bad_tar = tmp_path / "slip.tar.gz"
    with tarfile.open(bad_tar, "w:gz") as tf:
        info = tarfile.TarInfo("../../evil.txt")
        info.size = 3
        tf.addfile(info, io.BytesIO(b"pwn"))
    with pytest.raises(ValueError, match="traversal"):
        _extract_archive(bad_tar, tmp_path / "out2")

    # T5: Max bytes / max entries
    big_zip = tmp_path / "big.zip"
    with zipfile.ZipFile(big_zip, "w") as zf:
        zf.writestr("a.txt", "1234567890")
    with pytest.raises(ValueError, match="exceeds limit"):
        _extract_archive(big_zip, tmp_path / "out3", max_bytes=5)

    # T6 / M7: Symlink escape
    sym_tar = tmp_path / "sym.tar.gz"
    with tarfile.open(sym_tar, "w:gz") as tf:
        s = tarfile.TarInfo("escape")
        s.type = tarfile.SYMTYPE
        s.linkname = "../../etc/passwd"
        tf.addfile(s)
    with pytest.raises(ValueError, match="outside dest_dir"):
        _extract_archive(sym_tar, tmp_path / "out4")


def test_t7_remove_models_from_ini_ignores_traversal_paths(tmp_path: Path):
    from llama_mcp_bridge.presets import remove_models_from_ini

    models_dir = tmp_path / "models"
    models_dir.mkdir()
    outside = tmp_path / "secret.gguf"
    outside.write_bytes(b"keep")
    ini = tmp_path / "models.ini"
    ini.write_text(f"[custom]\nmodel = {outside}\n", encoding="utf-8")

    deleted = remove_models_from_ini(ini, ["custom"], models_dir, delete_files=True)
    assert deleted == []
    assert outside.exists()


def test_t8_register_configured_tools_idempotent(monkeypatch, tmp_path: Path):
    from fastmcp import FastMCP

    from llama_mcp_bridge.bridge import _register_configured_tools, ask_preset, get_bridge_status

    monkeypatch.setenv("LLAMA_MCP_HOME", str(tmp_path))
    (tmp_path / "models.ini").write_text("[*]\nmodels-max = 1\n\n[digest]\nmodel = d.gguf\n", encoding="utf-8")
    (tmp_path / "bridge.ini").write_text(f"[bridge]\nmodels_ini = {tmp_path / 'models.ini'}\n", encoding="utf-8")

    srv = FastMCP("t8")
    srv.tool()(get_bridge_status)
    srv.tool()(ask_preset)
    first = _register_configured_tools(target_mcp=srv)
    second = _register_configured_tools(target_mcp=srv)
    assert first == second == ["ask_digest"]


def test_t9_configure_client_apply_handles_list_json(monkeypatch, tmp_path: Path):
    from llama_mcp_bridge.installer import _configure_client

    home = tmp_path / "home"
    cfg = home / ".cursor" / "mcp.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("[]", encoding="utf-8")

    monkeypatch.setenv("LLAMA_MCP_HOME", str(tmp_path / "ws"))
    with (
        patch("llama_mcp_bridge.installer.Path.home", return_value=home),
        patch("llama_mcp_bridge.installer._resolve_bridge_command", return_value="/bin/llama-mcp-bridge"),
    ):
        _configure_client("cursor", apply=True)

    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["mcpServers"]["local-ai"]["command"] == "/bin/llama-mcp-bridge"


def test_t10_probe_model_metadata_timeout():
    from llama_mcp_bridge.presets import probe_model_metadata

    with patch("llama_mcp_bridge.presets.urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        ok, size, sha = probe_model_metadata("https://huggingface.co/test/m.gguf", timeout=0.1)
    assert (ok, size, sha) == (False, None, None)


def test_t11_find_llama_server_missing_configured_bin(tmp_path: Path):
    from llama_mcp_bridge.installer import _find_llama_server

    with patch("llama_mcp_bridge.installer.shutil.which", return_value=None):
        assert _find_llama_server(tmp_path, str(tmp_path / "missing-bin")) is None


def test_t15_query_llama_missing_models_ini_at_runtime(monkeypatch, tmp_path: Path):
    from llama_mcp_bridge.bridge import query_llama

    monkeypatch.setenv("LLAMA_MCP_HOME", str(tmp_path))
    (tmp_path / "bridge.ini").write_text(f"[bridge]\nmodels_ini = {tmp_path / 'deleted.ini'}\n", encoding="utf-8")

    ok_resp = MagicMock(status_code=200)
    ok_resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
    with (
        patch("llama_mcp_bridge.bridge.LlamaSupervisor.ensure_running"),
        patch("llama_mcp_bridge.bridge.requests.post", return_value=ok_resp),
    ):
        assert query_llama("digest", "sys", "hi") == "ok"


def test_t20_m10_run_doctor_auto_spawn_false_and_version_check(capsys, monkeypatch, tmp_path: Path):
    from llama_mcp_bridge.installer import _run_doctor

    monkeypatch.setenv("LLAMA_MCP_HOME", str(tmp_path))
    (tmp_path / "bridge.ini").write_text("[bridge]\nauto_spawn = false\n", encoding="utf-8")
    (tmp_path / "models.ini").write_text("[*]\nmodels-max = 1\n", encoding="utf-8")
    fake_bin = tmp_path / "bin" / "llama-server"
    fake_bin.parent.mkdir()
    fake_bin.write_bytes(b"#!/bin/sh\nexit 0\n")
    fake_bin.chmod(0o755)

    with (
        patch("llama_mcp_bridge.installer._find_llama_server", return_value=str(fake_bin)),
        patch("llama_mcp_bridge.supervisor.LlamaSupervisor._check_health", return_value=False),
    ):
        rc = _run_doctor()
    out = capsys.readouterr().out
    assert rc == 0
    assert "WARN" in out
    assert "Standby" not in out


def test_s5_crontab_rejects_relative_or_missing_binary(tmp_path: Path):
    from llama_mcp_bridge.installer import _install_reboot_crontab

    with pytest.raises(ValueError, match="existing absolute path"):
        _install_reboot_crontab("llama-server", str(tmp_path / "models.ini"), "127.0.0.1", 8080, dry_run=False)
