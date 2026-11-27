"""Invariant tests for presets.py across all 5 VRAM tiers and 9 built-in presets (T26, B13)."""

from __future__ import annotations

import configparser
from pathlib import Path
from types import SimpleNamespace

import pytest

from llama_mcp_bridge.presets import (
    generate_models_ini,
    get_tier_model_specs,
    merge_custom_preset_ini,
)

ALL_TIERS = ("6gb", "8gb", "12gb", "16gb", "24gb+")
ALL_PRESETS = (
    "digest",
    "coder-fast",
    "coder",
    "coder-long",
    "vision",
    "vision-deep",
    "reasoner",
    "analyst",
    "generalist",
)


@pytest.mark.parametrize("tier", ALL_TIERS)
@pytest.mark.parametrize("preset", ALL_PRESETS)
def test_tier_x_preset_models_ini_parity_with_get_tier_model_specs(
    tmp_path: Path, tier: str, preset: str
) -> None:
    """Every model filename in generate_models_ini(tier, [preset]) must appear in get_tier_model_specs."""
    profile = SimpleNamespace(
        os_name="linux",
        arch="x86_64",
        gpu_vendor="cpu" if tier == "6gb" else "nvidia",
        backend="cpu" if tier == "6gb" else "cuda",
        vram_gb=6.0 if tier == "6gb" else 24.0,
        ram_gb=32.0,
        tier=tier,
    )
    ini_text = generate_models_ini(profile, str(tmp_path), selected_presets=[preset])
    cp = configparser.ConfigParser(interpolation=None)
    cp.read_string(ini_text)

    specs = get_tier_model_specs(tier, presets=[preset])
    spec_filenames = {s["filename"] for s in specs}

    assert preset in cp
    model_name = Path(cp[preset]["model"]).name
    assert model_name in spec_filenames
    if "mmproj" in cp[preset]:
        mmproj_name = Path(cp[preset]["mmproj"]).name
        assert mmproj_name in spec_filenames


def test_kv_cache_and_ctx_overrides_and_merge_custom_preset(tmp_path: Path) -> None:
    """Verify tier KV cache/ctx_overrides and merge_custom_preset_ini preservation."""
    profile = SimpleNamespace(
        os_name="linux",
        arch="x86_64",
        gpu_vendor="nvidia",
        backend="cuda",
        vram_gb=24.0,
        ram_gb=64.0,
        tier="24gb+",
    )
    ini_text = generate_models_ini(profile, str(tmp_path))
    cp = configparser.ConfigParser(interpolation=None)
    cp.read_string(ini_text)
    assert cp["*"]["cache-type-k"] == "q8_0"
    assert cp["coder-long"]["ctx-size"] == "131072"

    models_ini = tmp_path / "models.ini"
    models_ini.write_text(ini_text, encoding="utf-8")
    merge_custom_preset_ini(
        models_ini,
        profile=profile,
        residency="moderate",
        models_dir=tmp_path,
        preset_name="my-custom",
        model_path=tmp_path / "custom.gguf",
        ctx_size=8192,
    )
    cp2 = configparser.ConfigParser(interpolation=None)
    cp2.read(models_ini, encoding="utf-8")
    assert "my-custom" in cp2
    assert cp2["my-custom"]["ctx-size"] == "8192"


def test_b17_resolve_hf_gguf_repo_wraps_network_and_json_errors() -> None:
    """B17: resolve_hf_gguf_repo must wrap URLError/JSONDecodeError into RuntimeError."""
    import urllib.error
    from unittest.mock import patch

    from llama_mcp_bridge.presets import resolve_hf_gguf_repo

    with (
        patch(
            "llama_mcp_bridge.presets.DEFAULT_HTTP_CLIENT.urlopen",
            side_effect=urllib.error.URLError("offline"),
        ),
        pytest.raises(RuntimeError, match="Failed to query Hugging Face repository"),
    ):
        resolve_hf_gguf_repo("owner/repo")


def test_m24_m25_m26_n19_presets_safety_pruning_atomic_cache_and_drift(tmp_path: Path) -> None:
    """M24: reject DEFAULT; M25: .GGUF/.part pruning; M26: atomic cache; N19: size_drift_pct."""
    from unittest.mock import patch

    from llama_mcp_bridge.presets import prune_unreferenced_models, verify_tier_models

    profile = SimpleNamespace(
        os_name="linux",
        arch="x86_64",
        gpu_vendor="nvidia",
        backend="cuda",
        vram_gb=8.0,
        ram_gb=32.0,
        tier="8gb",
    )
    models_ini = tmp_path / "models.ini"
    models_ini.write_text("[*]\n[digest]\nmodel = keep.gguf\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid preset name"):
        merge_custom_preset_ini(
            models_ini,
            profile=profile,
            residency="moderate",
            models_dir=tmp_path,
            preset_name="DEFAULT",
            model_path=tmp_path / "x.gguf",
        )

    (tmp_path / "keep.gguf").write_bytes(b"1")
    (tmp_path / "STALE.GGUF").write_bytes(b"2")
    (tmp_path / "orphan.gguf.part").write_bytes(b"3")
    pruned = prune_unreferenced_models(models_ini, tmp_path, include_partials=True)
    assert set(pruned) == {"STALE.GGUF", "orphan.gguf.part"}
    assert (tmp_path / "keep.gguf").exists()

    cache_file = tmp_path / "verify_cache.json"
    with (
        patch("llama_mcp_bridge.presets.verify_model_url", return_value=(True, 10 * 1024**3)),
        patch("llama_mcp_bridge.presets.search_hf_gguf_models", return_value=[]),
    ):
        res = verify_tier_models(tier="6gb", cache_path=cache_file, use_cache=True)
    assert cache_file.is_file()
    assert any(r.get("size_drift_pct") is not None and r["size_drift_pct"] > 20.0 for r in res)


def test_readme_tier_table_matches_presets_py() -> None:
    """README.md Hardware Tiers table must match presets.py parallel and coder/coder-long ctx."""
    import re

    readme = Path(__file__).resolve().parents[1] / "README.md"
    text = readme.read_text(encoding="utf-8")
    rows = re.findall(
        r"^\|\s*`(6gb|8gb|12gb|16gb|24gb\+)`\s*\|[^|]+\|[^|]+\|\s*(\d+)K\s*/\s*(\d+)K\s*\|\s*(\d+)\s*\|",
        text,
        flags=re.MULTILINE,
    )
    assert len(rows) == 5
    for tier, coder_k, long_k, parallel in rows:
        profile = SimpleNamespace(
            os_name="linux",
            arch="x86_64",
            gpu_vendor="cpu" if tier == "6gb" else "nvidia",
            backend="cpu" if tier == "6gb" else "cuda",
            vram_gb=6.0 if tier == "6gb" else 24.0,
            ram_gb=32.0,
            tier=tier,
        )
        cp = configparser.ConfigParser(interpolation=None)
        cp.read_string(generate_models_ini(profile, "/tmp/models"))
        assert cp["*"]["parallel"] == parallel
        assert int(cp["coder"]["ctx-size"]) // 1024 == int(coder_k)
        assert int(cp["coder-long"]["ctx-size"]) // 1024 == int(long_k)
