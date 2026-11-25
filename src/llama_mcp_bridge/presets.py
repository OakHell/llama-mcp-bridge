"""VRAM-tier presets and models.ini generation for llama-mcp-bridge."""

import concurrent.futures
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from typing import Any

from .hardware import SystemProfile

RESIDENCY_PROFILES: dict[str, dict[str, Any]] = {
    "stingy": {"idle_timeout_s": 15, "max_models_cap": 1},
    "moderate": {"idle_timeout_s": 120, "max_models_cap": 1},
    "snappy": {"idle_timeout_s": 600, "max_models_cap": 2},
}


def resolve_residency_settings(tier: str, residency: str = "moderate") -> tuple[int, int]:
    """Return (sleep_idle_seconds, models_max) for a given VRAM tier and residency profile."""
    prof = residency.strip().lower()
    if prof not in RESIDENCY_PROFILES:
        raise ValueError(f"Invalid residency profile: {residency!r}")
    idle_s = int(RESIDENCY_PROFILES[prof]["idle_timeout_s"])
    if prof == "stingy":
        models_max = 1
    elif prof == "moderate":
        models_max = 2 if tier in ("16gb", "24gb+") else 1
    else:  # snappy
        models_max = 3 if tier == "24gb+" else (2 if tier in ("12gb", "16gb") else 1)
    return idle_s, models_max


def generate_models_ini(
    profile: SystemProfile,
    models_dir: str = "/mnt/ai/models",
    residency: str = "moderate",
) -> str:
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
            "model_overrides": {
                "coder-fast": "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf",
                "coder": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
                "coder-long": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
                "vision-deep": "MiniCPM-V-4_6-Q4_K_M.gguf",
                "vision": "MiniCPM-V-4_6-Q4_K_M.gguf",
                "reasoner": "granite-4.2-3b-Q4_K_M.gguf",
                "analyst": "Ministral-3-3B-Reasoning-2512.Q4_K_M.gguf",
                "generalist": "gemma-4-E2B-it-Q4_K_M.gguf",
                "digest": "LFM3-1.7B-Q8_0.gguf",
            },
            "mmproj_overrides": {
                "vision-deep": "mmproj-model-f16.gguf",
                "vision": "mmproj-model-f16.gguf",
            },
        },
        "8gb": {
            "cache-k": "q8_0",
            "cache-v": "q8_0",
            "parallel": "1",
            "no_mmproj_offload": "true",
            "ctx_overrides": {},
            "model_overrides": {},
            "mmproj_overrides": {},
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
            "model_overrides": {},
            "mmproj_overrides": {},
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
            "model_overrides": {},
            "mmproj_overrides": {},
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
            "model_overrides": {
                "coder-fast": "qwen2.5-coder-14b-instruct-q4_k_m.gguf",
                "coder": "qwen2.5-coder-14b-instruct-q4_k_m.gguf",
                "coder-long": "qwen2.5-coder-14b-instruct-q4_k_m.gguf",
            },
            "mmproj_overrides": {},
        },
    }

    tier = profile.tier if profile.tier in tier_map else "8gb"
    adj = tier_map[tier]

    idle_s, models_max = resolve_residency_settings(tier, residency)
    base_config["global"]["cache-type-k"] = adj["cache-k"]
    base_config["global"]["cache-type-v"] = adj["cache-v"]
    base_config["global"]["parallel"] = adj["parallel"]
    base_config["global"]["models-max"] = str(models_max)
    base_config["global"]["sleep-idle-seconds"] = str(idle_s)
    if profile.gpu_vendor == "cpu":
        base_config["global"]["n-gpu-layers"] = "0"
        base_config["global"]["device"] = "none"
    base_config["vision-deep"]["no-mmproj-offload"] = adj["no_mmproj_offload"]

    for section, ctx_val in adj["ctx_overrides"].items():
        if section in base_config:
            base_config[section]["ctx-size"] = ctx_val

    for section, model_name in adj["model_overrides"].items():
        if section in base_config:
            base_config[section]["model"] = f"{models_dir}/{model_name}"

    for section, mmproj_name in adj["mmproj_overrides"].items():
        if section in base_config:
            base_config[section]["mmproj"] = f"{models_dir}/{mmproj_name}"

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


