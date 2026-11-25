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
    with patch("llama_mcp_bridge.installer.sys.platform", "darwin"):
        _generate_service_file(dry_run=False)

    plist_path = tmp_path / "com.llama-server.plist"
    assert plist_path.exists()
    data = plistlib.loads(plist_path.read_bytes())
    assert data["Label"] == "com.llama-server"
    assert data["WorkingDirectory"] == str(tmp_path)
    assert "--models-preset" in data["ProgramArguments"]
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


def test_supervisor_windows_popen_and_stop(tmp_path: Path):
    import subprocess

    from llama_mcp_bridge.config import BridgeConfig
    from llama_mcp_bridge.supervisor import LlamaSupervisor

    sup = LlamaSupervisor(BridgeConfig(workspace=str(tmp_path), llama_server_bin="llama-server"))
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None

    with (
        patch("llama_mcp_bridge.supervisor.sys.platform", "win32"),
        patch("shutil.which", return_value="C:\\bin\\llama-server.exe"),
        patch("llama_mcp_bridge.supervisor.subprocess.Popen", return_value=mock_proc) as mock_popen,
        patch.object(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, create=True),
        patch.object(subprocess, "DETACHED_PROCESS", 0x00000008, create=True),
    ):
        assert sup._start_server() is True
        cmd = mock_popen.call_args.args[0]
        assert cmd[0].endswith("llama-server.exe")
        kwargs = mock_popen.call_args.kwargs
        assert kwargs["creationflags"] == (0x00000200 | 0x00000008)
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert "start_new_session" not in kwargs
        assert (tmp_path / "llama-server.log").exists()

        assert sup.stop() is True
        mock_proc.terminate.assert_called_once()


def test_supervisor_posix_popen(tmp_path: Path):
    import subprocess

    from llama_mcp_bridge.config import BridgeConfig
    from llama_mcp_bridge.supervisor import LlamaSupervisor

    sup = LlamaSupervisor(BridgeConfig(workspace=str(tmp_path), llama_server_bin="llama-server"))
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None

    with (
        patch("llama_mcp_bridge.supervisor.sys.platform", "linux"),
        patch("shutil.which", return_value="/usr/bin/llama-server"),
        patch("llama_mcp_bridge.supervisor.subprocess.Popen", return_value=mock_proc) as mock_popen,
    ):
        assert sup._start_server() is True
        cmd = mock_popen.call_args.args[0]
        assert cmd[0] == "llama-server"
        kwargs = mock_popen.call_args.kwargs
        assert kwargs.get("start_new_session") is True
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert (tmp_path / "llama-server.log").exists()


def test_config_home_posix_default_never_mnt_ai(monkeypatch):
    from llama_mcp_bridge.config import get_config_home

    monkeypatch.delenv("LLAMA_MCP_HOME", raising=False)
    with patch("llama_mcp_bridge.config.sys.platform", "linux"):
        home = get_config_home()
    assert home == Path.home() / ".llama-mcp-bridge"
    assert "/mnt/ai" not in str(home)


def test_config_home_windows_localappdata(monkeypatch, tmp_path: Path):
    from llama_mcp_bridge.config import get_config_home

    monkeypatch.delenv("LLAMA_MCP_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    with patch("llama_mcp_bridge.config.sys.platform", "win32"):
        home = get_config_home()
    assert home == tmp_path / "LocalAppData" / "llama-mcp-bridge"


def test_load_config_custom_workspace_and_relative_paths(monkeypatch, tmp_path: Path):
    from llama_mcp_bridge.config import load_config

    cfg_home = tmp_path / "cfg"
    cfg_home.mkdir()
    ws = tmp_path / "my_workspace"
    (cfg_home / "bridge.ini").write_text(
        f"[bridge]\nworkspace = {ws}\nmodels_dir = custom_models\nllama_server_bin = bin/llama-server\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLAMA_MCP_HOME", str(cfg_home))

    with patch("llama_mcp_bridge.config.sys.platform", "win32"):
        cfg = load_config()

    assert cfg.workspace == str(ws)
    assert cfg.models_dir == str(ws / "custom_models")
    assert cfg.models_ini == str(ws / "models.ini")
    assert cfg.llama_server_bin == str(ws / "bin" / "llama-server.exe")


def test_bridge_example_ini_parity(monkeypatch, tmp_path: Path):
    import shutil

    from llama_mcp_bridge.config import load_config

    example_ini = Path(__file__).resolve().parents[1] / "bridge.example.ini"
    assert example_ini.exists()

    cfg_home = tmp_path / "cfg"
    cfg_home.mkdir()
    shutil.copy(example_ini, cfg_home / "bridge.ini")
    monkeypatch.setenv("LLAMA_MCP_HOME", str(cfg_home))

    cfg = load_config()
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8080
    assert cfg.auto_spawn is True
    assert cfg.health_timeout_s == 60
    assert cfg.request_timeout_s == 180


def test_windows_service_bat_and_schtasks(monkeypatch, tmp_path: Path):
    from llama_mcp_bridge.installer import _generate_service_file

    monkeypatch.setenv("LLAMA_MCP_HOME", str(tmp_path))
    with (
        patch("llama_mcp_bridge.installer.sys.platform", "win32"),
        patch("llama_mcp_bridge.config.sys.platform", "win32"),
        patch("llama_mcp_bridge.installer.subprocess.run") as mock_run,
    ):
        _generate_service_file(dry_run=False)

    bat_path = tmp_path / "start-llama-server.bat"
    assert bat_path.exists()
    assert "--models-preset" in bat_path.read_text(encoding="utf-8")
    assert mock_run.call_count == 2
    assert mock_run.call_args_list[0].args[0][:2] == ["schtasks", "/Create"]
    assert mock_run.call_args_list[1].args[0][:2] == ["schtasks", "/Run"]


def test_non_systemd_linux_and_bsd_crontab_dedup(monkeypatch, tmp_path: Path):
    from llama_mcp_bridge.installer import _generate_service_file

    monkeypatch.setenv("LLAMA_MCP_HOME", str(tmp_path))
    existing_cron = "0 * * * * /usr/bin/backup\n@reboot old # llama-mcp-bridge\n"

    for plat in ("linux", "freebsd14"):
        with (
            patch("llama_mcp_bridge.installer.sys.platform", plat),
            patch("llama_mcp_bridge.installer.shutil.which", return_value=None),
            patch("llama_mcp_bridge.installer.subprocess.run") as mock_run,
        ):
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout=existing_cron),
                MagicMock(returncode=0),
            ]
            _generate_service_file(dry_run=False)

        written_cron = mock_run.call_args_list[1].kwargs["input"]
        assert written_cron.count("# llama-mcp-bridge") == 1
        assert "/usr/bin/backup" in written_cron


