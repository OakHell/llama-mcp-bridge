"""Configuration module for llama-mcp-bridge.

This module provides a clean configuration system that supports environment
variables, INI file loading, and sensible defaults matching the local_bridge.py
reference implementation.
"""

import configparser
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BridgeConfig:
    """Configuration settings for the Llama MCP Bridge."""

    workspace: str = ""
    server_url: str = "http://127.0.0.1:8080/v1/chat/completions"
    host: str = "127.0.0.1"
    port: int = 8080
    models_dir: str = ""
    models_ini: str = ""
    llama_server_bin: str = ""
    auto_spawn: bool = True
    residency: str = "moderate"
    fallback_on_oom: bool = False
    health_timeout_s: int = 30
    request_timeout_s: int = 90
    max_tokens: int = 4096
    timeout: int = 180
    max_images: int = 8
    max_total_chars: int = 100_000
    default_ignores: frozenset[str] = field(
        default_factory=lambda: frozenset({
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
    )
    use_gitignore: bool = True
    include: list[str] = field(default_factory=list)
    ignore: list[str] = field(default_factory=list)
    gitignore_paths: list[str] = field(default_factory=list)


def get_config_home() -> Path:
    """Determine the canonical configuration home directory.

    Precedence:
    1. ``LLAMA_MCP_HOME`` environment variable (with ``~`` and env-var expansion).
    2. Windows: ``%LOCALAPPDATA%\\llama-mcp-bridge`` (fallback ``~/AppData/Local/llama-mcp-bridge``).
    3. POSIX (Linux, macOS, FreeBSD): ``~/.llama-mcp-bridge``.
    """
    env_home = os.environ.get("LLAMA_MCP_HOME")
    if env_home:
        return Path(os.path.expandvars(env_home)).expanduser()

    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(os.path.expandvars(local_app_data)).expanduser() / "llama-mcp-bridge"

    return Path.home() / ".llama-mcp-bridge"


def resolve_path(raw: str, workspace: Path) -> Path:
    """Expand ``~`` and env vars, resolving relative paths against ``workspace``."""
    expanded = Path(os.path.expandvars(raw)).expanduser()
    if expanded.is_absolute():
        return expanded
    return workspace / expanded


def _load_ini_config(config_path: Path) -> dict[str, str]:
    """Load configuration from an INI file."""
    config_dict: dict[str, str] = {}
    if not config_path.exists():
        return config_dict

    try:
        parser = configparser.ConfigParser()
        parser.read(config_path, encoding="utf-8")
        for section in parser.sections():
            for key, value in parser.items(section):
                config_dict[key] = value
    except (configparser.Error, OSError):
        return {}

    return config_dict


def load_config() -> BridgeConfig:
    """Load and merge configuration from bridge.ini and defaults."""
    config_home = get_config_home()
    bridge_ini_path = config_home / "bridge.ini"

    config_dict: dict[str, str] = {}
    if bridge_ini_path.exists():
        config_dict.update(_load_ini_config(bridge_ini_path))

    config = BridgeConfig()
    int_fields = {
        "port",
        "health_timeout_s",
        "request_timeout_s",
        "max_tokens",
        "timeout",
        "max_images",
        "max_total_chars",
    }
    bool_fields = {"use_gitignore", "auto_spawn", "fallback_on_oom"}

    for key, value in config_dict.items():
        if hasattr(config, key):
            try:
                if key in int_fields:
                    setattr(config, key, int(value))
                elif key in bool_fields:
                    setattr(config, key, value.strip().lower() in ("true", "1", "yes", "on"))
                elif key in ("include", "ignore", "gitignore_paths"):
                    setattr(config, key, [v.strip() for v in value.split(",") if v.strip()])
                elif key == "default_ignores":
                    setattr(config, key, frozenset(v.strip() for v in value.split(",") if v.strip()))
                else:
                    setattr(config, key, value)
            except (ValueError, TypeError):
                pass

    if config.residency:
        config.residency = config.residency.strip().lower()
    else:
        config.residency = "moderate"
    if config.residency not in ("stingy", "moderate", "snappy"):
        raise ValueError(
            f"Invalid residency profile {config.residency!r}; expected one of 'stingy', 'moderate', 'snappy'"
        )

    workspace = resolve_path(config.workspace, config_home) if config.workspace else config_home
    config.workspace = str(workspace)
    config.models_dir = str(
        resolve_path(config.models_dir, workspace) if config.models_dir else workspace / "models"
    )
    config.models_ini = str(
        resolve_path(config.models_ini, workspace) if config.models_ini else workspace / "models.ini"
    )
    if config.llama_server_bin:
        bin_path = str(resolve_path(config.llama_server_bin, workspace))
        if (sys.platform == "win32" or os.name == "nt") and not bin_path.lower().endswith(".exe"):
            bin_path = f"{bin_path}.exe"
        config.llama_server_bin = bin_path

    return config
