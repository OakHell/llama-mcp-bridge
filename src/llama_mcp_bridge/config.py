"""Configuration module for llama-mcp-bridge.

This module provides a clean configuration system that supports environment
variables, INI file loading, and sensible defaults matching the local_bridge.py
reference implementation.
"""

import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BridgeConfig:
    """Configuration settings for the Llama MCP Bridge."""

    server_url: str = "http://127.0.0.1:8080/v1/chat/completions"
    host: str = "127.0.0.1"
    port: int = 8080
    models_ini: str = ""
    llama_server_bin: str = ""
    auto_spawn: bool = True
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


def _get_config_home() -> Path:
    """Determine the configuration home directory."""
    env_home = os.environ.get("LLAMA_MCP_HOME")
    if env_home:
        return Path(env_home).expanduser()

    standard_home = Path.home() / ".config" / "llama-mcp-bridge"
    if standard_home.exists():
        return standard_home

    if Path("/mnt/ai/models.ini").exists():
        return Path("/mnt/ai")

    return standard_home


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
    except Exception:
        pass

    return config_dict


def load_config() -> BridgeConfig:
    """Load and merge configuration from multiple sources."""
    config_home = _get_config_home()
    bridge_ini_path = config_home / "bridge.ini"
    models_ini_path = Path("/mnt/ai/models.ini")

    config_dict: dict[str, str] = {}
    if bridge_ini_path.exists():
        config_dict.update(_load_ini_config(bridge_ini_path))
    if models_ini_path.exists():
        config_dict.update(_load_ini_config(models_ini_path))

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
    bool_fields = {"use_gitignore", "auto_spawn"}

    for key, value in config_dict.items():
        if hasattr(config, key):
            try:
                if key in int_fields:
                    setattr(config, key, int(value))
                elif key in bool_fields:
                    setattr(config, key, value.lower() in ("true", "1", "yes"))
                elif key in ("include", "ignore", "gitignore_paths"):
                    setattr(config, key, [v.strip() for v in value.split(",") if v.strip()])
                elif key == "default_ignores":
                    setattr(config, key, frozenset(v.strip() for v in value.split(",") if v.strip()))
                else:
                    setattr(config, key, value)
            except (ValueError, TypeError):
                pass

    return config
