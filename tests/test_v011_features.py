"""Tests for v0.11.0 Ollama importer, diagnostics, config introspection, and opt-in live test."""

from __future__ import annotations

import configparser
import json
import os
import struct
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from llama_mcp_bridge.config import BridgeConfig
from llama_mcp_bridge.config_cli import check_ini_file
from llama_mcp_bridge.config_cli import main as config_cli_main
from llama_mcp_bridge.diagnostics import (
    check_for_update,
    diff_config_against_defaults,
    export_merged_config,
    get_model_info,
    run_profile_benchmark,
    run_selftest,
)
from llama_mcp_bridge.hardware import SystemProfile
from llama_mcp_bridge.installer import _run_doctor
from llama_mcp_bridge.installer import main as installer_main
from llama_mcp_bridge.models_cli import main as models_cli_main
from llama_mcp_bridge.ollama_import import import_ollama_models
from llama_mcp_bridge.presets import prune_unreferenced_models, verify_tier_models


def _build_synthetic_gguf(path: Path, arch: str = "llama", n_layers: int = 8) -> None:
    """Write a minimal valid GGUF v3 binary header for offline header-parser tests."""
    def _str(s: str) -> bytes:
        b = s.encode("utf-8")
        return struct.pack("<Q", len(b)) + b

    kv_pairs = [
        (_str("general.architecture"), struct.pack("<I", 8) + _str(arch)),
        (_str(f"{arch}.block_count"), struct.pack("<II", 4, n_layers)),
        (_str(f"{arch}.context_length"), struct.pack("<II", 4, 8192)),
        (_str(f"{arch}.attention.head_count_kv"), struct.pack("<II", 4, 8)),
        (_str(f"{arch}.attention.key_length"), struct.pack("<II", 4, 128)),
        (_str(f"{arch}.attention.value_length"), struct.pack("<II", 4, 128)),
    ]
    header = struct.pack("<4sIQQ", b"GGUF", 3, 0, len(kv_pairs))
    path.write_bytes(header + b"".join(k + v for k, v in kv_pairs))


# --- F5: Ollama manifest parser & zero-copy blob symlinker ---
def test_f5_import_ollama_symlinks_tunes_and_handles_edge_cases(tmp_path: Path) -> None:
    ollama_dir = tmp_path / "ollama"
    manifests = ollama_dir / "manifests" / "registry.ollama.ai" / "library"
    blobs = ollama_dir / "blobs"
    blobs.mkdir(parents=True)

    # 1. Valid GGUF + mmproj projector
    gguf_blob = blobs / "sha256-aaa111"
    _build_synthetic_gguf(gguf_blob)
    proj_blob = blobs / "sha256-bbb222"
    proj_blob.write_bytes(b"GGUFproj")
    m_valid = manifests / "qwen2.5-coder" / "7b"
    m_valid.parent.mkdir(parents=True)
    m_valid.write_text(
        json.dumps({
            "layers": [
                {"mediaType": "application/vnd.ollama.image.model", "digest": "sha256:aaa111"},
                {"mediaType": "application/vnd.ollama.image.projector", "digest": "sha256:bbb222"},
            ]
        }),
        encoding="utf-8",
    )

    # 2. Split multi-blob model (must be rejected)
    m_split = manifests / "split-model" / "latest"
    m_split.parent.mkdir(parents=True)
    m_split.write_text(
        json.dumps({
            "layers": [
                {"mediaType": "application/vnd.ollama.image.model", "digest": "sha256:aaa111"},
                {"mediaType": "application/vnd.ollama.image.model", "digest": "sha256:ccc333"},
            ]
        }),
        encoding="utf-8",
    )

    # 3. Safetensors-backed Ollama model (Invalid GGUF magic -> warning, no crash)
    st_blob = blobs / "sha256-ddd444"
    st_blob.write_bytes(b"SAFETENSORS_HEADER_BYTES")
    m_safe = manifests / "safe-model" / "v1"
    m_safe.parent.mkdir(parents=True)
    m_safe.write_text(
        json.dumps({"layers": [{"mediaType": "application/vnd.ollama.image.model", "digest": "sha256:ddd444"}]}),
        encoding="utf-8",
    )

    models_dir = tmp_path / "ws" / "models"
    models_ini = tmp_path / "ws" / "models.ini"
    profile = SystemProfile("linux", "x86_64", "nvidia", "RTX 3070", 8.0, "sm_86", "ubuntu", "8gb")

    # Dry-run creates nothing
    dry_report = import_ollama_models(ollama_dir, models_dir, models_ini, profile, dry_run=True)
    assert len(dry_report.imported) == 1
    assert not models_dir.exists()
    assert not models_ini.exists()

    try:
        report = import_ollama_models(ollama_dir, models_dir, models_ini, profile, dry_run=False)
    except RuntimeError as exc:
        if "symlink" in str(exc).lower():
            pytest.skip("Symlink creation not permitted on this platform")
        raise

    assert len(report.imported) == 1
    entry = report.imported[0]
    assert entry.symlink_path.is_symlink()
    assert entry.symlink_path.resolve() == gguf_blob.resolve()
    assert entry.mmproj_symlink is not None and entry.mmproj_symlink.is_symlink()
    assert any("split" in w.lower() for w in report.warnings)
    assert any("safe-model-v1" in w for w in report.warnings)

    cp = configparser.ConfigParser()
    cp.read(models_ini, encoding="utf-8")
    assert cp.has_section(entry.preset_name)
    assert cp.getint(entry.preset_name, "ctx-size") >= 2048


