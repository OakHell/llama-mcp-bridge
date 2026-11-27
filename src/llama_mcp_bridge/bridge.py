# /// script
# dependencies = [
#     "fastmcp",
#     "pathspec",
#     "requests",
# ]
# ///

import argparse
import asyncio
import base64
import configparser
import importlib.metadata
import json
import logging
import mimetypes
import os
import re
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import requests
from fastmcp import Context, FastMCP

from llama_mcp_bridge.config import BridgeConfig, load_config
from llama_mcp_bridge.discovery import (
    DEFAULT_IGNORES,
    _build_prompt_with_files,
    _discover_ancestor_gitignores,
    _expand_target_files,
    _is_gitignored,
    _load_gitignore_spec,
    _matches_any_pattern,
    _standalone_rel_path,
    _walk_dir_pruned,
)
from llama_mcp_bridge.hardware import SystemProfile, detect_hardware
from llama_mcp_bridge.hygiene import clean_mcp_output
from llama_mcp_bridge.logging_utils import configure_logging
from llama_mcp_bridge.metrics import METRICS, start_metrics_server
from llama_mcp_bridge.paths import resolve_workspace
from llama_mcp_bridge.supervisor import LlamaSupervisor, classify_inference_error

mcp = FastMCP("Local Model Specialist")
_LOGGER = logging.getLogger(__name__)
_TOOL_REG_LOCK = threading.RLock()
_CACHED_HARDWARE: SystemProfile | None = None
_MODELS_INI_STATE: tuple[str, int | None, bytes | None] | None = None
_PROGRESS_THRESHOLD_S = 30.0
_PROGRESS_INTERVAL_S = 10.0


def _get_cached_hardware() -> SystemProfile:
    """Return a process-cached SystemProfile to avoid re-probing hardware on every error."""
    global _CACHED_HARDWARE
    if _CACHED_HARDWARE is None:
        _CACHED_HARDWARE = detect_hardware()
    return _CACHED_HARDWARE


def _is_preset_allowed(preset: str, config: BridgeConfig) -> bool:
    """Return True if preset is permitted by BridgeConfig.allowed_presets."""
    return not config.allowed_presets or preset in config.allowed_presets


def _snapshot_models_ini(models_ini: Path) -> tuple[str, int | None, bytes | None]:
    try:
        if models_ini.is_file():
            return str(models_ini), models_ini.stat().st_mtime_ns, models_ini.read_bytes()
    except OSError:
        pass
    return str(models_ini), None, None


_MODELS_INI_STAT_CACHE: tuple[str, int, int, tuple[str, int | None, bytes | None]] | None = None


def check_and_reload_models_ini(ctx: Any = None, target_mcp: FastMCP | None = None) -> bool:
    """Hot-reload models.ini when changed on disk and emit notifications/tools/list_changed."""
    global _MODELS_INI_STATE, _MODELS_INI_STAT_CACHE
    cfg = load_config()
    if not cfg.models_ini:
        return False
    ini_path = Path(cfg.models_ini)
    with _TOOL_REG_LOCK:
        try:
            st = ini_path.stat()
            if (
                _MODELS_INI_STAT_CACHE is not None
                and _MODELS_INI_STAT_CACHE[0] == str(ini_path)
                and _MODELS_INI_STAT_CACHE[1] == st.st_mtime_ns
                and _MODELS_INI_STAT_CACHE[2] == st.st_size
            ):
                snap = _MODELS_INI_STAT_CACHE[3]
            else:
                snap = _snapshot_models_ini(ini_path)
                _MODELS_INI_STAT_CACHE = (str(ini_path), st.st_mtime_ns, st.st_size, snap)
        except OSError:
            snap = _snapshot_models_ini(ini_path)
        if _MODELS_INI_STATE is None:
            _MODELS_INI_STATE = snap
            return False
        if snap == _MODELS_INI_STATE:
            return False
        _MODELS_INI_STATE = snap
    _register_configured_tools(target_mcp)
    session = getattr(ctx, "session", None)
    sender = getattr(session, "send_tool_list_changed", None) or getattr(ctx, "send_tool_list_changed", None)
    if callable(sender):
        try:
            res = sender()
            if asyncio.iscoroutine(res):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(res)
                except RuntimeError:
                    asyncio.run(res)
        except (RuntimeError, OSError, ValueError) as exc:
            _LOGGER.debug("Failed to emit tool list changed notification: %s", exc)
    return True


