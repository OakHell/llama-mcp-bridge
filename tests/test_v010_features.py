"""Tests for v0.10.0 modularisation, medium edge cases, polish, and testing gaps."""

from __future__ import annotations

import asyncio
import configparser
import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llama_mcp_bridge.bridge import _register_configured_tools, mcp
from llama_mcp_bridge.config import BridgeConfig, load_config
from llama_mcp_bridge.discovery import (
    _build_prompt_with_files,
    _discover_ancestor_gitignores,
    _expand_target_files,
    _walk_dir_pruned,
)
from llama_mcp_bridge.hardware import SystemProfile
from llama_mcp_bridge.hygiene import clean_mcp_output
from llama_mcp_bridge.installer import (
    _cleanup_harness_mcp_configs,
    _persist_server_bin,
    _resolve_harness_config_paths,
    _run_doctor,
    _uninstall_bridge,
    ps_main,
)
from llama_mcp_bridge.installer import (
    main as installer_main,
)
from llama_mcp_bridge.presets import (
    _with_urls,
    merge_custom_preset_ini,
    resolve_hf_gguf_repo,
)
from llama_mcp_bridge.supervisor import LlamaSupervisor


# --- T12: Relative workspace path resolution in bridge.ini ---
def test_t12_load_config_relative_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws_home = tmp_path / "home"
    ws_home.mkdir()
    (ws_home / "bridge.ini").write_text("[bridge]\nworkspace = ./local\n", encoding="utf-8")
    monkeypatch.setenv("LLAMA_MCP_HOME", str(ws_home))

    cfg = load_config()
    expected_ws = ws_home / "local"
    assert Path(cfg.workspace) == expected_ws
    assert Path(cfg.models_dir) == expected_ws / "models"
    assert Path(cfg.models_ini) == expected_ws / "models.ini"


