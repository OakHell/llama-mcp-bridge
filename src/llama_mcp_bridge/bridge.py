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
import glob as glob_mod
import json
import logging
import mimetypes
import os
import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import pathspec
import requests
from fastmcp import FastMCP

from llama_mcp_bridge.config import load_config
from llama_mcp_bridge.hardware import detect_hardware
from llama_mcp_bridge.hygiene import clean_mcp_output
from llama_mcp_bridge.paths import resolve_workspace
from llama_mcp_bridge.supervisor import LlamaSupervisor, classify_inference_error

mcp = FastMCP("Local Model Specialist")
_LOGGER = logging.getLogger(__name__)

DEFAULT_IGNORES: frozenset[str] = frozenset({
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    "*.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "uv.lock",
    "Cargo.lock",
    "poetry.lock",
    "composer.lock",
})


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


def _load_gitignore_spec(gitignore_file: Path) -> pathspec.GitIgnoreSpec | None:
    try:
        if gitignore_file.is_file():
            lines = gitignore_file.read_text(encoding="utf-8", errors="replace").splitlines()
            return pathspec.GitIgnoreSpec.from_lines(lines)
    except (OSError, ValueError):
        return None
    return None


def _discover_ancestor_gitignores(start_path: Path) -> list[tuple[Path, pathspec.GitIgnoreSpec]]:
    """Walks upward from start_path to the git repo root (if any) collecting (base_dir, spec) pairs."""
    cur = start_path if start_path.is_dir() else start_path.parent
    cur = cur.resolve()
    ancestors: list[Path] = []
    while True:
        ancestors.append(cur)
        if (cur / ".git").exists() or cur.parent == cur:
            break
        cur = cur.parent

    specs: list[tuple[Path, pathspec.GitIgnoreSpec]] = []
    for folder in reversed(ancestors):
        spec = _load_gitignore_spec(folder / ".gitignore")
        if spec is not None:
            specs.append((folder, spec))
    return specs


def _is_gitignored(
    target_path: Path,
    rel_path: str,
    is_dir: bool,
    scoped_specs: list[tuple[Path, pathspec.GitIgnoreSpec]],
    global_specs: list[pathspec.GitIgnoreSpec],
) -> bool:
    """Evaluates scoped (anchor-relative) and global gitignore specs, honouring !negations."""
    resolved = target_path.resolve()
    ignored = False

    for base_dir, spec in scoped_specs:
        try:
            rel = resolved.relative_to(base_dir).as_posix()
        except ValueError:
            continue
        if not rel or rel == ".":
            continue
        check_path = f"{rel}/" if is_dir else rel
        result = spec.check_file(check_path)
        if result.include is not None:
            ignored = result.include

    rel_posix = Path(rel_path).as_posix()
    check_rel = f"{rel_posix}/" if is_dir else rel_posix
    for gspec in global_specs:
        result = gspec.check_file(check_rel)
        if result.include is not None:
            ignored = result.include

    return ignored


def _matches_any_pattern(rel_path: str, patterns: set[str]) -> bool:
    """Checks if a relative file path, filename, or any relative path segment matches a pattern set."""
    rel_obj = Path(rel_path)
    name = rel_obj.name
    parts = rel_obj.parts
    for pat in patterns:
        if fnmatch(name, pat) or fnmatch(rel_path, pat):
            return True
        if any(fnmatch(part, pat) for part in parts):
            return True
    return False