def test_hardware_prerequisite_warning_when_missing(capsys):
    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.installer import _check_hardware_prerequisites

    profile = SystemProfile("linux", "x86_64", "nvidia", "RTX 3070", 8.0, "sm_86", "ubuntu", "8gb")
    with patch("llama_mcp_bridge.installer.shutil.which", return_value=None):
        missing = _check_hardware_prerequisites(profile, "prebuilt")
    out = capsys.readouterr().out
    assert "nvidia-smi" in out
    assert len(missing) >= 1


def test_auto_install_declined_skips_download(tmp_path: Path):
    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.installer import _auto_install_llama_server

    profile = SystemProfile("linux", "x86_64", "cpu", "CPU", 0.0, "", "linux", "6gb")
    with (
        patch("llama_mcp_bridge.installer.sys.stdin.isatty", return_value=True),
        patch("builtins.input", return_value="n"),
        patch("llama_mcp_bridge.installer.urllib.request.urlopen") as mock_url,
    ):
        _auto_install_llama_server(profile, "prebuilt", tmp_path, dry_run=False, yes=False)
    mock_url.assert_not_called()
    assert not (tmp_path / "bridge.ini").exists()


def test_auto_install_prebuilt_yes_extracts_and_updates_ini(tmp_path: Path):
    import io
    import json

    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.installer import _auto_install_llama_server

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("llama-server", "binary")
    zip_bytes = buf.getvalue()

    release_json = json.dumps(
        {"assets": [{"name": "llama-ubuntu-x64.zip", "browser_download_url": "https://example.com/llama.zip"}]}
    ).encode("utf-8")

    resp1 = MagicMock()
    resp1.__enter__.return_value = resp1
    resp1.read.return_value = release_json

    resp2 = MagicMock()
    resp2.__enter__.return_value = resp2
    resp2.read.return_value = zip_bytes

    profile = SystemProfile("linux", "x86_64", "cpu", "CPU", 0.0, "", "linux", "6gb")
    with patch("llama_mcp_bridge.installer.urllib.request.urlopen", side_effect=[resp1, resp2]):
        _auto_install_llama_server(profile, "prebuilt", tmp_path, dry_run=False, yes=True)

    assert (tmp_path / "bin" / "llama-server").exists()
    assert "llama_server_bin = bin/llama-server" in (tmp_path / "bridge.ini").read_text(encoding="utf-8")


def test_tui_wizard_generates_configs(monkeypatch, tmp_path: Path):
    import os

    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.installer import _run_tui_wizard

    custom_ws = tmp_path / "custom_ws"
    monkeypatch.setenv("LLAMA_MCP_HOME", str(tmp_path))
    profile = SystemProfile("linux", "x86_64", "cpu", "CPU", 0.0, "", "linux", "6gb")

    with (
        patch("llama_mcp_bridge.installer.detect_hardware", return_value=profile),
        patch("llama_mcp_bridge.installer._find_llama_server", return_value=None),
        patch("llama_mcp_bridge.models_cli.select_presets_interactive", return_value=["digest"]),
        patch(
            "llama_mcp_bridge.installer.Prompt.ask",
            side_effect=[str(custom_ws), str(custom_ws / "models"), "moderate", "skip"],
        ),
        patch("llama_mcp_bridge.installer.Confirm.ask", side_effect=[False, True, False, False]),
    ):
        _run_tui_wizard()

    assert os.environ["LLAMA_MCP_HOME"] == str(custom_ws)
    assert (custom_ws / "models.ini").exists()
    assert (custom_ws / "bridge.ini").exists()
    assert "models_ini =" in (custom_ws / "bridge.ini").read_text(encoding="utf-8")


def test_download_tier_models_skips_existing(tmp_path: Path):
    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.installer import _download_tier_models
    from llama_mcp_bridge.presets import get_tier_model_specs

    for spec in get_tier_model_specs("6gb"):
        (tmp_path / spec["filename"]).write_bytes(b"gguf")

    profile = SystemProfile("linux", "x86_64", "cpu", "CPU", 0.0, "", "linux", "6gb")
    with patch("llama_mcp_bridge.installer.urllib.request.urlopen") as mock_url:
        _download_tier_models(profile, tmp_path, dry_run=False)
    mock_url.assert_not_called()