# --- F4: 1-hour TTL cache for verify_tier_models ---
def test_f4_verify_tier_models_ttl_cache(tmp_path: Path) -> None:
    cache_path = tmp_path / ".cache" / "verify_models.json"
    with patch("llama_mcp_bridge.presets.verify_model_url", return_value=(True, 1024)) as mock_verify:
        res1 = verify_tier_models("6gb", cache_path=cache_path, use_cache=True, ttl_seconds=3600)
        assert mock_verify.call_count > 0
        first_calls = mock_verify.call_count

        # Second call within TTL must hit cache (zero additional verify_model_url calls)
        res2 = verify_tier_models("6gb", cache_path=cache_path, use_cache=True, ttl_seconds=3600)
        assert mock_verify.call_count == first_calls
        assert res2 == res1

        # use_cache=False bypasses cache
        verify_tier_models("6gb", cache_path=cache_path, use_cache=False, ttl_seconds=3600)
        assert mock_verify.call_count > first_calls


# --- F3: --verify-all combines --verify-models and --doctor ---
def test_f3_installer_verify_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLAMA_MCP_HOME", str(tmp_path))
    with (
        patch("llama_mcp_bridge.installer.verify_tier_models", return_value=[{"tier": "8gb", "filename": "m.gguf", "ok": True, "size_bytes": 100, "suggestions": []}]) as mock_v,
        patch("llama_mcp_bridge.installer._run_doctor", return_value=0) as mock_d,
        patch("sys.argv", ["llama-mcp-install", "--verify-all", "--tier", "8gb"]),
    ):
        with pytest.raises(SystemExit) as exc:
            installer_main()
        assert exc.value.code == 0
        mock_v.assert_called_once()
        mock_d.assert_called_once()


