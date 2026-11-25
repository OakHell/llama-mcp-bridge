"""VRAM-tier presets and models.ini generation for llama-mcp-bridge."""

from typing import Any

from .hardware import SystemProfile


def generate_models_ini(profile: SystemProfile, models_dir: str = "/mnt/ai/models") -> str:
    """Generate a models.ini configuration string based on hardware profile.

    Scales context sizes, cache precision, and parallelism according to
    available VRAM tiers (6gb, 8gb, 12gb, 16gb, 24gb+).
    """
    base_config: dict[str, dict[str, Any]] = {
        "global": {
            "n-gpu-layers": "-1",
            "fit": "on",
            "fit-target": "512",
            "flash-attn": "on",
            "cache-type-k": "q8_0",
            "cache-type-v": "q8_0",
            "parallel": "1",
            "jinja": "true",
            "reasoning-format": "deepseek",
            "reasoning-budget-message": (
                '"\\n\\nConsidering the limited time, I have to give the final '
                'solution based on my thinking now.\\n"'
            ),
        },
        "coder-fast": {
            "model": f"{models_dir}/qwen2.5-coder-7b-instruct-q4_k_m.gguf",
            "ctx-size": "16384",
            "temp": "0.1",
            "reasoning-budget-message": '""',
            "reasoning-budget": "0",
        },
        "coder": {
            "model": f"{models_dir}/Qwen3.5-9B-UD-Q4_K_XL.gguf",
            "ctx-size": "16384",
            "temp": "0.1",
            "reasoning-budget": "2048",
        },
        "coder-long": {
            "model": f"{models_dir}/Qwen3.5-9B-UD-Q4_K_XL.gguf",
            "ctx-size": "32768",
            "cache-type-v": "q4_0",
            "temp": "0.1",
            "reasoning-budget": "2048",
        },
        "vision-deep": {
            "model": f"{models_dir}/Qwen3.5-9B-UD-Q4_K_XL.gguf",
            "mmproj": f"{models_dir}/mmproj-BF16.gguf",
            "no-mmproj-offload": "true",
            "image-min-tokens": "1024",
            "ctx-size": "8192",
            "temp": "0.2",
            "reasoning-budget": "1024",
        },
        "vision": {
            "model": f"{models_dir}/MiniCPM-V-4_6-Q4_K_M.gguf",
            "mmproj": f"{models_dir}/mmproj-model-f16.gguf",
            "ctx-size": "4096",
            "temp": "0.2",
            "reasoning-budget": "0",
        },
        "reasoner": {
            "model": f"{models_dir}/granite-4.2-8b-Q4_K_M.gguf",
            "ctx-size": "16384",
            "cache-type-v": "q4_0",
            "temp": "0.3",
            "reasoning-budget": "2048",
        },
        "analyst": {
            "model": f"{models_dir}/Ministral-3-8B-Reasoning-2512-UD-Q4_K_XL.gguf",
            "ctx-size": "8192",
            "temp": "0.7",
            "top-p": "0.95",
            "reasoning-budget": "2048",
        },
        "generalist": {
            "model": f"{models_dir}/gemma-4-E4B-it-UD-Q6_K_XL.gguf",
            "ctx-size": "8192",
            "temp": "0.7",
            "reasoning-budget": "1024",
        },
        "digest": {
            "model": f"{models_dir}/LFM3-1.7B-Q8_0.gguf",
            "ctx-size": "65536",
            "temp": "0.1",
            "reasoning-budget": "0",
        },
    }

    tier_map: dict[str, dict[str, Any]] = {
        "6gb": {
            "cache-k": "q4_0",
            "cache-v": "q4_0",
            "parallel": "1",
            "no_mmproj_offload": "true",
            "ctx_overrides": {
                "coder-fast": "8192",
                "coder": "8192",
                "coder-long": "16384",
                "vision-deep": "4096",
                "vision": "2048",
                "reasoner": "8192",
                "analyst": "4096",
                "generalist": "4096",
            },
        },
        "8gb": {
            "cache-k": "q8_0",
            "cache-v": "q8_0",
            "parallel": "1",
            "no_mmproj_offload": "true",
            "ctx_overrides": {},
        },
        "12gb": {
            "cache-k": "q8_0",
            "cache-v": "q8_0",
            "parallel": "1",
            "no_mmproj_offload": "false",
            "ctx_overrides": {
                "coder-fast": "24576",
                "coder": "24576",
                "coder-long": "40960",
                "vision-deep": "12288",
                "vision": "8192",
                "reasoner": "24576",
                "analyst": "12288",
                "generalist": "12288",
            },
        },
        "16gb": {
            "cache-k": "q8_0",
            "cache-v": "q8_0",
            "parallel": "2",
            "no_mmproj_offload": "false",
            "ctx_overrides": {
                "coder-fast": "32768",
                "coder": "32768",
                "coder-long": "65536",
                "vision-deep": "16384",
                "vision": "16384",
                "reasoner": "32768",
                "analyst": "16384",
                "generalist": "16384",
            },
        },
        "24gb+": {
            "cache-k": "q8_0",
            "cache-v": "q8_0",
            "parallel": "4",
            "no_mmproj_offload": "false",
            "ctx_overrides": {
                "coder-fast": "65536",
                "coder": "65536",
                "coder-long": "131072",
                "vision-deep": "32768",
                "vision": "32768",
                "reasoner": "65536",
                "analyst": "32768",
                "generalist": "32768",
            },
        },
    }

    tier = profile.tier if profile.tier in tier_map else "8gb"
    adj = tier_map[tier]

    base_config["global"]["cache-type-k"] = adj["cache-k"]
    base_config["global"]["cache-type-v"] = adj["cache-v"]
    base_config["global"]["parallel"] = adj["parallel"]
    base_config["vision-deep"]["no-mmproj-offload"] = adj["no_mmproj_offload"]

    for section, ctx_val in adj["ctx_overrides"].items():
        if section in base_config:
            base_config[section]["ctx-size"] = ctx_val

    lines: list[str] = ["[*]"]
    for key, value in base_config["global"].items():
        lines.append(f"{key} = {value}")
    lines.append("")

    section_order = [
        "coder-fast",
        "coder",
        "coder-long",
        "vision-deep",
        "vision",
        "reasoner",
        "analyst",
        "generalist",
        "digest",
    ]
    for section_name in section_order:
        lines.append(f"[{section_name}]")
        for key, value in base_config[section_name].items():
            lines.append(f"{key} = {value}")
        lines.append("")

    return "\n".join(lines)