def test_download_tier_models_resumes_part_with_range(tmp_path: Path):
    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.installer import _download_tier_models
    from llama_mcp_bridge.presets import get_tier_model_specs

    specs = get_tier_model_specs("8gb")
    first = specs[0]["filename"]
    for spec in specs[1:]:
        (tmp_path / spec["filename"]).write_bytes(b"done")
    (tmp_path / f"{first}.part").write_bytes(b"abc")

    resp = MagicMock()
    resp.__enter__.return_value = resp
    resp.status = 206
    resp.headers = {"Content-Length": "3"}
    resp.read.side_effect = [b"def", b""]

    profile = SystemProfile("linux", "x86_64", "cpu", "CPU", 0.0, "", "linux", "8gb")
    with (
        patch("llama_mcp_bridge.installer.probe_model_metadata", return_value=(True, 6, None)),
        patch("llama_mcp_bridge.installer.urllib.request.urlopen", return_value=resp) as mock_url,
    ):
        _download_tier_models(profile, tmp_path, dry_run=False)

    req_sent = mock_url.call_args.args[0]
    assert req_sent.headers["Range"] == "bytes=3-"
    assert (tmp_path / first).read_bytes() == b"abcdef"
    assert not (tmp_path / f"{first}.part").exists()


def test_download_tier_models_dry_run_no_network(tmp_path: Path):
    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.installer import _download_tier_models

    profile = SystemProfile("linux", "x86_64", "cpu", "CPU", 0.0, "", "linux", "6gb")
    with patch("llama_mcp_bridge.installer.urllib.request.urlopen") as mock_url:
        _download_tier_models(profile, tmp_path, dry_run=True)
    mock_url.assert_not_called()


def test_tier_model_registry_matches_generated_models_ini():
    import configparser

    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.presets import generate_models_ini, get_tier_model_specs

    for tier in ("6gb", "8gb", "12gb", "16gb", "24gb+"):
        profile = SystemProfile("linux", "x86_64", "nvidia", "GPU", 8.0, "sm_86", "ubuntu", tier)
        ini_str = generate_models_ini(profile, models_dir="/models")
        parser = configparser.ConfigParser()
        parser.read_string(ini_str)

        specs = get_tier_model_specs(tier)
        registry_files = {s["filename"]: s for s in specs}

        for section in parser.sections():
            for key in ("model", "mmproj"):
                if parser.has_option(section, key):
                    basename = Path(parser.get(section, key)).name
                    assert basename in registry_files, f"{basename} missing in {tier} registry"
                    assert registry_files[basename]["url"].startswith("https://huggingface.co/")
                    assert "/resolve/main/" in registry_files[basename]["url"]
                    assert registry_files[basename]["size_gb"] > 0


def test_verify_tier_models_reports_ok_and_suggestions_on_404():
    import json
    import urllib.error

    from llama_mcp_bridge.presets import verify_tier_models

    ok_resp = MagicMock()
    ok_resp.__enter__.return_value = ok_resp
    ok_resp.status = 200
    ok_resp.headers = {"X-Linked-Size": "1048576"}

    search_resp = MagicMock()
    search_resp.__enter__.return_value = search_resp
    search_resp.read.return_value = json.dumps([{"id": "Qwen/Alt-GGUF", "downloads": 100}]).encode("utf-8")

    def fake_urlopen(req, timeout=10.0):
        url = req if isinstance(req, str) else req.full_url
        if "api/models" in url:
            return search_resp
        if "MiniCPM-V-4_6-Q4_K_M" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return ok_resp

    with patch("llama_mcp_bridge.presets.urllib.request.urlopen", side_effect=fake_urlopen):
        results = verify_tier_models("6gb")

    ok_items = [r for r in results if r["ok"]]
    missing_items = [r for r in results if not r["ok"]]
    assert len(ok_items) >= 1
    assert len(missing_items) == 1
    assert missing_items[0]["suggestions"][0]["id"] == "Qwen/Alt-GGUF"


def test_6gb_vision_deep_uses_vlm_model():
    import configparser

    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.presets import generate_models_ini

    profile = SystemProfile("linux", "x86_64", "cpu", "CPU", 0.0, "", "linux", "6gb")
    parser = configparser.ConfigParser()
    parser.read_string(generate_models_ini(profile, models_dir="/models"))
    assert parser.get("vision-deep", "model") == "/models/Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf"
    assert parser.get("vision-deep", "mmproj") == "/models/mmproj-F16.gguf"


def test_select_llama_release_asset_scoring():
    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.installer import _select_llama_release_asset

    assets = [
        {"name": "cudart-llama-bin-win-cuda-cu12.2-x64.zip"},
        {"name": "llama-b4000-bin-win-cpu-x64.zip"},
        {"name": "llama-b4000-bin-win-cuda-cu12.2-x64.zip"},
        {"name": "llama-b4000-bin-win-vulkan-arm64.zip"},
        {"name": "llama-b4000-bin-ubuntu-x64.tar.gz"},
        {"name": "llama-b4000-bin-ubuntu-vulkan-x64.tar.gz"},
    ]

    win_nv = SystemProfile("windows", "x86_64", "nvidia", "RTX 3070", 8.0, "sm_86", "windows", "8gb")
    assert _select_llama_release_asset(assets, win_nv)["name"] == "llama-b4000-bin-win-cuda-cu12.2-x64.zip"

    linux_amd = SystemProfile("linux", "x86_64", "amd", "RX 7600", 8.0, "", "ubuntu", "8gb")
    assert _select_llama_release_asset(assets, linux_amd)["name"] == "llama-b4000-bin-ubuntu-vulkan-x64.tar.gz"


def test_download_tier_models_resumed_progress_totals(tmp_path: Path):
    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.installer import _download_tier_models
    from llama_mcp_bridge.presets import get_tier_model_specs

    specs = get_tier_model_specs("6gb")
    first = specs[0]["filename"]
    for spec in specs[1:]:
        (tmp_path / spec["filename"]).write_bytes(b"done")
    (tmp_path / f"{first}.part").write_bytes(b"abc")

    resp = MagicMock()
    resp.__enter__.return_value = resp
    resp.status = 206
    resp.headers = {"Content-Length": "4"}
    resp.read.side_effect = [b"defg", b""]

    profile = SystemProfile("linux", "x86_64", "cpu", "CPU", 0.0, "", "linux", "6gb")
    with (
        patch("llama_mcp_bridge.installer.probe_model_metadata", return_value=(True, 7, None)),
        patch("llama_mcp_bridge.installer.urllib.request.urlopen", return_value=resp),
        patch("llama_mcp_bridge.installer.Progress") as mock_progress_cls,
    ):
        mock_progress = mock_progress_cls.return_value.__enter__.return_value
        _download_tier_models(profile, tmp_path, dry_run=False)

    mock_progress.add_task.assert_called_once_with(
        f"Downloading {first}", total=7, completed=3
    )


