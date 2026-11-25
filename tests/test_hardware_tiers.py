import configparser
from unittest.mock import patch

import pytest

from llama_mcp_bridge.hardware import SystemProfile, classify_vram_tier, detect_hardware
from llama_mcp_bridge.presets import generate_models_ini


def test_golden_8gb_sm86_matches_reference_models_ini():
    profile = SystemProfile(
        os_name="linux",
        arch="x86_64",
        gpu_vendor="nvidia",
        gpu_name="NVIDIA GeForce RTX 3070",
        vram_gb=8.0,
        compute_cap="sm_86",
        distro_family="ubuntu",
        tier="8gb",
    )
    generated = generate_models_ini(profile, models_dir="/mnt/ai/models")

    gen_parser = configparser.ConfigParser()
    gen_parser.read_string(generated)

    expected_sections = {
        "*",
        "coder-fast",
        "coder",
        "coder-long",
        "vision-deep",
        "vision",
        "reasoner",
        "analyst",
        "generalist",
        "digest",
    }
    assert set(gen_parser.sections()) == expected_sections
    assert gen_parser.get("*", "n-gpu-layers") == "-1"
    assert gen_parser.get("*", "flash-attn") == "on"
    assert gen_parser.get("*", "cache-type-k") == "q8_0"
    assert gen_parser.get("*", "cache-type-v") == "q8_0"
    assert gen_parser.get("coder-fast", "ctx-size") == "16384"
    assert gen_parser.get("coder-long", "ctx-size") == "32768"
    assert gen_parser.get("coder-long", "cache-type-v") == "q4_0"
    assert gen_parser.get("vision-deep", "no-mmproj-offload") == "true"
    assert gen_parser.get("digest", "ctx-size") == "65536"


@pytest.mark.parametrize(
    "vram_gb,expected_tier",
    [
        (4.0, "6gb"),
        (6.0, "6gb"),
        (8.0, "8gb"),
        (12.0, "12gb"),
        (16.0, "16gb"),
        (24.0, "24gb+"),
        (48.0, "24gb+"),
    ],
)
def test_classify_vram_tier(vram_gb: float, expected_tier: str):
    assert classify_vram_tier(vram_gb) == expected_tier


@pytest.mark.parametrize(
    "sys_name,arch,probe_target,probe_return,expected_vendor,expected_tier",
    [
        ("Windows", "AMD64", "llama_mcp_bridge.hardware._probe_nvidia", ("RTX 3070", 8.0, "sm_86"), "nvidia", "8gb"),
        ("Darwin", "arm64", "llama_mcp_bridge.hardware._probe_apple", ("Apple Silicon", 18.0), "apple", "16gb"),
        ("Linux", "x86_64", "llama_mcp_bridge.hardware._probe_amd", ("AMD ROCm", 16.0), "amd", "16gb"),
        ("FreeBSD", "amd64", None, None, "cpu", "6gb"),
    ],
)
def test_detect_hardware_cross_platform(
    sys_name: str,
    arch: str,
    probe_target: str | None,
    probe_return,
    expected_vendor: str,
    expected_tier: str,
):
    with (
        patch("platform.system", return_value=sys_name),
        patch("platform.machine", return_value=arch),
        patch("llama_mcp_bridge.hardware._probe_nvidia", return_value=None),
        patch("llama_mcp_bridge.hardware._probe_amd", return_value=None),
        patch("llama_mcp_bridge.hardware._probe_apple", return_value=None),
        patch("llama_mcp_bridge.hardware._probe_intel_or_vulkan", return_value=None),
    ):
        if probe_target:
            with patch(probe_target, return_value=probe_return):
                profile = detect_hardware()
        else:
            profile = detect_hardware()

    assert profile.gpu_vendor == expected_vendor
    assert profile.tier == expected_tier


@pytest.mark.parametrize(
    "tier,residency,expected_idle,expected_max",
    [
        ("8gb", "stingy", "15", "1"),
        ("8gb", "moderate", "120", "1"),
        ("16gb", "moderate", "120", "2"),
        ("12gb", "snappy", "600", "2"),
        ("24gb+", "snappy", "600", "3"),
    ],
)
def test_generate_models_ini_residency_profiles(
    tier: str, residency: str, expected_idle: str, expected_max: str
):
    profile = SystemProfile(
        os_name="linux",
        arch="x86_64",
        gpu_vendor="nvidia",
        gpu_name="GPU",
        vram_gb=16.0,
        compute_cap="sm_86",
        distro_family="ubuntu",
        tier=tier,
    )
    ini_str = generate_models_ini(profile, models_dir="/models", residency=residency)
    parser = configparser.ConfigParser()
    parser.read_string(ini_str)
    assert parser.get("*", "sleep-idle-seconds") == expected_idle
    assert parser.get("*", "models-max") == expected_max


def test_generate_models_ini_cpu_forces_pure_cpu_backend():
    cpu_profile = SystemProfile("linux", "x86_64", "cpu", "CPU", 0.0, "", "ubuntu", "6gb")
    cpu_parser = configparser.ConfigParser()
    cpu_parser.read_string(generate_models_ini(cpu_profile, models_dir="/models"))
    assert cpu_parser.get("*", "n-gpu-layers") == "0"
    assert cpu_parser.get("*", "device") == "none"

    gpu_profile = SystemProfile("linux", "x86_64", "nvidia", "RTX 3070", 8.0, "sm_86", "ubuntu", "8gb")
    gpu_parser = configparser.ConfigParser()
    gpu_parser.read_string(generate_models_ini(gpu_profile, models_dir="/models"))
    assert gpu_parser.get("*", "n-gpu-layers") == "-1"
    assert not gpu_parser.has_option("*", "device")


def test_generate_models_ini_selected_presets_cpu():
    cpu_profile = SystemProfile("linux", "x86_64", "cpu", "CPU", 0.0, "", "ubuntu", "6gb")
    parser = configparser.ConfigParser()
    parser.read_string(generate_models_ini(cpu_profile, models_dir="/models", selected_presets=["digest"]))
    assert parser.sections() == ["*", "digest"]
    assert parser.get("*", "n-gpu-layers") == "0"
    assert parser.get("*", "device") == "none"
