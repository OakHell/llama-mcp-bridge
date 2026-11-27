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
