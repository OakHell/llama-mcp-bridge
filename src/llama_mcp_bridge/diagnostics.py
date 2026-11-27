"""Diagnostics, benchmarking, update checking, and config introspection for llama-mcp-bridge."""

from __future__ import annotations

import configparser
import difflib
import importlib.metadata
import json
import re
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import requests

from llama_mcp_bridge.config import BridgeConfig
from llama_mcp_bridge.gguf_header import parse_gguf_header
from llama_mcp_bridge.hardware import SystemProfile, detect_hardware
from llama_mcp_bridge.presets import generate_models_ini
from llama_mcp_bridge.supervisor import LlamaSupervisor

__all__ = [
    "check_for_update",
    "diff_config_against_defaults",
    "export_merged_config",
    "get_model_info",
    "run_profile_benchmark",
    "run_selftest",
]

_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def _parse_semver(tag: str) -> tuple[int, int, int] | None:
    m = _SEMVER_RE.match(tag.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def check_for_update(
    repo_url: str = "https://github.com/OakHell/llama-mcp-bridge",
    timeout: float = 10.0,
) -> tuple[str, str | None, bool]:
    """Compare installed version against SemVer tags on the remote Git repository."""
    current = importlib.metadata.version("llama-mcp-bridge")
    cur_tuple = _parse_semver(current.split(".post")[0]) or (0, 0, 0)
    res = subprocess.run(
        ["git", "ls-remote", "--tags", repo_url],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(f"git ls-remote failed (exit {res.returncode}): {res.stderr.strip()}")

    best_tag: str | None = None
    best_tuple: tuple[int, int, int] = cur_tuple
    for line in res.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        ref = parts[1]
        if ref.endswith("^{}"):
            continue
        tag = ref.removeprefix("refs/tags/")
        parsed = _parse_semver(tag)
        if parsed and parsed > best_tuple:
            best_tuple = parsed
            best_tag = tag

    return current, best_tag, best_tag is not None


def export_merged_config(models_ini: Path) -> str:
    """Return effective merged [*] + [preset] INI configuration."""
    if not models_ini.is_file():
        raise FileNotFoundError(f"models.ini not found at {models_ini}")
    cp = configparser.ConfigParser()
    cp.read(models_ini, encoding="utf-8")
    defaults = dict(cp.items("*")) if cp.has_section("*") else {}

    lines: list[str] = []
    for sec in cp.sections():
        if sec == "*":
            continue
        merged = {**defaults, **dict(cp.items(sec))}
        lines.append(f"[{sec}]")
        for key, val in merged.items():
            lines.append(f"{key} = {val}")
        lines.append("")
    return "\n".join(lines)


def diff_config_against_defaults(
    models_ini: Path,
    profile: SystemProfile,
    models_dir: str,
    residency: str,
) -> str:
    """Return a unified diff comparing current models.ini against fresh tier defaults."""
    if not models_ini.is_file():
        raise FileNotFoundError(f"models.ini not found at {models_ini}")
    current_lines = models_ini.read_text(encoding="utf-8").splitlines(keepends=True)
    fresh_lines = generate_models_ini(
        profile, models_dir=models_dir, residency=residency
    ).splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            fresh_lines,
            current_lines,
            fromfile="defaults/models.ini",
            tofile=str(models_ini),
        )
    )


def get_model_info(models_ini: Path, preset: str) -> dict[str, Any]:
    """Return effective merged llama-server flags and parsed GGUF metadata for a preset."""
    if not models_ini.is_file():
        raise FileNotFoundError(f"models.ini not found at {models_ini}")
    cp = configparser.ConfigParser()
    cp.read(models_ini, encoding="utf-8")
    if not cp.has_section(preset):
        raise KeyError(f"Preset '{preset}' not found in {models_ini}")
    defaults = dict(cp.items("*")) if cp.has_section("*") else {}
    flags = {**defaults, **dict(cp.items(preset))}
    gguf_meta: dict[str, Any] | None = None
    model_path = flags.get("model")
    if model_path and Path(model_path).is_file():
        try:
            meta = parse_gguf_header(model_path)
            gguf_meta = asdict(meta)
        except (ValueError, OSError):
            gguf_meta = None
    return {"preset": preset, "flags": flags, "gguf": gguf_meta}


