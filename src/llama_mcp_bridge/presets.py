"""VRAM-tier presets and models.ini generation for llama-mcp-bridge."""

import concurrent.futures
import configparser
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .hardware import SystemProfile
from .http_resilience import DEFAULT_HTTP_CLIENT

__all__ = [
    "RESIDENCY_PROFILES",
    "TIER_MODEL_REGISTRY",
    "_VALID_PRESETS",
    "generate_models_ini",
    "get_tier_model_specs",
    "merge_custom_preset_ini",
    "merge_models_ini",
    "probe_model_metadata",
    "prune_unreferenced_models",
    "remove_models_from_ini",
    "resolve_hf_gguf_repo",
    "resolve_residency_settings",
    "search_hf_gguf_models",
    "verify_model_url",
    "verify_tier_models",
]

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


def _build_base_preset_config(models_dir: str) -> dict[str, dict[str, Any]]:
    """Build the default 8gb baseline [global] and specialist preset dictionary."""
    return {
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
        "digest": {
            "model": f"{models_dir}/LFM3-1.7B-Q8_0.gguf",
            "ctx-size": "65536",
            "temp": "0.1",
            "reasoning-budget": "0",
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
        "vision": {
            "model": f"{models_dir}/MiniCPM-V-4_6-Q4_K_M.gguf",
            "mmproj": f"{models_dir}/mmproj-model-f16.gguf",
            "ctx-size": "4096",
            "temp": "0.2",
            "reasoning-budget": "0",
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
    }


def generate_models_ini(
    profile: SystemProfile,
    models_dir: str = "/mnt/ai/models",
    residency: str = "moderate",
    selected_presets: Sequence[str] | None = None,
) -> str:
    """Generate a models.ini configuration string based on hardware profile.

    Scales context sizes, cache precision, and parallelism according to
    available VRAM tiers (6gb, 8gb, 12gb, 16gb, 24gb+).
    """
    base_config = _build_base_preset_config(models_dir)

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
                "coder": "qwen2.5-coder-3b-instruct-q4_k_m.gguf",
                "coder-long": "qwen2.5-coder-3b-instruct-q4_k_m.gguf",
                "vision-deep": "Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf",
                "vision": "MiniCPM-V-4_6-Q4_K_M.gguf",
                "reasoner": "granite-4.2-3b-Q4_K_M.gguf",
                "analyst": "Ministral-3-3B-Reasoning-2512.Q4_K_M.gguf",
                "generalist": "gemma-4-E2B-it-Q4_K_M.gguf",
                "digest": "LFM3-1.7B-Q8_0.gguf",
            },
            "mmproj_overrides": {
                "vision-deep": "mmproj-F16.gguf",
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
    if selected_presets is not None:
        selected_set = set(selected_presets)
        section_order = [s for s in section_order if s in selected_set]
    for section_name in section_order:
        lines.append(f"[{section_name}]")
        for key, value in base_config[section_name].items():
            lines.append(f"{key} = {value}")
        lines.append("")

    return "\n".join(lines)


def _with_urls(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **s,
            "url": f"https://huggingface.co/{s['repo']}/resolve/main/{urllib.parse.quote(s['filename'], safe='/')}",
        }
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
    {"role": "coder", "filename": "qwen2.5-coder-3b-instruct-q4_k_m.gguf", "repo": "Qwen/Qwen2.5-Coder-3B-Instruct-GGUF", "size_gb": 2.0},
    {"role": "vision-deep", "filename": "Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf", "repo": "unsloth/Qwen2.5-VL-3B-Instruct-GGUF", "size_gb": 1.9},
    {"role": "vision-deep-mmproj", "filename": "mmproj-F16.gguf", "repo": "unsloth/Qwen2.5-VL-3B-Instruct-GGUF", "size_gb": 1.3},
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


_VALID_PRESETS: tuple[str, ...] = tuple(
    key for key in _build_base_preset_config("") if key != "global"
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

    has_dedicated_vision_deep = any(s["role"] == "vision-deep" for s in specs)
    wanted_roles: set[str] = set()
    for p in presets:
        if tier == "24gb+" and p in ("coder", "coder-long"):
            wanted_roles.add("coder-fast")
            continue
        wanted_roles.add(p)
        if p == "vision":
            wanted_roles.add("vision-mmproj")
        elif p == "vision-deep":
            wanted_roles.add("vision-deep-mmproj")
            if not has_dedicated_vision_deep:
                wanted_roles.add("coder")
        elif p == "coder-long":
            wanted_roles.add("coder")

    return [s for s in specs if s["role"] in wanted_roles]


_HF_HTTP_TIMEOUT: float = 10.0


def _user_agent() -> str:
    """Return a dynamic User-Agent header using the installed package version."""
    try:
        import importlib.metadata

        ver = importlib.metadata.version("llama-mcp-bridge")
    except importlib.metadata.PackageNotFoundError:
        ver = "dev"
    return f"llama-mcp-bridge/{ver}"


def probe_model_metadata(
    url: str, timeout: float = _HF_HTTP_TIMEOUT
) -> tuple[bool, int | None, str | None]:
    """Check whether a Hugging Face URL exists and return (ok, size_bytes, expected_sha256)."""
    req = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": _user_agent()},
    )
    try:
        with DEFAULT_HTTP_CLIENT.urlopen(req, timeout=timeout) as resp:
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
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return False, None, None
    return False, None, None


def verify_model_url(url: str, timeout: float = _HF_HTTP_TIMEOUT) -> tuple[bool, int | None]:
    """Check whether a Hugging Face model URL exists via HTTP HEAD."""
    ok, size_bytes, _ = probe_model_metadata(url, timeout=timeout)
    return ok, size_bytes


def search_hf_gguf_models(
    query: str, limit: int = 5, timeout: float = _HF_HTTP_TIMEOUT
) -> list[dict[str, Any]]:
    """Query Hugging Face API for live GGUF model alternatives."""
    encoded = urllib.parse.quote(query)
    api_url = (
        f"https://huggingface.co/api/models?search={encoded}"
        f"&filter=gguf&sort=downloads&direction=-1&limit={limit}"
    )
    try:
        with DEFAULT_HTTP_CLIENT.urlopen(api_url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list):
                return [{"id": item.get("id", ""), "downloads": item.get("downloads", 0)} for item in data]
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError):
        return []
    return []


def verify_tier_models(
    tier: str | None = None,
    max_workers: int = 8,
    cache_path: Path | None = None,
    use_cache: bool = True,
    ttl_seconds: int = 3600,
) -> list[dict[str, Any]]:
    """Verify model URLs concurrently for one tier (or all tiers) with optional 1-hour TTL cache."""
    cache_key = tier or "__all__"
    if use_cache and cache_path is not None and cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            entry = cached.get(cache_key) if isinstance(cached, dict) else None
            if (
                isinstance(entry, dict)
                and (time.time() - float(entry.get("ts", 0))) < ttl_seconds
                and isinstance(entry.get("results"), list)
            ):
                return entry["results"]
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            pass

    tiers = [tier] if tier else list(TIER_MODEL_REGISTRY.keys())
    entries = [(t, spec) for t in tiers for spec in get_tier_model_specs(t)]
    unique_urls = {spec["url"] for _, spec in entries}

    def _check(url: str) -> tuple[str, bool, int | None]:
        ok, size_bytes = verify_model_url(url)
        return url, ok, size_bytes

    url_cache: dict[str, tuple[bool, int | None]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        for url, ok, size_bytes in pool.map(_check, unique_urls):
            url_cache[url] = (ok, size_bytes)

    results: list[dict[str, Any]] = []
    for t, spec in entries:
        ok, size_bytes = url_cache[spec["url"]]
        suggestions: list[dict[str, Any]] = []
        if not ok:
            stem = spec["filename"].replace(".gguf", "").split("-")[0]
            suggestions = search_hf_gguf_models(stem, limit=3)
        size_drift_pct: float | None = None
        expected_gb = float(spec.get("size_gb") or 0)
        if ok and size_bytes is not None and size_bytes > 0 and expected_gb > 0:
            actual_gb = size_bytes / (1024**3)
            drift = abs(actual_gb - expected_gb) / expected_gb
            if drift > 0.20:
                size_drift_pct = round(drift * 100.0, 1)
        results.append(
            {
                "tier": t,
                "role": spec["role"],
                "filename": spec["filename"],
                "url": spec["url"],
                "ok": ok,
                "size_bytes": size_bytes,
                "size_drift_pct": size_drift_pct,
                "suggestions": suggestions,
            }
        )

    if use_cache and cache_path is not None:
        try:
            existing_cache: dict[str, Any] = {}
            if cache_path.is_file():
                loaded = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    existing_cache = loaded
            existing_cache[cache_key] = {"ts": time.time(), "results": results}
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            import tempfile

            with tempfile.NamedTemporaryFile(
                "w", dir=cache_path.parent, encoding="utf-8", delete=False, prefix=".cache-", suffix=".tmp"
            ) as tf:
                tf.write(json.dumps(existing_cache, indent=2))
                tmp_path = Path(tf.name)
            tmp_path.replace(cache_path)
        except OSError:
            pass

    return results


def merge_models_ini(
    existing_path: Path,
    profile: SystemProfile,
    residency: str,
    models_dir: Path,
    presets_to_add: Sequence[str],
) -> str:
    """Merge selected presets into models.ini without duplicating [section] headers."""
    unknown = [p for p in presets_to_add if p not in _VALID_PRESETS]
    if unknown:
        raise ValueError(f"Unknown preset(s): {', '.join(unknown)}. Valid presets: {', '.join(_VALID_PRESETS)}")

    fresh = configparser.ConfigParser()
    fresh.read_string(
        generate_models_ini(
            profile,
            models_dir=str(models_dir),
            residency=residency,
            selected_presets=list(_VALID_PRESETS),
        )
    )

    merged = configparser.ConfigParser()
    if existing_path.is_file():
        merged.read(existing_path, encoding="utf-8")
    if "*" not in merged and "*" in fresh:
        merged["*"] = dict(fresh["*"])

    for preset in presets_to_add:
        if preset in fresh:
            merged[preset] = dict(fresh[preset])

    lines: list[str] = []
    for sec in merged.sections():
        lines.append(f"[{sec}]")
        for key, val in merged.items(sec):
            lines.append(f"{key} = {val}")
        lines.append("")
    content = "\n".join(lines)
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_path.write_text(content, encoding="utf-8")
    return content


def remove_models_from_ini(
    existing_path: Path,
    presets_to_remove: Sequence[str],
    models_dir: Path,
    delete_files: bool = True,
) -> list[str]:
    """Remove presets from models.ini and delete unreferenced GGUF/mmproj files.

    Uses ``Path(...).name`` to strip any directory prefix so file deletion is
    strictly restricted to basenames inside ``models_dir``.
    """
    if not existing_path.is_file():
        return []

    cp = configparser.ConfigParser()
    cp.read(existing_path, encoding="utf-8")

    candidate_files: set[str] = set()
    for preset in presets_to_remove:
        if cp.has_section(preset):
            for key in ("model", "mmproj"):
                if cp.has_option(preset, key):
                    # .name strips any directory components so deletion stays strictly inside models_dir.
                    candidate_files.add(Path(cp.get(preset, key)).name)
            cp.remove_section(preset)

    still_referenced: set[str] = set()
    for sec in cp.sections():
        for key in ("model", "mmproj"):
            if cp.has_option(sec, key):
                still_referenced.add(Path(cp.get(sec, key)).name)

    lines: list[str] = []
    for sec in cp.sections():
        lines.append(f"[{sec}]")
        for key, val in cp.items(sec):
            lines.append(f"{key} = {val}")
        lines.append("")
    existing_path.write_text("\n".join(lines), encoding="utf-8")

    deleted: list[str] = []
    if delete_files:
        for fname in sorted(candidate_files - still_referenced):
            target = models_dir / fname
            if target.is_file():
                target.unlink()
                deleted.append(fname)
    return deleted


def prune_unreferenced_models(
    existing_path: Path,
    models_dir: Path,
    dry_run: bool = False,
    include_partials: bool = True,
) -> list[str]:
    """Identify and remove unreferenced .gguf (case-insensitive) and optional .part/.gguf.tmp files."""
    if not models_dir.is_dir():
        return []

    referenced: set[str] = set()
    if existing_path.is_file():
        cp = configparser.ConfigParser()
        cp.read(existing_path, encoding="utf-8")
        for sec in cp.sections():
            for key in ("model", "mmproj"):
                if cp.has_option(sec, key):
                    referenced.add(Path(cp.get(sec, key)).name.lower())

    pruned: list[str] = []
    for candidate in sorted(models_dir.iterdir()):
        if not candidate.is_file():
            continue
        name_lower = candidate.name.lower()
        is_gguf = name_lower.endswith(".gguf")
        is_partial = include_partials and name_lower.endswith((".part", ".gguf.tmp"))
        if (is_gguf and name_lower not in referenced) or is_partial:
            if not dry_run:
                candidate.unlink(missing_ok=True)
            pruned.append(candidate.name)
    return pruned


def resolve_hf_gguf_repo(
    repo_spec: str,
    mmproj_hint: str | None = None,
    timeout: float = 15.0,
    tier: str | None = None,
) -> tuple[str, str, str | None]:
    """Resolve an unconstrained Hugging Face GGUF repo[:selector] to (repo_id, model_file, mmproj_file)."""
    repo_id, _, selector = repo_spec.strip().partition(":")
    if "/" not in repo_id:
        raise ValueError(f"Invalid Hugging Face repo '{repo_spec}'; expected '<owner>/<repo>[:<quant-or-filename>]'.")

    api_url = f"https://huggingface.co/api/models/{repo_id}"
    try:
        with DEFAULT_HTTP_CLIENT.urlopen(api_url, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Failed to query Hugging Face repository '{repo_id}': {exc}") from exc

    siblings = [item.get("rfilename", "") for item in payload.get("siblings", []) if isinstance(item, dict)]
    gguf_files = [f for f in siblings if f.lower().endswith(".gguf")]
    if not gguf_files:
        raise ValueError(f"No .gguf files found in Hugging Face repository '{repo_id}'.")

    mmproj_files = [f for f in gguf_files if "mmproj" in Path(f).name.lower()]
    model_files = [f for f in gguf_files if "mmproj" not in Path(f).name.lower()]
    if not model_files:
        raise ValueError(f"No primary model .gguf files found in '{repo_id}' (only mmproj files).")

    chosen_model: str | None = None
    sel = selector.strip().lower()
    if sel:
        exact = [f for f in model_files if f.lower() == sel or Path(f).name.lower() == sel]
        matches = exact or [f for f in model_files if sel in f.lower()]
        if not matches:
            raise ValueError(
                f"No GGUF matching '{selector}' in {repo_id}. Available: {', '.join(model_files)}"
            )
        if len(matches) > 1:
            raise ValueError(
                f"Multiple GGUFs match '{selector}' in {repo_id}: {', '.join(matches)}"
            )
        chosen_model = matches[0]
    elif len(model_files) == 1:
        chosen_model = model_files[0]
    else:
        tier_quant_prefs: dict[str, tuple[str, ...]] = {
            "6gb": ("q4_k_s", "q4_k_m", "iq4_xs"),
            "8gb": ("q4_k_m", "q4_k_s", "q5_k_m"),
            "12gb": ("q4_k_m", "q5_k_m", "q6_k"),
            "16gb": ("q6_k", "q5_k_m", "q4_k_m"),
            "24gb+": ("q6_k", "q8_0", "q5_k_m", "q4_k_m"),
        }
        prefs = tier_quant_prefs.get(tier, ("q4_k_m",)) if tier else ("q4_k_m",)
        for quant_tok in prefs:
            q_matches = [f for f in model_files if quant_tok in f.lower()]
            if len(q_matches) == 1:
                chosen_model = q_matches[0]
                break
        if chosen_model is None:
            raise ValueError(
                f"Multiple GGUFs found in {repo_id}; specify <repo>:<quant-or-filename>. "
                f"Available: {', '.join(model_files)}"
            )

    chosen_mmproj: str | None = None
    if mmproj_hint:
        hint_l = mmproj_hint.strip().lower()
        exact_m = [f for f in mmproj_files if f.lower() == hint_l or Path(f).name.lower() == hint_l]
        m_matches = exact_m or [f for f in mmproj_files if hint_l in f.lower()]
        if not m_matches:
            raise ValueError(
                f"No mmproj matching '{mmproj_hint}' in {repo_id}. Available: {', '.join(mmproj_files) or 'none'}"
            )
        chosen_mmproj = m_matches[0]
    elif mmproj_files:
        for pref in ("f16", "bf16", "q8_0"):
            pref_hit = next((f for f in mmproj_files if pref in f.lower()), None)
            if pref_hit:
                chosen_mmproj = pref_hit
                break
        if chosen_mmproj is None:
            chosen_mmproj = mmproj_files[0]

    return repo_id, chosen_model, chosen_mmproj


def merge_custom_preset_ini(
    existing_path: Path,
    profile: SystemProfile,
    residency: str,
    models_dir: Path,
    preset_name: str,
    model_path: Path | str,
    mmproj_path: Path | str | None = None,
    ctx_size: int | None = None,
    n_gpu_layers: int | None = None,
) -> str:
    """Merge a custom or overridden [preset] entry with hardware-tier or tuned defaults into models.ini."""
    clean_name = preset_name.strip()
    if not clean_name or clean_name == "*" or clean_name.upper() == "DEFAULT":
        raise ValueError(f"Invalid preset name: {preset_name!r}")

    fresh = configparser.ConfigParser()
    fresh.read_string(
        generate_models_ini(
            profile,
            models_dir=str(models_dir),
            residency=residency,
            selected_presets=[],
        )
    )

    merged = configparser.ConfigParser()
    if existing_path.is_file():
        merged.read(existing_path, encoding="utf-8")
    if "*" not in merged and "*" in fresh:
        merged["*"] = dict(fresh["*"])

    tier_ctx_defaults = {
        "6gb": "4096",
        "8gb": "8192",
        "12gb": "12288",
        "16gb": "16384",
        "24gb+": "32768",
    }
    resolved_ctx = str(ctx_size) if ctx_size is not None else tier_ctx_defaults.get(profile.tier, "8192")
    default_ngl = "0" if profile.gpu_vendor == "cpu" else fresh["*"].get("n-gpu-layers", "-1")
    resolved_ngl = str(n_gpu_layers) if n_gpu_layers is not None else default_ngl
    entry: dict[str, str] = {
        "model": str(model_path),
        "ctx-size": resolved_ctx,
        "n-gpu-layers": resolved_ngl,
        "temp": "0.3",
    }
    if mmproj_path:
        entry["mmproj"] = str(mmproj_path)
    merged[clean_name] = entry

    lines: list[str] = []
    for sec in merged.sections():
        lines.append(f"[{sec}]")
        for key, val in merged.items(sec):
            lines.append(f"{key} = {val}")
        lines.append("")
    content = "\n".join(lines)
    existing_path.parent.mkdir(parents=True, exist_ok=True)
    existing_path.write_text(content, encoding="utf-8")
    return content
