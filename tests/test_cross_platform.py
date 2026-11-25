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


def test_supervisor_windows_popen_and_stop():
    import subprocess

    from llama_mcp_bridge.config import BridgeConfig
    from llama_mcp_bridge.supervisor import LlamaSupervisor

    sup = LlamaSupervisor(BridgeConfig(llama_server_bin="llama-server"))
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None

    with (
        patch("llama_mcp_bridge.supervisor.sys.platform", "win32"),
        patch("shutil.which", return_value="C:\\bin\\llama-server.exe"),
        patch("llama_mcp_bridge.supervisor.subprocess.Popen", return_value=mock_proc) as mock_popen,
        patch.object(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, create=True),
        patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
    ):
        assert sup._start_server() is True
        cmd = mock_popen.call_args.args[0]
        assert cmd[0].endswith("llama-server.exe")
        kwargs = mock_popen.call_args.kwargs
        assert kwargs["creationflags"] == (0x00000200 | 0x08000000)
        assert "start_new_session" not in kwargs

        assert sup.stop() is True
        mock_proc.terminate.assert_called_once()


def test_supervisor_posix_popen():
    import os

    from llama_mcp_bridge.config import BridgeConfig
    from llama_mcp_bridge.supervisor import LlamaSupervisor

    sup = LlamaSupervisor(BridgeConfig(llama_server_bin="llama-server"))
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None

    with (
        patch("llama_mcp_bridge.supervisor.sys.platform", "linux"),
        patch("shutil.which", return_value="/usr/bin/llama-server"),
        patch.object(os, "setsid", MagicMock(), create=True),
        patch("llama_mcp_bridge.supervisor.subprocess.Popen", return_value=mock_proc) as mock_popen,
    ):
        assert sup._start_server() is True
        cmd = mock_popen.call_args.args[0]
        assert cmd[0] == "llama-server"
        kwargs = mock_popen.call_args.kwargs
        assert kwargs.get("start_new_session") is True or kwargs.get("preexec_fn") is not None


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
    mock_run.assert_called_once()
    assert mock_run.call_args.args[0][0] == "schtasks"


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
        patch(
            "llama_mcp_bridge.installer.Prompt.ask",
            side_effect=[str(custom_ws), str(custom_ws / "models"), "skip", "moderate"],
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
        registry_files = {s["filename"]: s["url"] for s in specs}

        for section in parser.sections():
            for key in ("model", "mmproj"):
                if parser.has_option(section, key):
                    basename = Path(parser.get(section, key)).name
                    assert basename in registry_files, f"{basename} missing in {tier} registry"
                    assert registry_files[basename].startswith("https://huggingface.co/")
                    assert "/resolve/main/" in registry_files[basename]


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
    assert parser.get("vision-deep", "model") == "/models/MiniCPM-V-4_6-Q4_K_M.gguf"
    assert parser.get("vision-deep", "mmproj") == "/models/mmproj-model-f16.gguf"


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