def _parse_sse_stream(response: requests.Response) -> str:
    """Accumulate tokens from an OpenAI-compatible Server-Sent Events stream."""
    chunks: list[str] = []
    for raw_line in response.iter_lines(decode_unicode=True):
        if not raw_line:
            continue
        line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8", errors="replace")
        if not line.startswith("data:"):
            continue
        payload_str = line[5:].strip()
        if payload_str == "[DONE]":
            break
        try:
            obj = json.loads(payload_str)
        except json.JSONDecodeError:
            continue
        choices = obj.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or choices[0].get("message") or {}
        token = delta.get("content")
        if token:
            chunks.append(str(token))
    return "".join(chunks)


def _start_progress_heartbeat(ctx: Any, stop_event: threading.Event, threshold_s: float = _PROGRESS_THRESHOLD_S) -> threading.Thread | None:
    """Emit periodic MCP notifications/progress when inference exceeds threshold_s."""
    report_fn = getattr(ctx, "report_progress", None)
    if not callable(report_fn):
        return None
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    def _worker() -> None:
        if stop_event.wait(threshold_s):
            return
        step = 1
        while not stop_event.is_set():
            try:
                res = report_fn(progress=step * 10, total=None, message=f"Local inference running ({step * 10 + int(threshold_s)}s)...")
                if asyncio.iscoroutine(res):
                    if loop is not None and loop.is_running():
                        asyncio.run_coroutine_threadsafe(res, loop)
                    else:
                        asyncio.run(res)
            except (RuntimeError, OSError, ValueError) as exc:
                _LOGGER.debug("Progress heartbeat failed: %s", exc)
            step += 1
            if stop_event.wait(_PROGRESS_INTERVAL_S):
                break

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return thread


def run_warmup(cfg: BridgeConfig, presets: Sequence[str] | None = None) -> list[str]:
    """Preload configured warmup presets via lightweight max_tokens=1 requests."""
    targets = list(presets) if presets is not None else list(cfg.warmup_presets)
    targets = [p.strip() for p in targets if p.strip() and _is_preset_allowed(p.strip(), cfg)]
    if not targets:
        return []
    supervisor = LlamaSupervisor(cfg)
    if not supervisor.ensure_running():
        return []
    headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else None
    warmed: list[str] = []
    for preset in targets:
        try:
            resp = requests.post(
                f"http://{cfg.host}:{cfg.port}/v1/chat/completions",
                json={
                    "model": preset,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                    "stream": False,
                },
                headers=headers,
                timeout=float(cfg.request_timeout_s),
            )
            if resp.status_code == 200:
                METRICS.record_model_load(preset)
                warmed.append(preset)
        except requests.RequestException:
            pass
    return warmed

__all__ = [
    "DEFAULT_IGNORES",
    "_build_prompt_with_files",
    "_collect_image_uris",
    "_discover_ancestor_gitignores",
    "_encode_image_data_uri",
    "_expand_target_files",
    "_is_gitignored",
    "_load_gitignore_spec",
    "_matches_any_pattern",
    "_register_configured_tools",
    "_register_custom_preset_tools",
    "_standalone_rel_path",
    "_walk_dir_pruned",
    "ask_analyst",
    "ask_batch",
    "ask_coder",
    "ask_coder_fast",
    "ask_coder_long",
    "ask_digest",
    "ask_generalist",
    "ask_preset",
    "ask_reasoner",
    "ask_vision",
    "ask_vision_deep",
    "check_and_reload_models_ini",
    "get_bridge_status",
    "main",
    "mcp",
    "query_llama",
    "run_warmup",
]

