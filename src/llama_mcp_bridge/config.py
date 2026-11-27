"""Configuration module for llama-mcp-bridge.

This module provides a clean configuration system that supports environment
variables, INI file loading, and sensible defaults matching the local_bridge.py
reference implementation.
"""

import configparser
import os
import sys
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

from llama_mcp_bridge.paths import resolve_workspace

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "BridgeConfig",
    "get_config_home",
    "load_config",
    "migrate_bridge_ini",
    "resolve_path",
]

CURRENT_SCHEMA_VERSION = 1


@dataclass
class BridgeConfig:
    """Configuration settings for the Llama MCP Bridge."""

    workspace: str = ""
    schema_version: int = CURRENT_SCHEMA_VERSION
    log_format: str = "text"
    host: str = "127.0.0.1"
    port: int = 8080
    models_dir: str = ""
    models_ini: str = ""
    llama_server_bin: str = ""
    server_log: str = ""
    api_key: str = ""
    metrics_port: int = 0
    verify_signatures: bool = True
    auto_spawn: bool = True
    residency: str = "moderate"
    models_max: int = 0
    sleep_idle_seconds: int = -1
    fallback_on_oom: bool = False
    enterprise_mode: bool = False
    audit_enabled: bool = False
    audit_log: str = ""
    audit_max_bytes: int = 10_485_760
    audit_backups: int = 5
    shared_models_dir: str = ""
    tenant: str = ""
    allowed_presets: tuple[str, ...] = ()
    warmup_presets: tuple[str, ...] = ()
    startup_timeout_seconds: float = 30.0
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


def get_config_home(explicit_dir: Path | str | None = None) -> Path:
    """Determine the canonical configuration home directory."""
    return resolve_workspace(explicit_dir=explicit_dir, platform_name=sys.platform)


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


_CONFIG_CACHE: dict[tuple[str, int | None, bytes | None, str, str, str, str], BridgeConfig] = {}


def load_config(explicit_dir: Path | str | None = None) -> BridgeConfig:
    """Load and merge configuration from bridge.ini and defaults (mtime + content-cached)."""
    config_home = get_config_home(explicit_dir=explicit_dir)
    bridge_ini_path = config_home / "bridge.ini"
    try:
        if bridge_ini_path.is_file():
            ini_mtime_ns: int | None = bridge_ini_path.stat().st_mtime_ns
            ini_bytes: bytes | None = bridge_ini_path.read_bytes()
        else:
            ini_mtime_ns = None
            ini_bytes = None
    except OSError:
        ini_mtime_ns = None
        ini_bytes = None

    env_api_key = os.environ.get("LLAMA_MCP_API_KEY", "").strip()
    env_enterprise = os.environ.get("LLAMA_MCP_ENTERPRISE", "").strip()
    env_tenant = os.environ.get("LLAMA_MCP_TENANT", "").strip()
    cache_key = (str(config_home), ini_mtime_ns, ini_bytes, sys.platform, env_api_key, env_enterprise, env_tenant)
    if cache_key in _CONFIG_CACHE:
        return replace(_CONFIG_CACHE[cache_key])

    config_dict: dict[str, str] = {}
    if ini_bytes is not None:
        config_dict.update(_load_ini_config(bridge_ini_path))

    config = BridgeConfig()
    int_fields = {f.name for f in fields(BridgeConfig) if f.type in (int, "int")}
    bool_fields = {f.name for f in fields(BridgeConfig) if f.type in (bool, "bool")}

    for key, value in config_dict.items():
        if hasattr(config, key):
            try:
                if key in int_fields:
                    setattr(config, key, int(value))
                elif key in bool_fields:
                    setattr(config, key, value.strip().lower() in ("true", "1", "yes", "on"))
                elif key in ("include", "ignore", "gitignore_paths"):
                    setattr(config, key, [v.strip() for v in value.split(",") if v.strip()])
                elif key in ("allowed_presets", "warmup_presets"):
                    setattr(config, key, tuple(v.strip() for v in value.split(",") if v.strip()))
                elif key == "default_ignores":
                    setattr(config, key, frozenset(v.strip() for v in value.split(",") if v.strip()))
                else:
                    setattr(config, key, value)
            except (ValueError, TypeError):
                pass

    env_api_key = os.environ.get("LLAMA_MCP_API_KEY", "").strip()
    if env_api_key:
        config.api_key = env_api_key
    if env_enterprise.lower() in ("1", "true", "yes", "on"):
        config.enterprise_mode = True
    if env_tenant:
        config.tenant = env_tenant

    if (config.audit_enabled or config.audit_log or config.shared_models_dir or config.tenant) and not config.enterprise_mode:
        raise ValueError(
            "Enterprise features (audit_enabled, audit_log, shared_models_dir, tenant) require --enterprise or enterprise_mode = true."
        )

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
    config.server_log = str(
        resolve_path(config.server_log, workspace) if config.server_log else workspace / "llama-server.log"
    )
    if config.llama_server_bin:
        bin_path = str(resolve_path(config.llama_server_bin, workspace))
        if sys.platform == "win32" and not bin_path.lower().endswith(".exe"):
            bin_path = f"{bin_path}.exe"
        config.llama_server_bin = bin_path

    _CONFIG_CACHE[cache_key] = config
    return replace(config)


def migrate_bridge_ini(bridge_ini: Path, dry_run: bool = False) -> tuple[bool, str]:
    """Migrate bridge.ini to CURRENT_SCHEMA_VERSION with an atomic .bak backup."""
    if not bridge_ini.is_file():
        return False, f"bridge.ini not found at {bridge_ini}"

    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = lambda optionstr: optionstr  # type: ignore[method-assign]  # typeshed types optionxform as a method
    cp.read(bridge_ini, encoding="utf-8")
    if "bridge" not in cp:
        cp["bridge"] = {}

    raw_ver = cp.get("bridge", "schema_version", fallback="0").strip()
    try:
        current_ver = int(raw_ver)
    except ValueError:
        current_ver = 0

    if current_ver >= CURRENT_SCHEMA_VERSION:
        return False, f"{bridge_ini} is already at schema_version={current_ver}."

    if dry_run:
        return True, f"[DRY-RUN] Would back up {bridge_ini} to {bridge_ini}.bak and set schema_version = {CURRENT_SCHEMA_VERSION}."

    backup_path = bridge_ini.with_suffix(".ini.bak")
    backup_path.write_bytes(bridge_ini.read_bytes())
    cp["bridge"]["schema_version"] = str(CURRENT_SCHEMA_VERSION)
    with bridge_ini.open("w", encoding="utf-8") as fh:
        cp.write(fh)
    return True, f"Migrated {bridge_ini} (v{current_ver} -> v{CURRENT_SCHEMA_VERSION}); backup saved to {backup_path}."