def _with_urls(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {**s, "url": f"https://huggingface.co/{s['repo']}/resolve/main/{s['filename']}"}
        for s in specs
    ]


_STANDARD_SPECS: list[dict[str, Any]] = [
    {"role": "coder-fast", "filename": "qwen2.5-coder-7b-instruct-q4_k_m.gguf", "repo": "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF", "size_gb": 4.7},
    {"role": "coder", "filename": "Qwen3.5-9B-UD-Q4_K_XL.gguf", "repo": "unsloth/Qwen3.5-9B-GGUF", "size_gb": 5.5},
    {"role": "vision-deep-mmproj", "filename": "mmproj-BF16.gguf", "repo": "unsloth/Qwen3.5-9B-GGUF", "size_gb": 0.9},
    {"role": "vision", "filename": "MiniCPM-V-4_6-Q4_K_M.gguf", "repo": "openbmb/MiniCPM-V-4_6-gguf", "size_gb": 0.5},
    {"role": "vision-mmproj", "filename": "mmproj-model-f16.gguf", "repo": "openbmb/MiniCPM-V-4_6-gguf", "size_gb": 1.1},
    {"role": "reasoner", "filename": "granite-4.2-8b-Q4_K_M.gguf", "repo": "ibm-granite/granite-4.2-8b-GGUF", "size_gb": 4.9},
    {"role": "analyst", "filename": "Ministral-3-8B-Reasoning-2512-UD-Q4_K_XL.gguf", "repo": "unsloth/Ministral-3-8B-Reasoning-2512-GGUF", "size_gb": 5.1},
    {"role": "generalist", "filename": "gemma-4-E4B-it-UD-Q6_K_XL.gguf", "repo": "unsloth/gemma-4-E4B-it-GGUF", "size_gb": 4.2},
    {"role": "digest", "filename": "LFM3-1.7B-Q8_0.gguf", "repo": "LiquidAI/LFM3-1.7B-GGUF", "size_gb": 1.8},
]

_COMPACT_6GB_SPECS: list[dict[str, Any]] = [
    {"role": "coder-fast", "filename": "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf", "repo": "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF", "size_gb": 1.1},
    {"role": "coder", "filename": "qwen2.5-1.5b-instruct-q4_k_m.gguf", "repo": "Qwen/Qwen2.5-1.5B-Instruct-GGUF", "size_gb": 1.1},
    {"role": "vision", "filename": "MiniCPM-V-4_6-Q4_K_M.gguf", "repo": "openbmb/MiniCPM-V-4_6-gguf", "size_gb": 0.5},
    {"role": "vision-mmproj", "filename": "mmproj-model-f16.gguf", "repo": "openbmb/MiniCPM-V-4_6-gguf", "size_gb": 1.1},
    {"role": "reasoner", "filename": "granite-4.2-3b-Q4_K_M.gguf", "repo": "ibm-granite/granite-4.2-3b-GGUF", "size_gb": 2.0},
    {"role": "analyst", "filename": "Ministral-3-3B-Reasoning-2512.Q4_K_M.gguf", "repo": "MaziyarPanahi/Ministral-3-3B-Reasoning-2512-GGUF", "size_gb": 2.1},
    {"role": "generalist", "filename": "gemma-4-E2B-it-Q4_K_M.gguf", "repo": "unsloth/gemma-4-E2B-it-GGUF", "size_gb": 2.5},
    {"role": "digest", "filename": "LFM3-1.7B-Q8_0.gguf", "repo": "LiquidAI/LFM3-1.7B-GGUF", "size_gb": 1.8},
]

_LARGE_24GB_SPECS: list[dict[str, Any]] = [
    {"role": "coder-fast", "filename": "qwen2.5-coder-14b-instruct-q4_k_m.gguf", "repo": "Qwen/Qwen2.5-Coder-14B-Instruct-GGUF", "size_gb": 9.0},
    *[s for s in _STANDARD_SPECS if s["role"] != "coder-fast"],
]

TIER_MODEL_REGISTRY: dict[str, list[dict[str, Any]]] = {
    "6gb": _with_urls(_COMPACT_6GB_SPECS),
    "8gb": _with_urls(_STANDARD_SPECS),
    "12gb": _with_urls(_STANDARD_SPECS),
    "16gb": _with_urls(_STANDARD_SPECS),
    "24gb+": _with_urls(_LARGE_24GB_SPECS),
}