def test_download_sha256_valid_etag_renames(tmp_path: Path, capsys):
    import hashlib

    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.installer import _download_tier_models
    from llama_mcp_bridge.presets import get_tier_model_specs

    specs = get_tier_model_specs("6gb")
    first = specs[0]["filename"]
    for spec in specs[1:]:
        (tmp_path / spec["filename"]).write_bytes(b"done")

    payload = b"valid-gguf-payload"
    expected = hashlib.sha256(payload).hexdigest().upper()

    head_resp = MagicMock()
    head_resp.__enter__.return_value = head_resp
    head_resp.status = 200
    head_resp.headers = {"X-Linked-Size": str(len(payload)), "X-Linked-ETag": f'"{expected}"'}

    get_resp = MagicMock()
    get_resp.__enter__.return_value = get_resp
    get_resp.status = 200
    get_resp.headers = {"Content-Length": str(len(payload))}
    get_resp.read.side_effect = [payload, b""]

    profile = SystemProfile("linux", "x86_64", "cpu", "CPU", 0.0, "", "linux", "6gb")
    with patch("urllib.request.urlopen", side_effect=[head_resp, get_resp]):
        _download_tier_models(profile, tmp_path, dry_run=False)

    assert (tmp_path / first).read_bytes() == payload
    assert not (tmp_path / f"{first}.part").exists()
    assert "Verified SHA-256:" in capsys.readouterr().out


def test_download_sha256_mismatch_deletes_part_and_raises(tmp_path: Path):
    import pytest

    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.installer import _download_tier_models
    from llama_mcp_bridge.presets import get_tier_model_specs

    specs = get_tier_model_specs("6gb")
    first = specs[0]["filename"]
    for spec in specs[1:]:
        (tmp_path / spec["filename"]).write_bytes(b"done")

    bad_sha = "0" * 64
    get_resp = MagicMock()
    get_resp.__enter__.return_value = get_resp
    get_resp.status = 200
    get_resp.headers = {"Content-Length": "7"}
    get_resp.read.side_effect = [b"corrupt", b""]

    profile = SystemProfile("linux", "x86_64", "cpu", "CPU", 0.0, "", "linux", "6gb")
    with (
        patch("llama_mcp_bridge.installer.probe_model_metadata", return_value=(True, 7, bad_sha)),
        patch("llama_mcp_bridge.installer.urllib.request.urlopen", return_value=get_resp),
        pytest.raises(RuntimeError, match="SHA-256 mismatch"),
    ):
        _download_tier_models(profile, tmp_path, dry_run=False)

    assert not (tmp_path / first).exists()
    assert not (tmp_path / f"{first}.part").exists()


def test_download_missing_or_non_sha256_etag_completes_gracefully():
    from llama_mcp_bridge.presets import probe_model_metadata

    head_resp = MagicMock()
    head_resp.__enter__.return_value = head_resp
    head_resp.status = 200
    head_resp.headers = {"Content-Length": "123", "X-Linked-ETag": '"weak-etag-not-sha256"'}

    with patch("llama_mcp_bridge.presets.urllib.request.urlopen", return_value=head_resp):
        ok, size, sha = probe_model_metadata("https://huggingface.co/test/model.gguf")

    assert ok is True
    assert size == 123
    assert sha is None


def test_bridge_config_residency_validation(monkeypatch, tmp_path: Path):
    import pytest

    from llama_mcp_bridge.config import load_config

    cfg_home = tmp_path / "cfg"
    cfg_home.mkdir()
    monkeypatch.setenv("LLAMA_MCP_HOME", str(cfg_home))

    (cfg_home / "bridge.ini").write_text("[bridge]\nresidency = SNAPPY\n", encoding="utf-8")
    assert load_config().residency == "snappy"

    (cfg_home / "bridge.ini").write_text("[bridge]\nresidency = invalid_mode\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid residency profile"):
        load_config()


def test_supervisor_get_status_reports_residency():
    from llama_mcp_bridge.config import BridgeConfig
    from llama_mcp_bridge.supervisor import LlamaSupervisor

    sup = LlamaSupervisor(BridgeConfig(residency="stingy"))
    with patch.object(sup, "_check_health", return_value=False):
        status = sup.get_status()
    assert status["residency"] == "stingy"


def test_ps_main_renders_status_table(capsys, monkeypatch, tmp_path: Path):
    from llama_mcp_bridge.installer import ps_main

    monkeypatch.setenv("LLAMA_MCP_HOME", str(tmp_path))
    fake_status = {
        "healthy": True,
        "server_url": "http://127.0.0.1:8080",
        "residency": "snappy",
        "tier": "16gb",
        "vram_gb": 16.0,
        "gpu_vendor": "nvidia",
        "models": {"coder": "loaded"},
    }
    with patch("llama_mcp_bridge.installer.LlamaSupervisor.get_status", return_value=fake_status):
        ps_main()

    out = capsys.readouterr().out
    assert "ONLINE" in out
    assert "snappy" in out
    assert "LOADED" in out


