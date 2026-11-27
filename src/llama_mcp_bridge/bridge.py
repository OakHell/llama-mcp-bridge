# /// script
# dependencies = [
#     "fastmcp",
#     "pathspec",
#     "requests",
# ]
# ///

import argparse
import base64
import configparser
import importlib.metadata
import json
import logging
import mimetypes
import os
import re
import threading
from pathlib import Path
from typing import Any

import requests
from fastmcp import FastMCP

from llama_mcp_bridge.config import load_config
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
from llama_mcp_bridge.paths import resolve_workspace
from llama_mcp_bridge.supervisor import LlamaSupervisor, classify_inference_error

mcp = FastMCP("Local Model Specialist")
_LOGGER = logging.getLogger(__name__)
_TOOL_REG_LOCK = threading.RLock()
_CACHED_HARDWARE: SystemProfile | None = None


def _get_cached_hardware() -> SystemProfile:
    """Return a process-cached SystemProfile to avoid re-probing hardware on every error."""
    global _CACHED_HARDWARE
    if _CACHED_HARDWARE is None:
        _CACHED_HARDWARE = detect_hardware()
    return _CACHED_HARDWARE

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
    "ask_coder",
    "ask_coder_fast",
    "ask_coder_long",
    "ask_digest",
    "ask_generalist",
    "ask_preset",
    "ask_reasoner",
    "ask_vision",
    "ask_vision_deep",
    "get_bridge_status",
    "main",
    "mcp",
    "query_llama",
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
) -> str:
    config = load_config()
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
            "stream": False,
        }

        if json_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "digest_output",
                    "schema": json_schema,
                },
            }

        response = requests.post(
            f"http://{config.host}:{config.port}/v1/chat/completions",
            json=payload,
            timeout=timeout,
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
            if is_oom and config.fallback_on_oom and model != "digest" and not image_data_uris:
                fallback_out = query_llama(
                    model="digest",
                    system_prompt=system_prompt,
                    prompt=prompt,
                    max_tokens=max_tokens,
                    timeout=timeout,
                    image_data_uris=None,
                    json_schema=json_schema,
                )
                return (
                    f"[Notice: Preset '{model}' hit VRAM/RAM OOM; automatically fell back to 'digest'.]\n\n"
                    f"{fallback_out}"
                )
            return diag
        if response.status_code != 200:
            return f"Error: Local model server returned status {response.status_code} ({response.text[:200]})"

        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            return "Error: Empty choices array from local model."

        message = choices[0].get("message", {})
        content = message.get("content", "")
        if not content:
            if message.get("reasoning_content"):
                return "Error: Model exhausted max_tokens inside reasoning block before emitting a final answer. Increase max_tokens or simplify prompt."
            return "Error: Empty response content from local model."
        return content

    except (TimeoutError, requests.exceptions.Timeout, requests.exceptions.RequestException, ValueError, KeyError) as e:
        is_oom, diag = classify_inference_error(
            e,
            supervisor.get_stderr_tail(),
            model,
            detect_hardware(),
            config.residency,
        )
        if is_oom and config.fallback_on_oom and model != "digest":
            fallback_out = query_llama(
                model="digest",
                system_prompt=system_prompt,
                prompt=prompt,
                max_tokens=max_tokens,
                timeout=timeout,
                image_data_uris=None,
                json_schema=json_schema,
            )
            dropped_note = " (text-only; images dropped)" if image_data_uris else ""
            return (
                f"[Notice: Preset '{model}' hit VRAM/RAM OOM; automatically fell back to 'digest'{dropped_note}.]\n\n"
                f"{fallback_out}"
            )
        return diag


def ask_digest(
    prompt: str,
    file_paths: list[str] | None = None,
    ignore: list[str] | None = None,
    include: list[str] | None = None,
    use_gitignore: bool = True,
    gitignore_paths: list[str] | None = None,
    json_schema: dict[str, Any] | None = None,
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
    )
    return clean_mcp_output(raw)


def ask_coder_fast(
    prompt: str,
    file_paths: list[str] | None = None,
    ignore: list[str] | None = None,
    include: list[str] | None = None,
    use_gitignore: bool = True,
    gitignore_paths: list[str] | None = None,
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
    )
    return clean_mcp_output(raw)


def ask_coder(
    prompt: str,
    file_paths: list[str] | None = None,
    ignore: list[str] | None = None,
    include: list[str] | None = None,
    use_gitignore: bool = True,
    gitignore_paths: list[str] | None = None,
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


def ask_vision(prompt: str, image_paths: list[str] | str) -> str:
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
    )
    return clean_mcp_output(raw)


def ask_vision_deep(prompt: str, image_paths: list[str] | str) -> str:
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
    )
    return clean_mcp_output(raw)


def ask_reasoner(
    prompt: str,
    file_paths: list[str] | None = None,
    ignore: list[str] | None = None,
    include: list[str] | None = None,
    use_gitignore: bool = True,
    gitignore_paths: list[str] | None = None,
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
    )
    return clean_mcp_output(raw)


def ask_analyst(
    prompt: str,
    file_paths: list[str] | None = None,
    ignore: list[str] | None = None,
    include: list[str] | None = None,
    use_gitignore: bool = True,
    gitignore_paths: list[str] | None = None,
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
    )
    return clean_mcp_output(raw)


def ask_generalist(
    prompt: str,
    file_paths: list[str] | None = None,
    ignore: list[str] | None = None,
    include: list[str] | None = None,
    use_gitignore: bool = True,
    gitignore_paths: list[str] | None = None,
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
    )
    return clean_mcp_output(raw)


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
        if k.startswith("tool:") and not k.startswith(("tool:get_bridge_status", "tool:ask_preset"))
    ]:
        components.pop(key, None)

    cfg = load_config()
    ini_path = Path(cfg.models_ini)
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
        if section in _BUILTIN_TOOL_MAP:
            fn = _BUILTIN_TOOL_MAP[section]
            srv.tool()(fn)  # type: ignore[type-var]
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
    parser.add_argument("--dir", "--home", dest="workspace_dir", type=str, help="Explicit workspace directory.")
    args, _ = parser.parse_known_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if args.workspace_dir:
        os.environ["LLAMA_MCP_HOME"] = str(resolve_workspace(args.workspace_dir))
    _register_configured_tools()
    mcp.run()


if __name__ == "__main__":
    main()