_VALID_PRESETS = (
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


def get_tier_model_specs(
    tier: str, presets: Sequence[str] | None = None
) -> list[dict[str, Any]]:
    """Return curated GGUF model specs for the given VRAM tier, optionally filtered by presets."""
    specs = list(TIER_MODEL_REGISTRY.get(tier, TIER_MODEL_REGISTRY["8gb"]))
    if not presets:
        return specs

    unknown = [p for p in presets if p not in _VALID_PRESETS]
    if unknown:
        raise ValueError(
            f"Unknown preset(s): {', '.join(unknown)}. Valid presets: {', '.join(_VALID_PRESETS)}"
        )

    wanted_roles: set[str] = set()
    for p in presets:
        wanted_roles.add(p)
        if p == "vision":
            wanted_roles.add("vision-mmproj")
        elif p == "vision-deep":
            wanted_roles.update({"vision-deep-mmproj", "coder", "vision", "vision-mmproj"})
        elif p == "coder-long":
            wanted_roles.add("coder")

    return [s for s in specs if s["role"] in wanted_roles]


def probe_model_metadata(url: str, timeout: float = 5.0) -> tuple[bool, int | None, str | None]:
    """Check whether a Hugging Face URL exists and return (ok, size_bytes, expected_sha256)."""
    req = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": "llama-mcp-bridge/0.1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            if status in (200, 302):
                raw_size = resp.headers.get("X-Linked-Size") or resp.headers.get("Content-Length")
                size_bytes = int(raw_size) if raw_size and raw_size.isdigit() else None
                raw_etag = (resp.headers.get("X-Linked-ETag") or "").strip().strip('"').lower()
                sha256 = (
                    raw_etag
                    if len(raw_etag) == 64 and all(c in "0123456789abcdef" for c in raw_etag)
                    else None
                )
                return True, size_bytes, sha256
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError):
        return False, None, None
    return False, None, None


def verify_model_url(url: str, timeout: float = 10.0) -> tuple[bool, int | None]:
    """Check whether a Hugging Face model URL exists via HTTP HEAD."""
    ok, size_bytes, _ = probe_model_metadata(url, timeout=timeout)
    return ok, size_bytes


def search_hf_gguf_models(query: str, limit: int = 5, timeout: float = 10.0) -> list[dict[str, Any]]:
    """Query Hugging Face API for live GGUF model alternatives."""
    encoded = urllib.parse.quote(query)
    api_url = (
        f"https://huggingface.co/api/models?search={encoded}"
        f"&filter=gguf&sort=downloads&direction=-1&limit={limit}"
    )
    try:
        with urllib.request.urlopen(api_url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list):
                return [{"id": item.get("id", ""), "downloads": item.get("downloads", 0)} for item in data]
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError):
        return []
    return []


def verify_tier_models(tier: str | None = None) -> list[dict[str, Any]]:
    """Verify model URLs concurrently for one tier (or all tiers) and return status + suggestions."""
    tiers = [tier] if tier else list(TIER_MODEL_REGISTRY.keys())
    entries = [(t, spec) for t in tiers for spec in get_tier_model_specs(t)]
    unique_urls = {spec["url"] for _, spec in entries}

    def _check(url: str) -> tuple[str, bool, int | None]:
        ok, size_bytes = verify_model_url(url)
        return url, ok, size_bytes

    url_cache: dict[str, tuple[bool, int | None]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for url, ok, size_bytes in pool.map(_check, unique_urls):
            url_cache[url] = (ok, size_bytes)

    results: list[dict[str, Any]] = []
    for t, spec in entries:
        ok, size_bytes = url_cache[spec["url"]]
        suggestions: list[dict[str, Any]] = []
        if not ok:
            stem = spec["filename"].replace(".gguf", "").split("-")[0]
            suggestions = search_hf_gguf_models(stem, limit=3)
        results.append(
            {
                "tier": t,
                "role": spec["role"],
                "filename": spec["filename"],
                "url": spec["url"],
                "ok": ok,
                "size_bytes": size_bytes,
                "suggestions": suggestions,
            }
        )
    return results