def _encode_image_data_uri(image_path: str) -> str:
    p = Path(image_path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"Image file not found: {image_path}")
    mime_type, _ = mimetypes.guess_type(str(p))
    if not mime_type or not mime_type.startswith("image/"):
        mime_type = "image/png"
    with p.open("rb") as img_file:
        encoded = base64.b64encode(img_file.read()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def _collect_image_uris(
    image_path: str | None = None,
    image_paths: list[str] | None = None,
    max_images: int = 8,
) -> tuple[list[str], str | None]:
    raw_list: list[str] = []
    if image_path:
        raw_list.append(image_path)
    if image_paths:
        raw_list.extend(image_paths)

    if not raw_list:
        return [], "Error: At least one image path must be provided."
    if len(raw_list) > max_images:
        return [], f"Error: Too many images attached ({len(raw_list)} > max {max_images} for this model's context window)."

    uris: list[str] = []
    for path_str in raw_list:
        try:
            uris.append(_encode_image_data_uri(path_str))
        except OSError as e:
            return [], f"Error reading image file at {path_str}: {e}"
    return uris, None


def query_llama(
    model: str,
    system_prompt: str,
    prompt: str,
    max_tokens: int = 4096,
    timeout: int = 180,
    image_data_uris: list[str] | None = None,
    json_schema: dict[str, Any] | None = None,
    stream: bool = False,
    timeout_s: float | None = None,
    ctx: Any = None,
) -> str:
    check_and_reload_models_ini(ctx=ctx)
    config = load_config()
    if not _is_preset_allowed(model, config):
        return f"Error: Access denied for preset '{model}' (not in allowed_presets)."
    if config.models_ini:
        ini_path = Path(config.models_ini)
        if ini_path.is_file():
            cp = configparser.ConfigParser()
            try:
                cp.read(ini_path, encoding="utf-8")
                if model not in cp.sections():
                    return f"Preset '{model}' is not configured in models.ini. Run: llama-mcp-models add {model}"
            except configparser.Error:
                pass
    supervisor = LlamaSupervisor(config)
    supervisor.ensure_running()

    effective_timeout: float = timeout_s if (timeout_s is not None and timeout_s > 0) else float(timeout)
    t0 = time.perf_counter()
    stop_event = threading.Event()
    _start_progress_heartbeat(ctx, stop_event)

    try:
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        if image_data_uris:
            user_content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
            for uri in image_data_uris:
                user_content.append({"type": "image_url", "image_url": {"url": uri}})
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": stream,
        }

        if json_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "digest_output",
                    "schema": json_schema,
                },
            }

        post_kwargs: dict[str, Any] = {
            "json": payload,
            "timeout": effective_timeout,
        }
        if stream:
            post_kwargs["stream"] = True
        if config.api_key:
            post_kwargs["headers"] = {"Authorization": f"Bearer {config.api_key}"}

        response = requests.post(
            f"http://{config.host}:{config.port}/v1/chat/completions",
            **post_kwargs,
        )

        if response.status_code in (500, 503):
            exc = RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
            is_oom, diag = classify_inference_error(
                exc,
                supervisor.get_stderr_tail(),
                model,
                _get_cached_hardware(),
                config.residency,
            )
            if is_oom:
                METRICS.record_oom(model)
            METRICS.record_request(model, "oom" if is_oom else "error", time.perf_counter() - t0)
            if is_oom and config.fallback_on_oom and model != "digest" and not image_data_uris:
                fallback_out = query_llama(
                    model="digest",
                    system_prompt=system_prompt,
                    prompt=prompt,
                    max_tokens=max_tokens,
                    timeout=timeout,
                    image_data_uris=None,
                    json_schema=json_schema,
                    stream=stream,
                    timeout_s=timeout_s,
                )
                return (
                    f"[Notice: Preset '{model}' hit VRAM/RAM OOM; automatically fell back to 'digest'.]\n\n"
                    f"{fallback_out}"
                )
            return diag
        if response.status_code != 200:
            METRICS.record_request(model, "error", time.perf_counter() - t0)
            return f"Error: Local model server returned status {response.status_code} ({response.text[:200]})"

        if stream:
            content = _parse_sse_stream(response)
            if not content:
                METRICS.record_request(model, "error", time.perf_counter() - t0)
                return "Error: Empty streamed response content from local model."
            METRICS.record_request(model, "ok", time.perf_counter() - t0)
            return content

        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            METRICS.record_request(model, "error", time.perf_counter() - t0)
            return "Error: Empty choices array from local model."

        message = choices[0].get("message", {})
        content = message.get("content", "")
        if not content:
            METRICS.record_request(model, "error", time.perf_counter() - t0)
            if message.get("reasoning_content"):
                return "Error: Model exhausted max_tokens inside reasoning block before emitting a final answer. Increase max_tokens or simplify prompt."
            return "Error: Empty response content from local model."
        METRICS.record_request(model, "ok", time.perf_counter() - t0)
        return content

    except (TimeoutError, requests.exceptions.Timeout, requests.exceptions.RequestException, ValueError, KeyError) as e:
        is_oom, diag = classify_inference_error(
            e,
            supervisor.get_stderr_tail(),
            model,
            _get_cached_hardware(),
            config.residency,
        )
        if is_oom:
            METRICS.record_oom(model)
        METRICS.record_request(model, "oom" if is_oom else "error", time.perf_counter() - t0)
        if is_oom and config.fallback_on_oom and model != "digest":
            fallback_out = query_llama(
                model="digest",
                system_prompt=system_prompt,
                prompt=prompt,
                max_tokens=max_tokens,
                timeout=timeout,
                image_data_uris=None,
                json_schema=json_schema,
                stream=stream,
                timeout_s=timeout_s,
            )
            dropped_note = " (text-only; images dropped)" if image_data_uris else ""
            return (
                f"[Notice: Preset '{model}' hit VRAM/RAM OOM; automatically fell back to 'digest'{dropped_note}.]\n\n"
                f"{fallback_out}"
            )
        return diag
    finally:
        stop_event.set()