def test_run_doctor_passes_and_fails_on_missing_gguf(monkeypatch, tmp_path: Path):
    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.installer import _run_doctor
    from llama_mcp_bridge.presets import generate_models_ini

    monkeypatch.setenv("LLAMA_MCP_HOME", str(tmp_path))
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (tmp_path / "bridge.ini").write_text(
        f"[bridge]\nworkspace = {tmp_path}\nmodels_dir = {models_dir}\n"
        f"models_ini = {tmp_path / 'models.ini'}\n",
        encoding="utf-8",
    )
    profile = SystemProfile("linux", "x86_64", "cpu", "CPU", 0.0, "", "linux", "6gb")
    ini_text = generate_models_ini(profile, models_dir=str(models_dir))
    (tmp_path / "models.ini").write_text(ini_text, encoding="utf-8")

    fake_bin = tmp_path / "llama-server"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)

    with (
        patch("llama_mcp_bridge.installer._find_llama_server", return_value=fake_bin),
        patch("llama_mcp_bridge.installer.LlamaSupervisor.get_status", return_value={"healthy": False, "server_url": "http://127.0.0.1:8080"}),
    ):
        assert _run_doctor() == 1

    import configparser

    cp = configparser.ConfigParser()
    cp.read_string(ini_text)
    for sec in cp.sections():
        for key in ("model", "mmproj"):
            if cp.has_option(sec, key):
                Path(cp.get(sec, key)).write_bytes(b"gguf")

    with (
        patch("llama_mcp_bridge.installer._find_llama_server", return_value=fake_bin),
        patch("llama_mcp_bridge.installer.LlamaSupervisor.get_status", return_value={"healthy": True, "server_url": "http://127.0.0.1:8080"}),
    ):
        assert _run_doctor() == 0


def test_configure_client_prints_json_and_url_without_mutating(capsys, monkeypatch, tmp_path: Path):
    import json

    from llama_mcp_bridge.installer import _configure_client

    monkeypatch.setenv("LLAMA_MCP_HOME", str(tmp_path))
    _configure_client("cursor")
    out = capsys.readouterr().out
    assert "https://docs.cursor.com/context/model-context-protocol" in out
    assert json.dumps(str(tmp_path)) in out
    assert "mcpServers" in out


def test_auto_install_prebuilt_falls_back_to_recent_releases(tmp_path: Path):
    import io
    import json
    import tarfile

    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.installer import _auto_install_llama_server

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("llama-server")
        payload = b"binary"
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    tgz_bytes = buf.getvalue()

    latest_json = json.dumps({"assets": [{"name": "nightly-tag.txt"}]}).encode("utf-8")
    releases_json = json.dumps(
        [
            {"draft": True, "assets": [{"name": "llama-b9999-bin-ubuntu-x64.tar.gz"}]},
            {
                "draft": False,
                "assets": [
                    {
                        "name": "llama-b12359-bin-ubuntu-x64.tar.gz",
                        "browser_download_url": "https://example.com/b12359.tar.gz",
                    }
                ],
            },
        ]
    ).encode("utf-8")

    r1 = MagicMock()
    r1.__enter__.return_value = r1
    r1.read.return_value = latest_json

    r2 = MagicMock()
    r2.__enter__.return_value = r2
    r2.read.return_value = releases_json

    r3 = MagicMock()
    r3.__enter__.return_value = r3
    r3.read.return_value = tgz_bytes

    profile = SystemProfile("linux", "x86_64", "cpu", "CPU", 0.0, "", "ubuntu", "6gb")
    with patch("llama_mcp_bridge.installer.urllib.request.urlopen", side_effect=[r1, r2, r3]):
        _auto_install_llama_server(profile, "prebuilt", tmp_path, dry_run=False, yes=True)

    assert (tmp_path / "bin" / "llama-server").read_bytes() == b"binary"


def test_uninstall_yes_removes_bins_and_service_preserves_models_and_ini(monkeypatch, tmp_path: Path):
    from llama_mcp_bridge.installer import _uninstall_bridge

    monkeypatch.setenv("LLAMA_MCP_HOME", str(tmp_path))
    (tmp_path / "bridge.ini").write_text(f"[bridge]\nworkspace = {tmp_path}\n", encoding="utf-8")
    (tmp_path / "models.ini").write_text("[*]\n", encoding="utf-8")
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "test.gguf").write_bytes(b"gguf")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "llama-server").write_text("bin")
    (tmp_path / "llama.cpp").mkdir()
    (tmp_path / "llama.cpp" / "CMakeLists.txt").write_text("cmake")

    existing_cron = "0 * * * * /usr/bin/keep\n@reboot nohup llama-server & # llama-mcp-bridge\n"
    with (
        patch("llama_mcp_bridge.installer.sys.platform", "linux"),
        patch("llama_mcp_bridge.installer.shutil.which", return_value=None),
        patch("llama_mcp_bridge.installer.LlamaSupervisor.stop"),
        patch("llama_mcp_bridge.installer.subprocess.run") as mock_run,
    ):
        mock_run.side_effect = [MagicMock(returncode=0, stdout=existing_cron), MagicMock(returncode=0)]
        _uninstall_bridge(dry_run=False, yes=True)

    assert not (tmp_path / "bin").exists()
    assert not (tmp_path / "llama.cpp").exists()
    assert (tmp_path / "bridge.ini").exists()
    assert (tmp_path / "models.ini").exists()
    assert (tmp_path / "models" / "test.gguf").exists()
    written_cron = mock_run.call_args_list[1].kwargs["input"]
    assert "llama-mcp-bridge" not in written_cron
    assert "/usr/bin/keep" in written_cron


def test_uninstall_dry_run_leaves_everything_untouched(monkeypatch, tmp_path: Path):
    from llama_mcp_bridge.installer import _uninstall_bridge

    monkeypatch.setenv("LLAMA_MCP_HOME", str(tmp_path))
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "llama-server").write_text("bin")
    (tmp_path / "bridge.ini").write_text(f"[bridge]\nworkspace = {tmp_path}\n", encoding="utf-8")

    with patch("llama_mcp_bridge.installer.subprocess.run") as mock_run:
        _uninstall_bridge(dry_run=True, yes=False)

    mock_run.assert_not_called()
    assert (tmp_path / "bin" / "llama-server").exists()
    assert (tmp_path / "bridge.ini").exists()


