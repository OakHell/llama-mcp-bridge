"""Centralised workspace resolution and global registration helpers."""

import os
import sys
from pathlib import Path

__all__ = [
    "get_default_workspace",
    "get_named_workspaces_dir",
    "get_pointer_file",
    "list_named_workspaces",
    "read_named_workspace",
    "read_registered_workspace",
    "register_global_workspace",
    "register_named_workspace",
    "resolve_workspace",
    "unregister_global_workspace",
]


def get_pointer_file(platform_name: str | None = None) -> Path:
    """Return the user-level global workspace pointer file path."""
    plat = platform_name or sys.platform
    if plat == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(os.path.expandvars(base)).expanduser() / "llama-mcp-bridge" / "home"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base_dir = Path(os.path.expandvars(xdg)).expanduser() if xdg else (Path.home() / ".config")
    return base_dir / "llama-mcp-bridge" / "home"


def get_default_workspace(platform_name: str | None = None) -> Path:
    """Return the default OS-specific workspace directory."""
    plat = platform_name or sys.platform
    if plat == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(os.path.expandvars(local_app_data)).expanduser() / "llama-mcp-bridge"
    return Path.home() / ".llama-mcp-bridge"


def read_registered_workspace(platform_name: str | None = None) -> Path | None:
    """Read the globally registered workspace path if the pointer file exists and is non-empty."""
    pointer = get_pointer_file(platform_name)
    if not pointer.is_file():
        return None
    try:
        raw = pointer.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    return Path(os.path.expandvars(raw)).expanduser()


def register_global_workspace(workspace: Path | str, platform_name: str | None = None) -> Path:
    """Persist a user-level workspace pointer and return the pointer file path."""
    ws = Path(os.path.expandvars(str(workspace))).expanduser().resolve()
    pointer = get_pointer_file(platform_name)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(f"{ws}\n", encoding="utf-8")
    return pointer


def unregister_global_workspace(
    only_if_matches: Path | str | None = None,
    platform_name: str | None = None,
) -> bool:
    """Remove the user-level workspace pointer if present (optionally only when matching a path)."""
    pointer = get_pointer_file(platform_name)
    if not pointer.is_file():
        return False
    if only_if_matches is not None:
        current = read_registered_workspace(platform_name)
        target = Path(os.path.expandvars(str(only_if_matches))).expanduser().resolve()
        if current is None or current.resolve() != target:
            return False
    pointer.unlink(missing_ok=True)
    return True


def get_named_workspaces_dir(platform_name: str | None = None) -> Path:
    """Return directory storing named workspace pointer files (<name>.path)."""
    return get_default_workspace(platform_name) / "workspaces"


def register_named_workspace(
    name: str, workspace: Path | str, platform_name: str | None = None
) -> Path:
    """Persist a named workspace pointer under workspaces/<name>.path."""
    clean = name.strip()
    if not clean or "/" in clean or "\\" in clean or ".." in clean:
        raise ValueError(f"Invalid workspace name: {name!r}")
    ws = Path(os.path.expandvars(str(workspace))).expanduser().resolve()
    ptr = get_named_workspaces_dir(platform_name) / f"{clean}.path"
    ptr.parent.mkdir(parents=True, exist_ok=True)
    ptr.write_text(f"{ws}\n", encoding="utf-8")
    return ptr


def read_named_workspace(name: str, platform_name: str | None = None) -> Path | None:
    """Read a named workspace pointer if it exists and is non-empty."""
    clean = name.strip()
    if not clean:
        return None
    ptr = get_named_workspaces_dir(platform_name) / f"{clean}.path"
    if not ptr.is_file():
        return None
    raw = ptr.read_text(encoding="utf-8").strip()
    return Path(os.path.expandvars(raw)).expanduser() if raw else None


def list_named_workspaces(platform_name: str | None = None) -> dict[str, Path]:
    """Return a mapping of registered workspace names to their target Paths."""
    ws_dir = get_named_workspaces_dir(platform_name)
    if not ws_dir.is_dir():
        return {}
    result: dict[str, Path] = {}
    for ptr in sorted(ws_dir.glob("*.path")):
        raw = ptr.read_text(encoding="utf-8").strip()
        if raw:
            result[ptr.stem] = Path(os.path.expandvars(raw)).expanduser()
    return result


def resolve_workspace(
    explicit_dir: Path | str | None = None,
    platform_name: str | None = None,
    workspace_name: str | None = None,
) -> Path:
    """Resolve the active workspace using strict precedence.

    1. Explicit CLI flag (``--dir`` / ``--home``)
    2. ``LLAMA_MCP_HOME`` environment variable (when non-empty)
    3. Named workspace (``--workspace-name`` / ``LLAMA_MCP_WORKSPACE``)
    4. Registered global pointer file
    5. Default OS workspace directory
    """
    if explicit_dir is not None and str(explicit_dir).strip():
        return Path(os.path.expandvars(str(explicit_dir).strip())).expanduser()

    env_home = os.environ.get("LLAMA_MCP_HOME", "").strip()
    if env_home:
        return Path(os.path.expandvars(env_home)).expanduser()

    ws_name = (workspace_name or os.environ.get("LLAMA_MCP_WORKSPACE", "")).strip()
    if ws_name:
        named = read_named_workspace(ws_name, platform_name=platform_name)
        if named is not None:
            return named

    registered = read_registered_workspace(platform_name)
    if registered is not None:
        return registered

    return get_default_workspace(platform_name)