def ask_digest(
    prompt: str,
    file_paths: list[str] | None = None,
    ignore: list[str] | None = None,
    include: list[str] | None = None,
    use_gitignore: bool = True,
    gitignore_paths: list[str] | None = None,
    json_schema: dict[str, Any] | None = None,
    stream: bool = False,
    timeout_s: float | None = None,
    ctx: Context | None = None,
) -> str:
    """Use Liquid LFM3 1.7B (non-thinking, 64K context) for bulk log summarisation, doc digestion, and strict JSON extraction.

    Supports files, directories, and globs in `file_paths` with automatic `.gitignore` discovery (`use_gitignore=True`), custom `gitignore_paths`, and `include > ignore` precedence.
    Pass `json_schema` (a JSON Schema dict) to enforce grammar-constrained valid JSON output.
    Limits: 65536 context (~200K chars), 4096 max_tokens. Timeout: 90s.
    """
    full_prompt, err = _build_prompt_with_files(
        prompt,
        file_paths,
        max_total_chars=200_000,
        ignore=ignore,
        include=include,
        use_gitignore=use_gitignore,
        gitignore_paths=gitignore_paths,
    )
    if err:
        return err

    system_prompt = (
        "You are a high-speed closed-context extraction and summarisation engine. "
        "Extract exact facts, errors, or structured JSON strictly from the provided text and attached files without hallucinating."
    )
    raw = query_llama(
        model="digest",
        system_prompt=system_prompt,
        prompt=full_prompt,
        max_tokens=4096,
        timeout=90,
        json_schema=json_schema,
        stream=stream,
        timeout_s=timeout_s,
        ctx=ctx,
    )
    return clean_mcp_output(raw)


def ask_coder_fast(
    prompt: str,
    file_paths: list[str] | None = None,
    ignore: list[str] | None = None,
    include: list[str] | None = None,
    use_gitignore: bool = True,
    gitignore_paths: list[str] | None = None,
    stream: bool = False,
    timeout_s: float | None = None,
    ctx: Context | None = None,
) -> str:
    """Use Qwen 2.5 Coder 7B (non-thinking) for rapid single-file code generation, boilerplate, regexes, and quick syntax edits.

    Supports files, directories, and globs in `file_paths` with `.gitignore` discovery (`use_gitignore=True`), custom `gitignore_paths`, and `include > ignore` precedence.
    Limits: 16384 context (~50K chars), 2048 max_tokens. Timeout: 90s.
    """
    full_prompt, err = _build_prompt_with_files(
        prompt,
        file_paths,
        max_total_chars=50_000,
        ignore=ignore,
        include=include,
        use_gitignore=use_gitignore,
        gitignore_paths=gitignore_paths,
    )
    if err:
        return err

    system_prompt = "You are an expert software engineer. Provide clean, concise, production-ready code without unnecessary preamble."
    raw = query_llama(
        model="coder-fast",
        system_prompt=system_prompt,
        prompt=full_prompt,
        max_tokens=2048,
        timeout=90,
        stream=stream,
        timeout_s=timeout_s,
        ctx=ctx,
    )
    return clean_mcp_output(raw)


def ask_coder(
    prompt: str,
    file_paths: list[str] | None = None,
    ignore: list[str] | None = None,
    include: list[str] | None = None,
    use_gitignore: bool = True,
    gitignore_paths: list[str] | None = None,
    stream: bool = False,
    timeout_s: float | None = None,
    ctx: Context | None = None,
) -> str:
    """Use Qwen 3.5 9B for standard code generation, refactoring, and routine syntax tasks.

    Supports files, directories, and globs in `file_paths` with `.gitignore` discovery (`use_gitignore=True`), custom `gitignore_paths`, and `include > ignore` precedence.
    Limits: 16384 context (~50K chars), 6144 max_tokens (2048 thinking budget + 4096 answer headroom). Timeout: 150s.
    """
    full_prompt, err = _build_prompt_with_files(
        prompt,
        file_paths,
        max_total_chars=50_000,
        ignore=ignore,
        include=include,
        use_gitignore=use_gitignore,
        gitignore_paths=gitignore_paths,
    )
    if err:
        return err

    system_prompt = "You are an expert software engineer. Provide clean, efficient, production-grade code."
    raw = query_llama(
        model="coder",
        system_prompt=system_prompt,
        prompt=full_prompt,
        max_tokens=6144,
        timeout=150,
        stream=stream,
        timeout_s=timeout_s,
        ctx=ctx,
    )
    return clean_mcp_output(raw)