# --- F9: --doctor --verify-sha ---
def test_f9_doctor_verify_sha_detects_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    ws = tmp_path / "ws"
    models = ws / "models"
    models.mkdir(parents=True)
    # Use a known curated filename from TIER_MODEL_REGISTRY
    curated_gguf = models / "LFM3-1.7B-Q8_0.gguf"
    curated_gguf.write_bytes(b"corrupted-content")
    srv = ws / ("llama-server.exe" if sys.platform == "win32" else "llama-server")
    srv.write_text("#!/bin/sh\n", encoding="utf-8")
    srv.chmod(0o755)
    (ws / "bridge.ini").write_text(f"[bridge]\nworkspace = {ws}\nllama_server_bin = {srv}\n", encoding="utf-8")
    (ws / "models.ini").write_text(f"[digest]\nmodel = {curated_gguf}\n", encoding="utf-8")
    monkeypatch.setenv("LLAMA_MCP_HOME", str(ws))

    with (
        patch("llama_mcp_bridge.installer.subprocess.run", return_value=MagicMock(returncode=0)),
        patch("llama_mcp_bridge.installer.probe_model_metadata", return_value=(True, 17, "0" * 64)),
        patch(
            "llama_mcp_bridge.installer.LlamaSupervisor.get_status",
            return_value={"healthy": True, "server_url": "http://127.0.0.1:8080", "tier": "8gb", "residency": "moderate", "models": {}},
        ),
    ):
        rc = _run_doctor(json_output=True, verify_sha=True)
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    sha_checks = [c for c in out["checks"] if c["check"] == "GGUF SHA-256"]
    assert sha_checks and sha_checks[0]["status"] == "FAIL"


# --- F10: llama-mcp-models --prune ---
def test_f10_prune_unreferenced_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path / "ws"
    models = ws / "models"
    models.mkdir(parents=True)
    kept = models / "kept.gguf"
    orphan = models / "orphan.gguf"
    kept.write_bytes(b"keep")
    orphan.write_bytes(b"drop")
    ini = ws / "models.ini"
    ini.write_text(f"[digest]\nmodel = {kept}\n", encoding="utf-8")
    (ws / "bridge.ini").write_text(f"[bridge]\nworkspace = {ws}\nmodels_dir = {models}\nmodels_ini = {ini}\n", encoding="utf-8")
    monkeypatch.setenv("LLAMA_MCP_HOME", str(ws))

    assert prune_unreferenced_models(ini, models, dry_run=True) == ["orphan.gguf"]
    assert orphan.exists()

    with patch("sys.argv", ["llama-mcp-models", "--prune", "--yes"]):
        models_cli_main()
    assert kept.exists()
    assert not orphan.exists()


# --- F8, F11, F16, F19, F21: diagnostics & config introspection ---
def test_f8_f11_f16_f19_f21_diagnostics_and_config_introspection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    models = tmp_path / "models"
    models.mkdir()
    gguf = models / "custom.gguf"
    _build_synthetic_gguf(gguf)
    ini = tmp_path / "models.ini"
    ini.write_text(f"[*]\nparallel = 1\n\n[digest]\nmodel = {gguf}\nctx-size = 4096\n", encoding="utf-8")

    # F8: export & diff
    exported = export_merged_config(ini)
    assert "parallel = 1" in exported
    assert "ctx-size = 4096" in exported
    prof = SystemProfile("linux", "x86_64", "cpu", "CPU", 0.0, "", "linux", "6gb")
    diff_out = diff_config_against_defaults(ini, prof, str(models), "moderate")
    assert "--- defaults/models.ini" in diff_out

    # F19: get_model_info
    info = get_model_info(ini, "digest")
    assert info["flags"]["parallel"] == "1"
    assert info["gguf"]["architecture"] == "llama"

    # F21: check_for_update
    git_out = "abc123\trefs/tags/v0.10.0\ndef456\trefs/tags/v99.0.0\n"
    with patch("llama_mcp_bridge.diagnostics.subprocess.run", return_value=MagicMock(returncode=0, stdout=git_out, stderr="")):
        cur, latest, has_update = check_for_update()
    assert cur
    assert latest == "v99.0.0"
    assert has_update is True

    # F16: run_selftest
    with patch("llama_mcp_bridge.bridge.ask_digest", return_value="pong"):
        assert run_selftest(json_output=True) == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True

    with patch("llama_mcp_bridge.bridge.ask_digest", return_value="Error: offline"):
        assert run_selftest(json_output=True) == 1
    assert json.loads(capsys.readouterr().out)["passed"] is False

    # F11: run_profile_benchmark
    cfg = BridgeConfig(workspace=str(tmp_path), models_ini=str(ini), models_dir=str(models))
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"usage": {"completion_tokens": 12}, "choices": [{"message": {"content": "local benchmark ready."}}]}
    with (
        patch("llama_mcp_bridge.diagnostics.detect_hardware", return_value=prof),
        patch("llama_mcp_bridge.diagnostics.LlamaSupervisor.ensure_running", return_value=True),
        patch("llama_mcp_bridge.diagnostics.requests.post", return_value=mock_resp),
    ):
        assert run_profile_benchmark(cfg, json_output=True) == 0
    bench_json = json.loads(capsys.readouterr().out)
    assert bench_json["results"][0]["ok"] is True
    assert bench_json["results"][0]["tokens_per_s"] > 0