def test_download_tier_models_selective_digest_only(tmp_path: Path):
    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.installer import _download_tier_models

    payload = b"digest-gguf"
    get_resp = MagicMock()
    get_resp.__enter__.return_value = get_resp
    get_resp.status = 200
    get_resp.headers = {"Content-Length": str(len(payload))}
    get_resp.read.side_effect = [payload, b""]

    profile = SystemProfile("linux", "x86_64", "cpu", "CPU", 0.0, "", "linux", "6gb")
    with (
        patch("llama_mcp_bridge.installer.probe_model_metadata", return_value=(True, len(payload), None)),
        patch("llama_mcp_bridge.installer.urllib.request.urlopen", return_value=get_resp) as mock_url,
    ):
        _download_tier_models(profile, tmp_path, dry_run=False, presets=["digest"])

    mock_url.assert_called_once()
    assert (tmp_path / "LFM3-1.7B-Q8_0.gguf").read_bytes() == payload
    assert not (tmp_path / "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf").exists()


def test_download_tier_models_invalid_preset_raises(tmp_path: Path):
    import pytest

    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.installer import _download_tier_models

    profile = SystemProfile("linux", "x86_64", "cpu", "CPU", 0.0, "", "linux", "6gb")
    with pytest.raises(ValueError, match="Unknown preset"):
        _download_tier_models(profile, tmp_path, dry_run=False, presets=["nonexistent"])


def test_supervisor_unmocked_check_health_with_bridge_config():
    import requests

    from llama_mcp_bridge.config import BridgeConfig
    from llama_mcp_bridge.supervisor import LlamaSupervisor

    cfg = BridgeConfig(host="127.0.0.1", port=59999, request_timeout_s=90)
    sup = LlamaSupervisor(cfg)
    with patch("llama_mcp_bridge.supervisor.requests.get", side_effect=requests.exceptions.ConnectionError("refused")) as mock_get:
        status = sup.get_status()

    assert status["healthy"] is False
    mock_get.assert_called_once()
    assert mock_get.call_args.kwargs["timeout"] == 2.0


def test_auto_install_flattens_nested_dir_and_preserves_symlinks(tmp_path: Path):
    import io
    import json
    import os
    import tarfile

    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.installer import _auto_install_llama_server

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        bin_info = tarfile.TarInfo("llama-b12728/llama-server")
        bin_bytes = b"#!/bin/sh\n"
        bin_info.size = len(bin_bytes)
        tf.addfile(bin_info, io.BytesIO(bin_bytes))

        lib_info = tarfile.TarInfo("llama-b12728/libllama.so.1.0.0")
        lib_bytes = b"so"
        lib_info.size = len(lib_bytes)
        tf.addfile(lib_info, io.BytesIO(lib_bytes))

        sym_info = tarfile.TarInfo("llama-b12728/libllama.so")
        sym_info.type = tarfile.SYMTYPE
        sym_info.linkname = "libllama.so.1.0.0"
        tf.addfile(sym_info)
    tgz_bytes = buf.getvalue()

    rel_json = json.dumps(
        {
            "assets": [
                {
                    "name": "llama-b12728-bin-ubuntu-x64.tar.gz",
                    "browser_download_url": "https://example.com/b12728.tar.gz",
                }
            ]
        }
    ).encode("utf-8")

    r1 = MagicMock()
    r1.__enter__.return_value = r1
    r1.read.return_value = rel_json

    r2 = MagicMock()
    r2.__enter__.return_value = r2
    r2.read.return_value = tgz_bytes

    profile = SystemProfile("linux", "x86_64", "cpu", "CPU", 0.0, "", "ubuntu", "6gb")
    with patch("llama_mcp_bridge.installer.urllib.request.urlopen", side_effect=[r1, r2]):
        _auto_install_llama_server(profile, "prebuilt", tmp_path, dry_run=False, yes=True)

    target_bin = tmp_path / "bin" / "llama-server"
    assert target_bin.is_file()
    assert os.access(target_bin, os.X_OK)
    assert not (tmp_path / "bin" / "llama-b12728").exists()
    assert (tmp_path / "bin" / "libllama.so").is_symlink()


def test_merge_models_ini_no_duplicate_sections(tmp_path: Path):
    import configparser

    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.presets import generate_models_ini, merge_models_ini

    profile = SystemProfile("linux", "x86_64", "cpu", "CPU", 0.0, "", "ubuntu", "6gb")
    models_ini = tmp_path / "models.ini"
    models_ini.write_text(
        generate_models_ini(profile, models_dir=str(tmp_path / "models"), selected_presets=["digest"]),
        encoding="utf-8",
    )

    merged = merge_models_ini(models_ini, profile, "moderate", tmp_path / "models", ["digest", "coder"])
    assert merged.count("[digest]") == 1
    assert merged.count("[coder]") == 1

    cp = configparser.ConfigParser()
    cp.read_string(merged)
    assert cp.sections() == ["*", "digest", "coder"]


def test_remove_models_from_ini_protects_shared_gguf(tmp_path: Path):
    import configparser

    from llama_mcp_bridge.presets import remove_models_from_ini

    models_dir = tmp_path / "models"
    models_dir.mkdir()
    shared_gguf = models_dir / "shared.gguf"
    unshared_gguf = models_dir / "unique.gguf"
    shared_gguf.write_bytes(b"shared")
    unshared_gguf.write_bytes(b"unique")

    models_ini = tmp_path / "models.ini"
    models_ini.write_text(
        "[*]\nmodels-max = 2\n\n"
        "[coder]\nmodel = shared.gguf\n\n"
        "[analyst]\nmodel = shared.gguf\n\n"
        "[digest]\nmodel = unique.gguf\n",
        encoding="utf-8",
    )

    deleted = remove_models_from_ini(models_ini, ["coder", "digest"], models_dir, delete_files=True)
    assert shared_gguf.exists()
    assert not unshared_gguf.exists()
    assert deleted == ["unique.gguf"]

    cp = configparser.ConfigParser()
    cp.read(models_ini, encoding="utf-8")
    assert cp.sections() == ["*", "analyst"]