@mcp.tool()
def get_bridge_status() -> str:
    """Return server health, hardware tier, and loaded/unloaded model status as JSON."""
    supervisor = LlamaSupervisor(load_config())
    return json.dumps(supervisor.get_status(), indent=2)


def ask_coder_long(
    prompt: str,
    file_paths: list[str] | None = None,
    ignore: list[str] | None = None,
    include: list[str] | None = None,
    use_gitignore: bool = True,
    gitignore_paths: list[str] | None = None,
    stream: bool = False,
    timeout_s: float | None = None,
    ctx: Context | None = None,
) -> str:
    """Use Qwen 3.5 9B with a 32K context window for multi-file codebase analysis and architectural debugging."""
    full_prompt, err = _build_prompt_with_files(
        prompt,
        file_paths,
        max_total_chars=100_000,
        ignore=ignore,
        include=include,
        use_gitignore=use_gitignore,
        gitignore_paths=gitignore_paths,
    )
    if err:
        return err
    raw = query_llama(
        model="coder-long",
        system_prompt="You are a senior software engineer analysing a multi-file codebase. Be thorough, precise, and cite exact file paths and symbols.",
        prompt=full_prompt,
        max_tokens=6144,
        timeout=300,
        stream=stream,
        timeout_s=timeout_s,
        ctx=ctx,
    )
    cleaned = clean_mcp_output(raw)
    if file_paths:
        files, _ = _expand_target_files(
            file_paths,
            ignore=ignore,
            include=include,
            use_gitignore=use_gitignore,
            gitignore_paths=gitignore_paths,
        )
        total_files = len(files)
        included_count = full_prompt.count("<file path=")
        omitted = [str(f) for f in files[included_count:]] if included_count < total_files else []
        footer = f"\n\n[Context Budget: {included_count}/{total_files} files included, {len(full_prompt)} chars]"
        if omitted:
            footer += f" Omitted: {', '.join(omitted[:5])}"
        return cleaned + footer
    return cleaned


def ask_vision(
    prompt: str,
    image_paths: list[str] | str,
    stream: bool = False,
    timeout_s: float | None = None,
    ctx: Context | None = None,
) -> str:
    """Use Qwen 3.5 4B Vision for rapid UI inspection, screenshot reading, and OCR."""
    paths = [image_paths] if isinstance(image_paths, str) else list(image_paths)
    uris = [_encode_image_data_uri(p) for p in paths]
    raw = query_llama(
        model="vision",
        system_prompt="You are a fast, accurate vision assistant. Describe what you see clearly and concisely.",
        prompt=prompt,
        max_tokens=2048,
        timeout=120,
        image_data_uris=uris,
        stream=stream,
        timeout_s=timeout_s,
        ctx=ctx,
    )
    return clean_mcp_output(raw)


def ask_vision_deep(
    prompt: str,
    image_paths: list[str] | str,
    stream: bool = False,
    timeout_s: float | None = None,
    ctx: Context | None = None,
) -> str:
    """Use Qwen 3.5 9B Vision for complex visual reasoning, diagrams, and dense UI analysis."""
    paths = [image_paths] if isinstance(image_paths, str) else list(image_paths)
    uris = [_encode_image_data_uri(p) for p in paths]
    raw = query_llama(
        model="vision-deep",
        system_prompt="You are a thorough visual reasoning assistant. Analyse diagrams, layouts, and text in detail.",
        prompt=prompt,
        max_tokens=4096,
        timeout=240,
        image_data_uris=uris,
        stream=stream,
        timeout_s=timeout_s,
        ctx=ctx,
    )
    return clean_mcp_output(raw)


def ask_reasoner(
    prompt: str,
    file_paths: list[str] | None = None,
    ignore: list[str] | None = None,
    include: list[str] | None = None,
    use_gitignore: bool = True,
    gitignore_paths: list[str] | None = None,
    stream: bool = False,
    timeout_s: float | None = None,
    ctx: Context | None = None,
) -> str:
    """Use DeepSeek-R1-Distill-Qwen-7B for deep mathematical, algorithmic, and logical reasoning."""
    full_prompt, err = _build_prompt_with_files(
        prompt,
        file_paths,
        ignore=ignore,
        include=include,
        use_gitignore=use_gitignore,
        gitignore_paths=gitignore_paths,
    )
    if err:
        return err
    raw = query_llama(
        model="reasoner",
        system_prompt="You are a rigorous mathematical and algorithmic reasoner. Verify every step carefully.",
        prompt=full_prompt,
        max_tokens=4096,
        timeout=240,
        stream=stream,
        timeout_s=timeout_s,
        ctx=ctx,
    )
    return clean_mcp_output(raw)