# --- F18: llama-mcp-config check <path> ---
def test_f18_config_cli_check(tmp_path: Path) -> None:
    good_bridge = tmp_path / "bridge.ini"
    models_ini = tmp_path / "models.ini"
    gguf = tmp_path / "m.gguf"
    gguf.write_bytes(b"GGUF")
    models_ini.write_text(f"[digest]\nmodel = {gguf}\n", encoding="utf-8")
    good_bridge.write_text(f"[bridge]\nresidency = moderate\nport = 8080\nmodels_ini = {models_ini}\n", encoding="utf-8")

    assert check_ini_file(good_bridge) == []
    assert check_ini_file(models_ini) == []

    bad_bridge = tmp_path / "bad_bridge.ini"
    bad_bridge.write_text("[bridge]\nresidency = invalid\nport = 99999\n", encoding="utf-8")
    errs = check_ini_file(bad_bridge, kind="bridge")
    assert len(errs) == 2

    with patch("sys.argv", ["llama-mcp-config", "check", str(good_bridge)]):
        config_cli_main()


# --- T23: Opt-in live llama-server binary test ---
@pytest.mark.live_server
def test_t23_live_llama_server_version_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    server_bin = os.environ.get("LLAMA_SERVER_BIN")
    if server_bin:
        res = subprocess.run([server_bin, "--version"], capture_output=True, text=True, timeout=10, check=False)
        assert res.returncode == 0
        return

    ws = tmp_path / "live_sim"
    ws.mkdir()
    (ws / "models.ini").write_text("[digest]\nmodel = m.gguf\n", encoding="utf-8")
    (ws / "bridge.ini").write_text(f"[bridge]\nworkspace = {ws}\n", encoding="utf-8")
    monkeypatch.setenv("LLAMA_MCP_HOME", str(ws))

    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"choices": [{"message": {"content": "pong"}}]}
    with (
        patch("llama_mcp_bridge.bridge.LlamaSupervisor.ensure_running", return_value=True),
        patch("llama_mcp_bridge.bridge.requests.post", return_value=mock_resp),
    ):
        assert run_selftest(json_output=False) == 0


def test_load_config_invalidates_cache_when_mtime_ns_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ensure sub-tick Windows NTFS overwrites with identical st_mtime_ns still re-parse."""
    from llama_mcp_bridge.config import load_config

    cfg_home = tmp_path / "ntfs_cfg"
    cfg_home.mkdir()
    ini_path = cfg_home / "bridge.ini"
    monkeypatch.setenv("LLAMA_MCP_HOME", str(cfg_home))

    fixed_mtime_ns = 1_700_000_000_000_000_000
    ini_path.write_text("[bridge]\nresidency = snappy\n", encoding="utf-8")
    os.utime(ini_path, ns=(fixed_mtime_ns, fixed_mtime_ns))
    assert load_config().residency == "snappy"

    ini_path.write_text("[bridge]\nresidency = invalid_mode\n", encoding="utf-8")
    os.utime(ini_path, ns=(fixed_mtime_ns, fixed_mtime_ns))
    with pytest.raises(ValueError, match="Invalid residency profile"):
        load_config()