def run_selftest(json_output: bool = False) -> int:
    """Run an end-to-end ask_digest('ping') live smoke test against llama-server."""
    from llama_mcp_bridge.bridge import ask_digest

    t0 = time.perf_counter()
    reply = ask_digest("ping")
    elapsed = time.perf_counter() - t0
    passed = bool(reply and reply.strip() and not reply.startswith("Error:"))
    if json_output:
        print(
            json.dumps(
                {"passed": passed, "elapsed_s": round(elapsed, 3), "reply": reply[:200]},
                indent=2,
            )
        )
    else:
        status = "PASS" if passed else "FAIL"
        print(f"Selftest {status} ({elapsed:.2f}s): {reply[:160]}")
    return 0 if passed else 1


def run_profile_benchmark(cfg: BridgeConfig, json_output: bool = False) -> int:
    """Benchmark configured presets against a live llama-server and report tokens/s & latency."""
    models_ini = Path(cfg.models_ini)
    if not models_ini.is_file():
        raise FileNotFoundError(f"models.ini not found at {models_ini}")
    cp = configparser.ConfigParser()
    cp.read(models_ini, encoding="utf-8")
    presets = [s for s in cp.sections() if s != "*"]
    if not presets:
        print("No presets configured in models.ini.")
        return 1

    profile = detect_hardware()
    supervisor = LlamaSupervisor(cfg)
    if not supervisor.ensure_running():
        print("Error: llama-server is not running and failed to start.")
        return 1

    url = f"http://{cfg.host}:{cfg.port}/v1/chat/completions"
    results: list[dict[str, Any]] = []
    for preset in presets:
        payload = {
            "model": preset,
            "messages": [{"role": "user", "content": "Reply with exactly three words: local benchmark ready."}],
            "max_tokens": 32,
            "stream": False,
        }
        t0 = time.perf_counter()
        try:
            resp = requests.post(url, json=payload, timeout=cfg.request_timeout_s)
            elapsed = max(1e-6, time.perf_counter() - t0)
            if resp.status_code != 200:
                results.append(
                    {"preset": preset, "ok": False, "latency_s": round(elapsed, 3), "tokens_per_s": 0.0, "error": f"HTTP {resp.status_code}"}
                )
                continue
            data = resp.json()
            usage = data.get("usage") or {}
            comp_tokens = int(usage.get("completion_tokens") or 0)
            if comp_tokens <= 0:
                text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
                comp_tokens = max(1, len(text.split()))
            tps = comp_tokens / elapsed
            results.append(
                {
                    "preset": preset,
                    "ok": True,
                    "latency_s": round(elapsed, 3),
                    "completion_tokens": comp_tokens,
                    "tokens_per_s": round(tps, 2),
                }
            )
        except (requests.RequestException, ValueError) as exc:
            elapsed = max(1e-6, time.perf_counter() - t0)
            results.append(
                {"preset": preset, "ok": False, "latency_s": round(elapsed, 3), "tokens_per_s": 0.0, "error": str(exc)}
            )

    if json_output:
        print(
            json.dumps(
                {"tier": profile.tier, "vram_gb": profile.vram_gb, "results": results},
                indent=2,
            )
        )
    else:
        print(f"Benchmark (tier={profile.tier}, vram={profile.vram_gb:.1f} GB):")
        for r in results:
            if r["ok"]:
                print(f"  {r['preset']}: {r['tokens_per_s']} tok/s ({r['latency_s']}s)")
            else:
                print(f"  {r['preset']}: FAIL ({r.get('error', 'unknown')})")
    return 0 if any(r["ok"] for r in results) else 1