def ask_analyst(
    prompt: str,
    file_paths: list[str] | None = None,
    ignore: list[str] | None = None,
    include: list[str] | None = None,
    use_gitignore: bool = True,
    gitignore_paths: list[str] | None = None,
    stream: bool = False,
    timeout_s: float | None = None,
    ctx: Context | None = None,
) -> str:
    """Use Gemma-3-12B-IT for deep technical analysis, auditing, and nuanced prose."""
    full_prompt, err = _build_prompt_with_files(
        prompt,
        file_paths,
        ignore=ignore,
        include=include,
        use_gitignore=use_gitignore,
        gitignore_paths=gitignore_paths,
    )
    if err:
        return err
    raw = query_llama(
        model="analyst",
        system_prompt="You are a senior technical analyst. Provide structured, thorough, and objective analysis.",
        prompt=full_prompt,
        max_tokens=4096,
        timeout=240,
        stream=stream,
        timeout_s=timeout_s,
        ctx=ctx,
    )
    return clean_mcp_output(raw)


def ask_generalist(
    prompt: str,
    file_paths: list[str] | None = None,
    ignore: list[str] | None = None,
    include: list[str] | None = None,
    use_gitignore: bool = True,
    gitignore_paths: list[str] | None = None,
    stream: bool = False,
    timeout_s: float | None = None,
    ctx: Context | None = None,
) -> str:
    """Use Qwen 3.5 9B for general-purpose writing, documentation, and synthesis."""
    full_prompt, err = _build_prompt_with_files(
        prompt,
        file_paths,
        ignore=ignore,
        include=include,
        use_gitignore=use_gitignore,
        gitignore_paths=gitignore_paths,
    )
    if err:
        return err
    raw = query_llama(
        model="generalist",
        system_prompt="You are a helpful, concise technical writer and generalist assistant.",
        prompt=full_prompt,
        max_tokens=4096,
        timeout=180,
        stream=stream,
        timeout_s=timeout_s,
        ctx=ctx,
    )
    return clean_mcp_output(raw)


@mcp.tool()
def ask_preset(
    preset: str,
    prompt: str,
    system_prompt: str = "You are a helpful local specialist assistant.",
    file_paths: list[str] | None = None,
    image_path: str | None = None,
    image_paths: list[str] | None = None,
    ignore: list[str] | None = None,
    include: list[str] | None = None,
    use_gitignore: bool = True,
    gitignore_paths: list[str] | None = None,
    max_tokens: int = 4096,
    json_schema: dict[str, Any] | None = None,
    stream: bool = False,
    timeout_s: float | None = None,
    ctx: Context | None = None,
) -> str:
    """Invoke any configured or custom [preset] section from models.ini by name."""
    full_prompt, err = _build_prompt_with_files(
        prompt,
        file_paths,
        ignore=ignore,
        include=include,
        use_gitignore=use_gitignore,
        gitignore_paths=gitignore_paths,
    )
    if err:
        return err

    image_uris: list[str] | None = None
    if image_path or image_paths:
        image_uris, img_err = _collect_image_uris(image_path=image_path, image_paths=image_paths)
        if img_err:
            return img_err

    raw = query_llama(
        model=preset,
        system_prompt=system_prompt,
        prompt=full_prompt,
        max_tokens=max_tokens,
        timeout=180,
        image_data_uris=image_uris,
        json_schema=json_schema,
        stream=stream,
        timeout_s=timeout_s,
        ctx=ctx,
    )
    return clean_mcp_output(raw)