def test_run_doctor_passes_with_only_selected_presets_on_disk(tmp_path: Path, monkeypatch, capsys):
    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.installer import _run_doctor
    from llama_mcp_bridge.presets import generate_models_ini, get_tier_model_specs

    monkeypatch.setenv("LLAMA_MCP_HOME", str(tmp_path))
    models_dir = tmp_path / "models"
    models_dir.mkdir()

    digest_spec = get_tier_model_specs("6gb", presets=["digest"])[0]
    (models_dir / digest_spec["filename"]).write_bytes(b"gguf")

    llama_bin = tmp_path / "bin" / "llama-server"
    llama_bin.parent.mkdir()
    llama_bin.write_bytes(b"#!/bin/sh\n")
    llama_bin.chmod(0o755)

    profile = SystemProfile("linux", "x86_64", "cpu", "CPU", 0.0, "", "ubuntu", "6gb")
    (tmp_path / "models.ini").write_text(
        generate_models_ini(profile, models_dir=str(models_dir), selected_presets=["digest"]),
        encoding="utf-8",
    )
    (tmp_path / "bridge.ini").write_text(
        f"[bridge]\nhost = 127.0.0.1\nport = 8080\nllama_server_bin = {llama_bin}\nmodels_dir = {models_dir}\n",
        encoding="utf-8",
    )

    health_resp = MagicMock()
    health_resp.status_code = 200
    with (
        patch("llama_mcp_bridge.installer._find_llama_server", return_value=str(llama_bin)),
        patch("requests.get", return_value=health_resp),
    ):
        _run_doctor()

    out = capsys.readouterr().out
    assert "FAIL" not in out
    assert "All referenced files present in" in out


def test_select_presets_interactive_inline_live_cursor_and_size(capsys):
    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.models_cli import select_presets_interactive

    profile = SystemProfile("linux", "x86_64", "cpu", "CPU", 0.0, "", "ubuntu", "6gb")
    with (
        patch("llama_mcp_bridge.models_cli.sys.stdin.isatty", return_value=True),
        patch("llama_mcp_bridge.models_cli._read_key", side_effect=["DOWN", " ", "\r"]),
    ):
        chosen = select_presets_interactive(profile, initial_selected=["digest"])

    assert chosen == ["digest", "coder-fast"]
    out = capsys.readouterr().out
    assert "Size" in out
    assert "> [x]" in out
    assert "  [x]" in out
    assert "  [ ]" in out