_BASE_MODEL_SPECS: list[dict[str, Any]] = [
    {
        "role": "coder-fast",
        "filename": "qwen2.5-coder-7b-instruct-q4_k_m.gguf",
        "repo": "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
        "size_gb": 4.7,
    },
    {
        "role": "coder",
        "filename": "Qwen3.5-9B-UD-Q4_K_XL.gguf",
        "repo": "unsloth/Qwen3.5-9B-GGUF",
        "size_gb": 5.5,
    },
    {
        "role": "vision-deep-mmproj",
        "filename": "mmproj-BF16.gguf",
        "repo": "unsloth/Qwen3.5-9B-GGUF",
        "size_gb": 0.9,
    },
    {
        "role": "vision",
        "filename": "MiniCPM-V-4_6-Q4_K_M.gguf",
        "repo": "openbmb/MiniCPM-V-4_6-gguf",
        "size_gb": 4.8,
    },
    {
        "role": "vision-mmproj",
        "filename": "mmproj-model-f16.gguf",
        "repo": "openbmb/MiniCPM-V-4_6-gguf",
        "size_gb": 0.8,
    },
    {
        "role": "reasoner",
        "filename": "granite-4.2-8b-Q4_K_M.gguf",
        "repo": "ibm-granite/granite-4.2-8b-GGUF",
        "size_gb": 4.9,
    },
    {
        "role": "analyst",
        "filename": "Ministral-3-8B-Reasoning-2512-UD-Q4_K_XL.gguf",
        "repo": "unsloth/Ministral-3-8B-Reasoning-2512-GGUF",
        "size_gb": 5.1,
    },
    {
        "role": "generalist",
        "filename": "gemma-4-E4B-it-UD-Q6_K_XL.gguf",
        "repo": "unsloth/gemma-4-E4B-it-GGUF",
        "size_gb": 4.2,
    },
    {
        "role": "digest",
        "filename": "LFM3-1.7B-Q8_0.gguf",
        "repo": "LiquidAI/LFM3-1.7B-GGUF",
        "size_gb": 1.8,
    },
]

TIER_MODEL_REGISTRY: dict[str, list[dict[str, Any]]] = {
    tier: [
        {
            **spec,
            "url": f"https://huggingface.co/{spec['repo']}/resolve/main/{spec['filename']}",
        }
        for spec in _BASE_MODEL_SPECS
    ]
    for tier in ("6gb", "8gb", "12gb", "16gb", "24gb+")
}


def get_tier_model_specs(tier: str) -> list[dict[str, Any]]:
    """Return curated GGUF model specs for the given VRAM tier."""
    return list(TIER_MODEL_REGISTRY.get(tier, TIER_MODEL_REGISTRY["8gb"]))