@mcp.tool()
async def ask_batch(
    preset: str,
    prompts: list[str],
    system_prompt: str = "",
    json_schema: str = "",
    timeout_s: float | None = None,
    ctx: Context | None = None,
) -> str:
    """Run N prompts in parallel against a preset (bounded by its configured parallel slots)."""
    config = load_config()
    if not _is_preset_allowed(preset, config):
        return json.dumps({"error": f"Access denied for preset '{preset}' (not in allowed_presets)."})
    if not prompts:
        return json.dumps({"preset": preset, "parallel": 1, "results": []})

    parsed_schema: dict[str, Any] | None = None
    if json_schema and json_schema.strip():
        try:
            loaded = json.loads(json_schema)
            if isinstance(loaded, dict):
                parsed_schema = loaded
        except json.JSONDecodeError as exc:
            return json.dumps({"error": f"Invalid json_schema: {exc}"})

    parallel = 1
    if config.models_ini and Path(config.models_ini).is_file():
        cp = configparser.ConfigParser()
        try:
            cp.read(config.models_ini, encoding="utf-8")
            raw_p = cp.get(preset, "parallel", fallback="") or cp.get("*", "parallel", fallback="1")
            parallel = max(1, int(raw_p))
        except (configparser.Error, ValueError):
            parallel = 1

    sem = asyncio.Semaphore(parallel)

    async def _run_one(idx: int, item_prompt: str) -> dict[str, Any]:
        async with sem:
            try:
                raw = await asyncio.to_thread(
                    query_llama,
                    preset,
                    system_prompt,
                    item_prompt,
                    4096,
                    180,
                    None,
                    parsed_schema,
                    False,
                    timeout_s,
                    ctx,
                )
                cleaned = clean_mcp_output(raw)
                ok = not cleaned.startswith("Error:")
                return {"index": idx, "ok": ok, "output": cleaned}
            except (RuntimeError, OSError, ValueError) as exc:
                return {"index": idx, "ok": False, "output": f"Error: {exc}"}

    results = await asyncio.gather(*[_run_one(i, p) for i, p in enumerate(prompts)])
    return json.dumps({"preset": preset, "parallel": parallel, "results": list(results)}, indent=2)


_BUILTIN_TOOL_MAP = {
    "digest": ask_digest,
    "coder-fast": ask_coder_fast,
    "coder": ask_coder,
    "coder-long": ask_coder_long,
    "vision": ask_vision,
    "vision-deep": ask_vision_deep,
    "reasoner": ask_reasoner,
    "analyst": ask_analyst,
    "generalist": ask_generalist,
}


def _register_configured_tools(target_mcp: FastMCP | None = None) -> list[str]:
    """Register only configured built-in and custom [preset] MCP tools from models.ini."""
    with _TOOL_REG_LOCK:
        return _register_configured_tools_unlocked(target_mcp)


def _register_configured_tools_unlocked(target_mcp: FastMCP | None = None) -> list[str]:
    srv = target_mcp or mcp
    # Guard FastMCP internal local_provider._components clearing so API changes fail loudly.
    local_provider = getattr(srv, "local_provider", None)
    components = getattr(local_provider, "_components", None)
    if not isinstance(components, dict):
        raise TypeError("FastMCP local_provider._components is unavailable; cannot refresh registered tools.")
    for key in [
        k
        for k in list(components.keys())
        if k.startswith("tool:") and not k.startswith(("tool:get_bridge_status", "tool:ask_preset", "tool:ask_batch"))
    ]:
        components.pop(key, None)

    cfg = load_config()
    ini_path = Path(cfg.models_ini)
    global _MODELS_INI_STATE
    _MODELS_INI_STATE = _snapshot_models_ini(ini_path)
    if not ini_path.is_file():
        _LOGGER.warning("models.ini not found at %s; exposing only get_bridge_status and ask_preset.", ini_path)
        return []

    cp = configparser.ConfigParser()
    try:
        cp.read(ini_path, encoding="utf-8")
    except configparser.Error:
        return []

    registered = []
    for section in cp.sections():
        if section == "*":
            continue
        if not _is_preset_allowed(section, cfg):
            continue
        if section in _BUILTIN_TOOL_MAP:
            fn = _BUILTIN_TOOL_MAP[section]
            srv.tool()(fn)  # type: ignore[type-var]  # FastMCP tool() decorator TypeVar expects concrete callable
            registered.append(fn.__name__)
            continue
        safe_suffix = re.sub(r"[^a-zA-Z0-9_]", "_", section).strip("_")
        if not safe_suffix:
            continue
        tool_name = f"ask_{safe_suffix}"

        def _make_tool(preset_name: str, fn_name: str) -> None:
            def _dynamic_tool(
                prompt: str,
                system_prompt: str = "You are a helpful local specialist assistant.",
                file_paths: list[str] | None = None,
                image_path: str | None = None,
                image_paths: list[str] | None = None,
                ignore: list[str] | None = None,
                include: list[str] | None = None,
                use_gitignore: bool = True,
                gitignore_paths: list[str] | None = None,
                max_tokens: int = 4096,
                stream: bool = False,
                timeout_s: float | None = None,
                ctx: Context | None = None,
            ) -> str:
                return ask_preset(
                    preset=preset_name,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    file_paths=file_paths,
                    image_path=image_path,
                    image_paths=image_paths,
                    ignore=ignore,
                    include=include,
                    use_gitignore=use_gitignore,
                    gitignore_paths=gitignore_paths,
                    max_tokens=max_tokens,
                    stream=stream,
                    timeout_s=timeout_s,
                    ctx=ctx,
                )

            _dynamic_tool.__name__ = fn_name
            _dynamic_tool.__doc__ = f"Invoke custom models.ini preset '{preset_name}'."
            srv.tool()(_dynamic_tool)

        _make_tool(section, tool_name)
        registered.append(tool_name)
    return registered