# --- T13: _discover_ancestor_gitignores with directory symlink loops ---
def test_t13_discover_ancestor_gitignores_symlink_loop(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = a / "b"
    b.mkdir(parents=True)
    (a / ".gitignore").write_text("*.log\n", encoding="utf-8")
    loop = b / "loop_to_a"
    try:
        os.symlink(a, loop, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation not permitted on this platform")

    specs = _discover_ancestor_gitignores(loop)
    assert any(base == a.resolve() for base, _ in specs)


# --- T14: _walk_dir_pruned with directory symlink loops ---
def test_t14_walk_dir_pruned_symlink_loop(tmp_path: Path) -> None:
    root = tmp_path / "root"
    sub = root / "sub"
    sub.mkdir(parents=True)
    (sub / "ok.py").write_text("x = 1\n", encoding="utf-8")
    try:
        os.symlink(root, sub / "back_to_root", target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation not permitted on this platform")

    seen: set[Path] = set()
    candidates: list[tuple[Path, str, bool]] = []
    _walk_dir_pruned(root, set(), set(), True, [], seen, candidates)
    rels = [rel for _, rel, _ in candidates]
    assert any("ok.py" in r for r in rels)


# --- T16 & M19: _persist_server_bin preserves sections and mixed-case keys ---
def test_t16_persist_server_bin_preserves_sections_and_case(tmp_path: Path) -> None:
    ini = tmp_path / "bridge.ini"
    ini.write_text(
        "[bridge]\nhost = 127.0.0.1\n\n[CustomSection]\nMixedCaseKey = PreserveMe\n",
        encoding="utf-8",
    )
    _persist_server_bin(tmp_path, "bin/llama-server")
    text = ini.read_text(encoding="utf-8")
    assert "[CustomSection]" in text
    assert "MixedCaseKey = PreserveMe" in text
    assert "llama_server_bin = bin/llama-server" in text


# --- T17: _expand_target_files precedence (include > gitignore > ignore > defaults) ---
@pytest.mark.parametrize(
    ("ignore", "include", "use_gitignore", "expected_names"),
    [
        (None, None, True, {"keep.py"}),
        (["keep.py"], None, True, set()),
        (["keep.py"], ["keep.py"], True, {"keep.py"}),
        (None, ["ignored.log", "node_modules/pkg.js"], True, {"keep.py", "ignored.log", "pkg.js"}),
        (None, None, False, {"keep.py", "ignored.log"}),
    ],
)
def test_t17_expand_target_files_precedence(
    tmp_path: Path,
    ignore: list[str] | None,
    include: list[str] | None,
    use_gitignore: bool,
    expected_names: set[str],
) -> None:
    (tmp_path / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (tmp_path / "keep.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_path / "ignored.log").write_text("log\n", encoding="utf-8")
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "pkg.js").write_text("js\n", encoding="utf-8")

    files, err = _expand_target_files(
        [str(tmp_path)],
        ignore=ignore,
        include=include,
        use_gitignore=use_gitignore,
    )
    assert err is None
    assert {f.name for f in files if f.name != ".gitignore"} == expected_names


# --- T18: _build_prompt_with_files budget & smallest-first sorting ---
@pytest.mark.parametrize(
    ("max_chars", "expect_omitted"),
    [
        (500, False),
        (10, True),
    ],
)
def test_t18_build_prompt_with_files_budget_and_order(
    tmp_path: Path, max_chars: int, expect_omitted: bool
) -> None:
    small = tmp_path / "a_small.txt"
    large = tmp_path / "b_large.txt"
    small.write_text("hi", encoding="utf-8")
    large.write_text("x" * 100, encoding="utf-8")

    combined, err = _build_prompt_with_files("do task", [str(large), str(small)], max_total_chars=max_chars)
    assert err is None
    assert combined.index("a_small.txt") < combined.index("b_large.txt")
    if expect_omitted:
        assert "TRUNCATED" in combined or "omitted_budget_exhausted" in combined


# --- T19: clean_mcp_output fuzz/chaos tests ---
@pytest.mark.parametrize(
    ("raw", "must_contain", "must_not_contain"),
    [
        ("<think>secret thought</think>\n```python\n<think>keep</think>\n```", "<think>keep</think>", "secret thought"),
        ("Thinking Process:\n1. Step\n---\n$$\nE = mc^2\n$$\n\nResult: E = mc^2.", "$$", "Thinking Process:"),
        ("<think>unclosed reasoning\nstill thinking", "unclosed thought block", ""),
        ("Answer is `42`.", "`42`", ""),
    ],
)
def test_t19_clean_mcp_output_chaos(raw: str, must_contain: str, must_not_contain: str) -> None:
    out = clean_mcp_output(raw)
    if must_contain:
        assert must_contain in out
    if must_not_contain:
        assert must_not_contain not in out


# --- T21: _uninstall_bridge across launchd, win32 schtasks, linux systemd, and BSD crontab ---
@pytest.mark.parametrize("plat", ["darwin", "win32", "linux", "freebsd14"])
def test_t21_uninstall_bridge_cross_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plat: str
) -> None:
    ws = tmp_path / "ws"
    (ws / "bin").mkdir(parents=True)
    (ws / "build").mkdir(parents=True)
    (ws / "models").mkdir(parents=True)
    (ws / "bridge.ini").write_text(f"[bridge]\nworkspace = {ws}\n", encoding="utf-8")
    (ws / "models.ini").write_text("[digest]\nmodel = m.gguf\n", encoding="utf-8")
    monkeypatch.setenv("LLAMA_MCP_HOME", str(ws))

    if plat == "darwin":
        plist = ws / "com.llama-server.plist"
        plist.write_text("<plist/>", encoding="utf-8")

    with (
        patch("llama_mcp_bridge.service_install.sys.platform", plat),
        patch("llama_mcp_bridge.service_install.Path.home", return_value=tmp_path),
        patch("llama_mcp_bridge.service_install.LlamaSupervisor.stop"),
        patch("llama_mcp_bridge.service_install.shutil.which", return_value="/usr/bin/systemctl" if plat == "linux" else None),
        patch("llama_mcp_bridge.service_install.subprocess.run") as mock_run,
    ):
        if plat == "linux":
            with patch.object(Path, "exists", return_value=True):
                _uninstall_bridge(dry_run=False, yes=True)
        else:
            _uninstall_bridge(dry_run=False, yes=True)

    assert not (ws / "bin").exists()
    assert not (ws / "build").exists()
    assert (ws / "bridge.ini").exists()
    assert (ws / "models.ini").exists()
    assert mock_run.called


# --- T22: --configure-client --apply + MCP tools/list integration ---
def test_t22_configure_client_apply_and_mcp_tools_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "bridge.ini").write_text(f"[bridge]\nworkspace = {ws}\n", encoding="utf-8")
    (ws / "models.ini").write_text("[digest]\nmodel = m.gguf\n\n[my_custom]\nmodel = c.gguf\n", encoding="utf-8")
    monkeypatch.setenv("LLAMA_MCP_HOME", str(ws))

    with (
        patch("llama_mcp_bridge.client_config.Path.home", return_value=tmp_path),
        patch("sys.argv", ["llama-mcp-install", "--configure-client", "claude-code", "--apply"]),
    ):
        installer_main()

    claude_cfg = json.loads((tmp_path / ".claude.json").read_text(encoding="utf-8"))
    assert "local-ai" in claude_cfg["mcpServers"]

    registered = _register_configured_tools(mcp)
    assert "ask_digest" in registered
    assert "ask_my_custom" in registered

    tools = asyncio.run(mcp.list_tools())
    tool_names = {t.name for t in tools}
    assert {"get_bridge_status", "ask_preset", "ask_digest", "ask_my_custom"}.issubset(tool_names)


# --- M1, M2, M8: Harness cleanup across all 6 file harnesses & empty mcpServers removal ---
def test_m1_m2_m8_cleanup_all_harnesses_and_cline_variants(tmp_path: Path) -> None:
    with patch("llama_mcp_bridge.client_config.sys.platform", "linux"):
        (tmp_path / ".config" / "Code - Insiders").mkdir(parents=True)
        (tmp_path / ".config" / "VSCodium").mkdir(parents=True)
        cline_paths = _resolve_harness_config_paths("cline", home_dir=tmp_path)
        assert len(cline_paths) == 3

        windsurf_cfg = tmp_path / ".codeium" / "windsurf" / "mcp_config.json"
        windsurf_cfg.parent.mkdir(parents=True)
        windsurf_cfg.write_text(json.dumps({"mcpServers": {"local-ai": {"command": "x"}}}), encoding="utf-8")

        zed_cfg = tmp_path / ".config" / "zed" / "settings.json"
        zed_cfg.parent.mkdir(parents=True)
        zed_cfg.write_text(
            json.dumps({"mcpServers": {"local-ai": {"command": "x"}, "other": {"command": "y"}}}),
            encoding="utf-8",
        )

        with patch("llama_mcp_bridge.client_config.Path.home", return_value=tmp_path):
            updated = _cleanup_harness_mcp_configs(dry_run=False)

    assert windsurf_cfg in updated
    assert zed_cfg in updated
    # M2: empty mcpServers key removed
    assert "mcpServers" not in json.loads(windsurf_cfg.read_text(encoding="utf-8"))
    # Other entries preserved
    zed_data = json.loads(zed_cfg.read_text(encoding="utf-8"))
    assert "local-ai" not in zed_data["mcpServers"]
    assert "other" in zed_data["mcpServers"]


# --- M4, M5, M15: Tier defaults, tier-aware quant, URL encoding ---
def test_m4_m5_m15_presets_tier_and_url_encoding(tmp_path: Path) -> None:
    prof_16gb = SystemProfile("linux", "x86_64", "nvidia", "RTX 4080", 16.0, "sm_89", "ubuntu", "16gb")
    ini_path = tmp_path / "models.ini"
    merge_custom_preset_ini(ini_path, prof_16gb, "moderate", tmp_path, "custom_one", tmp_path / "m.gguf")
    cp = configparser.ConfigParser()
    cp.read(ini_path, encoding="utf-8")
    assert cp.get("custom_one", "ctx-size") == "16384"
    assert cp.get("custom_one", "n-gpu-layers") == "-1"

    payload = json.dumps({"siblings": [{"rfilename": "model-Q4_K_M.gguf"}, {"rfilename": "model-Q6_K.gguf"}]}).encode()
    resp = MagicMock()
    resp.__enter__.return_value = resp
    resp.read.return_value = payload
    with patch("llama_mcp_bridge.presets.urllib.request.urlopen", return_value=resp):
        _, chosen, _ = resolve_hf_gguf_repo("org/model", tier="16gb")
    assert chosen == "model-Q6_K.gguf"

    urls = _with_urls([{"repo": "org/repo", "filename": "my model+v1.gguf"}])
    assert urls[0]["url"] == "https://huggingface.co/org/repo/resolve/main/my%20model%2Bv1.gguf"


# --- M17 & N4: Mtime-cached load_config & dataclass-derived field parsing ---
def test_m17_n4_load_config_mtime_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    ini = ws / "bridge.ini"
    ini.write_text("[bridge]\nport = 8081\nauto_spawn = false\n", encoding="utf-8")
    monkeypatch.setenv("LLAMA_MCP_HOME", str(ws))

    cfg1 = load_config()
    assert cfg1.port == 8081
    assert cfg1.auto_spawn is False

    # Mutate returned copy; cache should not be corrupted
    cfg1.port = 9999
    assert load_config().port == 8081

    # Modify file with distinct mtime_ns
    ini.write_text("[bridge]\nport = 8082\nauto_spawn = true\n", encoding="utf-8")
    st = ini.stat()
    os.utime(ini, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    cfg2 = load_config()
    assert cfg2.port == 8082
    assert cfg2.auto_spawn is True


# --- N7, N8, N9, M11, M12: --doctor --json, --ps --json, workspace & tool checks ---
def test_n7_n8_n9_m11_m12_doctor_and_ps_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = tmp_path / "ws"
    models = ws / "models"
    models.mkdir(parents=True)
    gguf = models / "m.gguf"
    gguf.write_bytes(b"GGUF")
    srv = ws / ("llama-server.exe" if sys.platform == "win32" else "llama-server")
    srv.write_text("#!/bin/sh\n", encoding="utf-8")
    srv.chmod(0o755)
    (ws / "bridge.ini").write_text(f"[bridge]\nworkspace = {ws}\nllama_server_bin = {srv}\n", encoding="utf-8")
    (ws / "models.ini").write_text(f"[digest]\nmodel = {gguf}\n", encoding="utf-8")
    monkeypatch.setenv("LLAMA_MCP_HOME", str(ws))

    with (
        patch("llama_mcp_bridge.installer.subprocess.run", return_value=MagicMock(returncode=0)),
        patch(
            "llama_mcp_bridge.installer.LlamaSupervisor.get_status",
            return_value={"healthy": True, "server_url": "http://127.0.0.1:8080", "tier": "8gb", "residency": "moderate", "models": {"digest": "loaded"}, "configured_presets": ["digest"]},
        ),
    ):
        rc = _run_doctor(json_output=True)
        assert rc == 0
        doc_json = json.loads(capsys.readouterr().out)
        assert doc_json["passed"] is True
        assert doc_json["llama_mcp_home_active"] is True
        assert any(c["check"] == "Preset tools" and c["status"] == "PASS" for c in doc_json["checks"])

        ps_main(json_output=True)
        ps_json = json.loads(capsys.readouterr().out)
        assert ps_json["workspace"] == str(ws)
        assert ps_json["llama_mcp_home_active"] is True
        assert ps_json["presets"]["digest"] == "loaded"


# --- C4, C6, C7: --version, server_log, --verify-models --dry-run ---
def test_c4_c6_c7_version_server_log_and_verify_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    custom_log = tmp_path / "logs" / "custom.log"
    cfg = BridgeConfig(workspace=str(tmp_path), server_log=str(custom_log))
    sup = LlamaSupervisor(cfg)
    mock_proc = MagicMock(pid=1234, stderr=io.BytesIO(b""))
    with patch("llama_mcp_bridge.supervisor.subprocess.Popen", return_value=mock_proc):
        assert sup._start_server() is True
    assert custom_log.exists()

    with (
        patch("llama_mcp_bridge.installer.verify_tier_models") as mock_verify,
        patch("sys.argv", ["llama-mcp-install", "--verify-models", "--dry-run"]),
    ):
        installer_main()
        mock_verify.assert_not_called()
    assert "[DRY-RUN]" in capsys.readouterr().out

    with patch("sys.argv", ["llama-mcp-install", "--version"]):
        with pytest.raises(SystemExit) as exc:
            installer_main()
        assert exc.value.code == 0
    assert "llama-mcp-install" in capsys.readouterr().out