def test_linux_systemd_service_auto_activates(monkeypatch, tmp_path: Path):
    from llama_mcp_bridge.installer import _generate_service_file

    monkeypatch.setenv("LLAMA_MCP_HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    orig_exists = Path.exists

    def fake_exists(self: Path) -> bool:
        if self.as_posix() == "/run/systemd/system":
            return True
        return orig_exists(self)

    with (
        patch("llama_mcp_bridge.installer.sys.platform", "linux"),
        patch("llama_mcp_bridge.installer.shutil.which", return_value="/usr/bin/systemctl"),
        patch.object(Path, "exists", fake_exists),
        patch("llama_mcp_bridge.installer.subprocess.run") as mock_run,
    ):
        _generate_service_file(dry_run=False)

    assert (tmp_path / ".config" / "systemd" / "user" / "llama-server.service").exists()
    calls = [c.args[0] for c in mock_run.call_args_list]
    assert ["systemctl", "--user", "daemon-reload"] in calls
    assert ["systemctl", "--user", "enable", "--now", "llama-server.service"] in calls


def test_ps_main_filters_configured_presets(capsys, monkeypatch, tmp_path: Path):
    from llama_mcp_bridge.installer import ps_main

    monkeypatch.setenv("LLAMA_MCP_HOME", str(tmp_path))
    (tmp_path / "models.ini").write_text("[*]\nmodels-max = 1\n\n[digest]\nmodel = d.gguf\n", encoding="utf-8")
    (tmp_path / "bridge.ini").write_text(f"[bridge]\nmodels_ini = {tmp_path / 'models.ini'}\n", encoding="utf-8")

    with patch("llama_mcp_bridge.supervisor.LlamaSupervisor._check_health", return_value=False):
        ps_main()

    out = capsys.readouterr().out
    assert "digest" in out
    assert "vision-deep" not in out


def test_models_list_includes_mmproj_size_and_filename_and_distinct_6gb_coder(capsys, monkeypatch, tmp_path: Path):
    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.models_cli import _print_models_list

    monkeypatch.setenv("COLUMNS", "160")

    profile = SystemProfile("linux", "x86_64", "cpu", "CPU", 0.0, "", "ubuntu", "6gb")
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    _print_models_list(profile, tmp_path / "models.ini", models_dir)

    out = capsys.readouterr().out
    assert "1.6 GB" in out
    assert "3.2 GB" in out
    assert "(+ mmproj-model-f16.gguf)" in out
    assert "(+ mmproj-F16.gguf)" in out
    assert "1.1 GB" in out
    assert "2.0 GB" in out


def test_uninstall_silences_systemctl_stderr(monkeypatch, tmp_path: Path):
    import subprocess

    from llama_mcp_bridge.installer import _uninstall_bridge

    monkeypatch.setenv("LLAMA_MCP_HOME", str(tmp_path))
    orig_exists = Path.exists

    def fake_exists(self: Path) -> bool:
        if self.as_posix() == "/run/systemd/system":
            return True
        return orig_exists(self)

    with (
        patch("llama_mcp_bridge.installer.sys.platform", "linux"),
        patch("llama_mcp_bridge.installer.shutil.which", return_value="/usr/bin/systemctl"),
        patch.object(Path, "exists", fake_exists),
        patch("llama_mcp_bridge.installer.subprocess.run") as mock_run,
    ):
        _uninstall_bridge(dry_run=False, yes=True)

    disable_calls = [
        c for c in mock_run.call_args_list if c.args[0] == ["systemctl", "--user", "disable", "--now", "llama-server.service"]
    ]
    assert len(disable_calls) == 1
    assert disable_calls[0].kwargs["stdout"] == subprocess.DEVNULL
    assert disable_calls[0].kwargs["stderr"] == subprocess.DEVNULL


def test_select_llama_release_asset_excludes_cudart():
    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.installer import _select_llama_release_asset

    profile = SystemProfile("windows", "AMD64", "nvidia", "RTX 3070", 8.0, "sm_86", "windows", "8gb")
    assets = [
        {"name": "cudart-llama-bin-win-cuda-12.4-x64.zip", "browser_download_url": "https://example.com/cudart.zip"},
        {"name": "llama-b1234-bin-win-cuda-12.4-x64.zip", "browser_download_url": "https://example.com/cuda.zip"},
    ]
    selected = _select_llama_release_asset(assets, profile)
    assert selected is not None
    assert selected["name"] == "llama-b1234-bin-win-cuda-12.4-x64.zip"

    only_cudart = [{"name": "cudart-llama-bin-win-cuda-12.4-x64.zip", "browser_download_url": "https://example.com/cudart.zip"}]
    assert _select_llama_release_asset(only_cudart, profile) is None


def test_auto_install_windows_nvidia_colocates_cudart_dlls(tmp_path: Path):
    import io
    import json
    import zipfile

    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.installer import _auto_install_llama_server

    main_buf = io.BytesIO()
    with zipfile.ZipFile(main_buf, "w") as zf:
        zf.writestr("llama-b1234/llama-server.exe", b"MZ")
    cudart_buf = io.BytesIO()
    with zipfile.ZipFile(cudart_buf, "w") as zf:
        zf.writestr("cudart64_12.dll", b"DLL1")
        zf.writestr("cublas64_12.dll", b"DLL2")

    rel_json = json.dumps(
        {
            "assets": [
                {"name": "cudart-llama-bin-win-cuda-12.4-x64.zip", "browser_download_url": "https://example.com/cudart.zip"},
                {"name": "llama-b1234-bin-win-cuda-12.4-x64.zip", "browser_download_url": "https://example.com/llama.zip"},
            ]
        }
    ).encode("utf-8")

    r_api = MagicMock()
    r_api.__enter__.return_value = r_api
    r_api.read.return_value = rel_json

    r_main = MagicMock()
    r_main.__enter__.return_value = r_main
    r_main.read.return_value = main_buf.getvalue()

    r_cudart = MagicMock()
    r_cudart.__enter__.return_value = r_cudart
    r_cudart.read.return_value = cudart_buf.getvalue()

    profile = SystemProfile("windows", "AMD64", "nvidia", "RTX 3070", 8.0, "sm_86", "windows", "8gb")
    with patch("llama_mcp_bridge.installer.urllib.request.urlopen", side_effect=[r_api, r_main, r_cudart]):
        _auto_install_llama_server(profile, "prebuilt", tmp_path, dry_run=False, yes=True)

    assert (tmp_path / "bin" / "llama-server.exe").is_file()
    assert (tmp_path / "bin" / "cudart64_12.dll").is_file()
    assert (tmp_path / "bin" / "cublas64_12.dll").is_file()


def test_auto_install_macos_preserves_dylib_metallib_and_codesigns(tmp_path: Path):
    import io
    import json
    import tarfile

    from llama_mcp_bridge.hardware import SystemProfile
    from llama_mcp_bridge.installer import _auto_install_llama_server

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for fname, payload in (
            ("build/bin/llama-server", b"MACHO"),
            ("build/bin/libllama.dylib", b"DYLIB"),
            ("build/bin/ggml-metal.metallib", b"METAL"),
        ):
            info = tarfile.TarInfo(fname)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))

    rel_json = json.dumps(
        {
            "assets": [
                {"name": "llama-b1234-bin-macos-arm64.tar.gz", "browser_download_url": "https://example.com/macos.tar.gz"}
            ]
        }
    ).encode("utf-8")

    r_api = MagicMock()
    r_api.__enter__.return_value = r_api
    r_api.read.return_value = rel_json

    r_tgz = MagicMock()
    r_tgz.__enter__.return_value = r_tgz
    r_tgz.read.return_value = buf.getvalue()

    profile = SystemProfile("darwin", "arm64", "apple", "Apple M2", 16.0, "", "macos", "16gb")
    with (
        patch("llama_mcp_bridge.installer.urllib.request.urlopen", side_effect=[r_api, r_tgz]),
        patch("llama_mcp_bridge.installer.subprocess.run") as mock_run,
    ):
        _auto_install_llama_server(profile, "prebuilt", tmp_path, dry_run=False, yes=True)

    assert (tmp_path / "bin" / "llama-server").is_file()
    assert (tmp_path / "bin" / "libllama.dylib").is_file()
    assert (tmp_path / "bin" / "ggml-metal.metallib").is_file()
    codesign_calls = [c.args[0] for c in mock_run.call_args_list if c.args[0][0] == "codesign"]
    assert any(c[:5] == ["codesign", "--force", "--deep", "-s", "-"] for c in codesign_calls)