def _register_custom_preset_tools() -> list[str]:
    """Backward-compatible wrapper returning configured tools registered from models.ini."""
    return _register_configured_tools()


def main() -> None:
    """Entry point for the llama-mcp-bridge MCP server."""
    parser = argparse.ArgumentParser(description="Run the llama-mcp-bridge MCP server.")
    parser.add_argument(
        "--version",
        action="version",
        version=f"llama-mcp-bridge {importlib.metadata.version('llama-mcp-bridge')}",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="WARNING",
        help="Logging verbosity level.",
    )
    parser.add_argument(
        "--log-format",
        choices=["text", "json"],
        default=None,
        help="Logging output format (text or json).",
    )
    parser.add_argument("--dir", "--home", dest="workspace_dir", type=str, help="Explicit workspace directory.")
    parser.add_argument("--workspace-name", type=str, help="Target a registered named workspace.")
    parser.add_argument("--metrics", action="store_true", help="Enable Prometheus /metrics sidecar HTTP server (default port 9090).")
    parser.add_argument("--metrics-port", type=int, default=0, help="Port for Prometheus /metrics sidecar HTTP server.")
    parser.add_argument("--enterprise", action="store_true", help="Enable opt-in [enterprise] features (audit log, probes, multi-tenant).")
    parser.add_argument("--audit-log", type=str, default=None, help="Path to rotating JSONL audit log (requires --enterprise).")
    parser.add_argument("--watchdog", action="store_true", help="Enable systemd sd_notify watchdog heartbeat (requires --enterprise).")
    parser.add_argument("--shared-models-dir", type=str, default=None, help="Shared read-only GGUF directory (requires --enterprise).")
    parser.add_argument("--tenant", type=str, default=None, help="Tenant identifier for isolated workspace (requires --enterprise).")
    parser.add_argument(
        "--warmup",
        nargs="?",
        const="",
        default=None,
        metavar="PRESETS",
        help="Comma-separated presets to preload asynchronously on startup.",
    )
    args, _ = parser.parse_known_args()
    if args.enterprise:
        os.environ["LLAMA_MCP_ENTERPRISE"] = "1"
    if args.tenant:
        os.environ["LLAMA_MCP_TENANT"] = args.tenant
    if args.workspace_dir or args.workspace_name:
        os.environ["LLAMA_MCP_HOME"] = str(
            resolve_workspace(args.workspace_dir, workspace_name=args.workspace_name)
        )
    cfg = load_config()
    if (args.audit_log or args.watchdog or args.shared_models_dir or args.tenant) and not (
        args.enterprise or cfg.enterprise_mode
    ):
        parser.error("Enterprise flags (--audit-log, --watchdog, --shared-models-dir, --tenant) require --enterprise or enterprise_mode = true.")
    configure_logging(args.log_level, args.log_format or cfg.log_format)
    _register_configured_tools()
    metrics_port = args.metrics_port or cfg.metrics_port or (9090 if args.metrics else 0)
    if metrics_port > 0:
        start_metrics_server(metrics_port, METRICS, host=cfg.host, enterprise_mode=cfg.enterprise_mode)
    if cfg.enterprise_mode and args.watchdog:
        from llama_mcp_bridge.enterprise.probes import sd_notify, start_watchdog_loop

        sd_notify("READY=1")
        start_watchdog_loop()
    warmup_list: list[str] | None = None
    if args.warmup is not None:
        warmup_list = [p.strip() for p in args.warmup.split(",") if p.strip()] or list(cfg.warmup_presets)
    elif cfg.warmup_presets:
        warmup_list = list(cfg.warmup_presets)
    if warmup_list:
        threading.Thread(target=run_warmup, args=(cfg, warmup_list), daemon=True).start()
    mcp.run()


if __name__ == "__main__":
    main()