def _walk_dir_pruned(
    root_dir: Path,
    effective_ignores: set[str],
    include_set: set[str],
    use_gitignore: bool,
    global_specs: list[pathspec.GitIgnoreSpec],
    seen: set[Path],
    candidates: list[tuple[Path, str, bool]],
) -> None:
    """Walks root_dir without following symlinks, pruning DEFAULT_IGNORES and nested .gitignore rules."""
    base_specs = _discover_ancestor_gitignores(root_dir) if use_gitignore else []
    dir_specs_map: dict[Path, list[tuple[Path, pathspec.GitIgnoreSpec]]] = {
        root_dir.resolve(): base_specs
    }

    for dirpath, dirnames, filenames in os.walk(root_dir, topdown=True, followlinks=False):
        cur_dir = Path(dirpath)
        cur_resolved = cur_dir.resolve()
        rel_dir = cur_dir.relative_to(root_dir)

        active_specs = list(dir_specs_map.get(cur_resolved, base_specs))
        if use_gitignore and cur_resolved != root_dir.resolve():
            local_spec = _load_gitignore_spec(cur_dir / ".gitignore")
            if local_spec is not None:
                active_specs.append((cur_resolved, local_spec))

        kept_dirs: list[str] = []
        for d in sorted(dirnames):
            sub_path = cur_dir / d
            sub_rel = str(rel_dir / d)
            dir_specs_map[sub_path.resolve()] = active_specs

            if not include_set:
                if _matches_any_pattern(sub_rel, effective_ignores):
                    continue
                if (use_gitignore or global_specs) and _is_gitignored(
                    sub_path,
                    rel_path=sub_rel,
                    is_dir=True,
                    scoped_specs=active_specs,
                    global_specs=global_specs,
                ):
                    continue
            kept_dirs.append(d)
        dirnames[:] = kept_dirs

        for fname in sorted(filenames):
            fpath = cur_dir / fname
            if not fpath.is_file():
                continue
            resolved = fpath.resolve()
            if resolved not in seen:
                seen.add(resolved)
                rel_str = str(fpath.relative_to(root_dir))
                git_ignored = (use_gitignore or bool(global_specs)) and _is_gitignored(
                    fpath,
                    rel_path=rel_str,
                    is_dir=False,
                    scoped_specs=active_specs,
                    global_specs=global_specs,
                )
                candidates.append((fpath, rel_str, git_ignored))


def _standalone_rel_path(file_path: Path) -> str:
    """Computes repo-relative (or cwd-relative) path for standalone files so path-anchored gitignore rules match."""
    resolved = file_path.resolve()
    cur = resolved.parent
    while True:
        if (cur / ".git").exists():
            return resolved.relative_to(cur).as_posix()
        if cur.parent == cur:
            break
        cur = cur.parent
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return file_path.name


def _expand_target_files(
    file_paths: list[str],
    ignore: list[str] | None = None,
    include: list[str] | None = None,
    use_gitignore: bool = True,
    gitignore_paths: list[str] | None = None,
) -> tuple[list[Path], str | None]:
    """Expands files, directories, and globs with include > (gitignore + ignore + defaults) precedence."""
    effective_ignores = set(DEFAULT_IGNORES)
    if ignore:
        effective_ignores.update(ignore)
    include_set = set(include) if include else set()

    global_specs: list[pathspec.GitIgnoreSpec] = []
    if gitignore_paths:
        for gp_raw in gitignore_paths:
            gp = Path(gp_raw).expanduser()
            spec = _load_gitignore_spec(gp)
            if spec is None:
                return [], f"Error: Provided gitignore file not found or unreadable: {gp_raw}"
            global_specs.append(spec)

    candidates: list[tuple[Path, str, bool]] = []
    seen: set[Path] = set()

    for raw in file_paths:
        expanded_str = str(Path(raw).expanduser())
        has_magic = glob_mod.has_magic(expanded_str)

        if has_magic:
            first_magic = min(
                (i for i, ch in enumerate(expanded_str) if ch in "*?["),
                default=len(expanded_str),
            )
            prefix_dir = Path(os.path.dirname(expanded_str[:first_magic]) or ".") if first_magic > 0 else Path(".")
            matches = sorted(glob_mod.glob(expanded_str, recursive=True))
            if not matches:
                return [], f"Error: Glob pattern matched no files: {raw}"
            for m in matches:
                mp = Path(m)
                if mp.is_file():
                    resolved = mp.resolve()
                    if resolved not in seen:
                        seen.add(resolved)
                        try:
                            rel = str(mp.relative_to(prefix_dir))
                        except ValueError:
                            rel = _standalone_rel_path(mp)
                        anc_specs = _discover_ancestor_gitignores(mp) if use_gitignore else []
                        git_ignored = (use_gitignore or bool(global_specs)) and _is_gitignored(
                            mp,
                            rel_path=rel,
                            is_dir=False,
                            scoped_specs=anc_specs,
                            global_specs=global_specs,
                        )
                        candidates.append((mp, rel, git_ignored))
                elif mp.is_dir():
                    _walk_dir_pruned(
                        mp, effective_ignores, include_set, use_gitignore, global_specs, seen, candidates
                    )
        else:
            p = Path(expanded_str)
            if not p.exists():
                return [], f"Error: Attached path not found: {raw}"
            if p.is_file():
                resolved = p.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    rel = _standalone_rel_path(p)
                    anc_specs = _discover_ancestor_gitignores(p) if use_gitignore else []
                    git_ignored = (use_gitignore or bool(global_specs)) and _is_gitignored(
                        p,
                        rel_path=rel,
                        is_dir=False,
                        scoped_specs=anc_specs,
                        global_specs=global_specs,
                    )
                    candidates.append((p, rel, git_ignored))
            elif p.is_dir():
                _walk_dir_pruned(
                    p, effective_ignores, include_set, use_gitignore, global_specs, seen, candidates
                )
            else:
                return [], f"Error: Attached path is neither a regular file nor directory: {raw}"

    filtered: list[Path] = []
    for file_path, rel_path, git_ignored in candidates:
        if include_set and _matches_any_pattern(rel_path, include_set):
            filtered.append(file_path)
            continue
        if git_ignored or _matches_any_pattern(rel_path, effective_ignores):
            continue
        filtered.append(file_path)

    filtered.sort(key=lambda fp: (fp.stat().st_size, str(fp)))
    return filtered, None


def _build_prompt_with_files(
    prompt: str,
    file_paths: list[str] | None = None,
    max_total_chars: int = 100_000,
    ignore: list[str] | None = None,
    include: list[str] | None = None,
    use_gitignore: bool = True,
    gitignore_paths: list[str] | None = None,
) -> tuple[str, str | None]:
    """Expands files/dirs/globs, applies .gitignore + include/ignore precedence, and prepends <file> blocks."""
    if not file_paths:
        return prompt, None

    target_files, err = _expand_target_files(
        file_paths,
        ignore=ignore,
        include=include,
        use_gitignore=use_gitignore,
        gitignore_paths=gitignore_paths,
    )
    if err:
        return "", err

    if not target_files:
        return "", "Error: No files remained after applying ignore/include filters."

    sections: list[str] = []
    remaining = max_total_chars

    for p in target_files:
        if remaining <= 0:
            sections.append(f'<file path="{p}" status="omitted_budget_exhausted" />')
            continue

        try:
            with p.open("rb") as f:
                raw_bytes = f.read(remaining + 1)
        except OSError as e:
            return "", f"Error reading attached file {p}: {e}"

        if b"\x00" in raw_bytes[:4096]:
            sections.append(f'<file path="{p}" status="skipped_binary" />')
            continue

        content = raw_bytes.decode("utf-8", errors="replace")
        if len(content) > remaining:
            content = (
                content[:remaining]
                + f"\n... [TRUNCATED after {remaining} chars to fit context budget]"
            )
            remaining = 0
        else:
            remaining -= len(content)

        sections.append(f'<file path="{p}">\n{content}\n</file>')

    attached_block = "\n\n".join(sections)
    combined = f"Attached Files:\n\n{attached_block}\n\nTask:\n{prompt}"
    return combined, None


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
        messages = []
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
                detect_hardware(),
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


def _encode_image_to_data_uri(image_path: str) -> str:
    """Encode a local image file as a base64 data URI."""
    import base64
    import mimetypes

    path = Path(image_path)
    mime_type, _ = mimetypes.guess_type(str(path))
    if not mime_type:
        mime_type = "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def ask_vision(prompt: str, image_paths: list[str] | str) -> str:
    """Use Qwen 3.5 4B Vision for rapid UI inspection, screenshot reading, and OCR."""
    paths = [image_paths] if isinstance(image_paths, str) else list(image_paths)
    uris = [_encode_image_to_data_uri(p) for p in paths]
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
    uris = [_encode_image_to_data_uri(p) for p in paths]
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
            srv.tool()(fn)
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
    parser.add_argument("--dir", "--home", dest="workspace_dir", type=str, help="Explicit workspace directory.")
    args, _ = parser.parse_known_args()
    if args.workspace_dir:
        os.environ["LLAMA_MCP_HOME"] = str(resolve_workspace(args.workspace_dir))
    _register_configured_tools()
    mcp.run()


if __name__ == "__main__":
    main()
