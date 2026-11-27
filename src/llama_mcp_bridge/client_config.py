"""MCP client configuration, snippet generation, and harness cleanup for llama-mcp-bridge."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from llama_mcp_bridge.config import load_config
from llama_mcp_bridge.paths import get_default_workspace, read_registered_workspace

__all__ = [
    "_HARNESS_GUIDES",
    "_build_mcp_snippet",
    "_cleanup_harness_mcp_configs",
    "_configure_client",
    "_resolve_bridge_command",
    "_resolve_harness_config_paths",
]

_HARNESS_GUIDES: dict[str, tuple[str, str]] = {
    "claude-code": ("~/.claude.json", "https://docs.anthropic.com/en/docs/claude-code/mcp"),
    "cursor": ("~/.cursor/mcp.json", "https://docs.cursor.com/context/model-context-protocol"),
    "cline": ("VS Code globalStorage cline_mcp_settings.json", "https://docs.cline.bot/mcp-servers/configuring-mcp-servers"),
    "windsurf": ("~/.codeium/windsurf/mcp_config.json", "https://docs.windsurf.com/windsurf/mcp"),
    "zed": ("~/.config/zed/settings.json", "https://zed.dev/docs/assistant/model-context-protocol"),
    "antigravity": ("~/.gemini/config/mcp_config.json", "https://antigravity.google/docs/mcp"),
    "other": ("Your MCP client's mcpServers JSON config", "https://modelcontextprotocol.io/quickstart/user"),
}


def _inst_attr(name: str, default: Any) -> Any:
    mod = sys.modules.get("llama_mcp_bridge.installer")
    return getattr(mod, name, default) if mod is not None else default


def _resolve_bridge_command() -> str:
    """Resolve llama-mcp-bridge to an absolute executable path on both POSIX and Windows."""
    exe_name = "llama-mcp-bridge.exe" if sys.platform == "win32" else "llama-mcp-bridge"
    candidates = [
        Path(sys.executable).parent / exe_name,
        Path.home() / ".local" / "bin" / exe_name,
    ]
    for cand in candidates:
        if cand.is_file():
            return str(cand)

    which = shutil.which("llama-mcp-bridge") or (shutil.which(exe_name) if sys.platform == "win32" else None)
    if which:
        return which
    return "llama-mcp-bridge"


def _build_mcp_snippet(workspace: Path) -> str:
    """Build mcp_config.json snippet, omitting LLAMA_MCP_HOME when globally registered or default."""
    resolve_cmd = _inst_attr("_resolve_bridge_command", _resolve_bridge_command)
    server_cfg: dict[str, Any] = {"command": resolve_cmd()}
    resolved_ws = workspace.expanduser().resolve()
    registered = read_registered_workspace(sys.platform)
    is_default = resolved_ws == get_default_workspace(sys.platform).expanduser().resolve()
    is_registered = registered is not None and resolved_ws == registered.expanduser().resolve()
    if not is_default and not is_registered:
        server_cfg["env"] = {"LLAMA_MCP_HOME": str(workspace)}
    return json.dumps({"mcpServers": {"local-ai": server_cfg}}, indent=2)


def _resolve_harness_config_paths(harness: str, home_dir: Path | None = None) -> list[Path]:
    """Resolve target JSON config file path(s) for a coding harness across POSIX and Windows."""
    home = home_dir or Path.home()
    if harness == "claude-code":
        return [home / ".claude.json"]
    if harness == "cursor":
        return [home / ".cursor" / "mcp.json"]
    if harness == "windsurf":
        return [home / ".codeium" / "windsurf" / "mcp_config.json"]
    if harness == "zed":
        if sys.platform == "win32":
            appdata = Path(os.environ.get("APPDATA") or str(home / "AppData" / "Roaming"))
            return [appdata / "Zed" / "settings.json"]
        return [home / ".config" / "zed" / "settings.json"]
    if harness == "antigravity":
        paths = [home / ".gemini" / "config" / "mcp_config.json"]
        if (home / ".gemini" / "antigravity").exists():
            paths.append(home / ".gemini" / "antigravity" / "mcp_config.json")
        return paths
    if harness == "cline":
        if sys.platform == "win32":
            root = Path(os.environ.get("APPDATA") or str(home / "AppData" / "Roaming"))
        elif sys.platform == "darwin":
            root = home / "Library" / "Application Support"
        else:
            root = home / ".config"
        rel_settings = Path("User") / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json"
        paths = [root / "Code" / rel_settings]
        for variant in ("Code - Insiders", "VSCodium"):
            variant_dir = root / variant
            if variant_dir.exists():
                paths.append(variant_dir / rel_settings)
        return paths
    return []


def _configure_client(harness: str, apply: bool = False, dry_run: bool = False) -> None:
    """Print (or optionally merge-write via --apply) MCP client JSON snippet and official guide URL."""
    console = Console()
    if harness == "prompt":
        harness = Prompt.ask(
            "Choose your coding harness",
            choices=[k for k in _HARNESS_GUIDES if k != "prompt"],
            default="claude-code",
        )
    cfg = load_config()
    target_path, doc_url = _HARNESS_GUIDES.get(harness, _HARNESS_GUIDES["other"])
    if harness == "zed" and sys.platform == "win32":
        target_path = r"%APPDATA%\Zed\settings.json"
    snippet = _inst_attr("_build_mcp_snippet", _build_mcp_snippet)(Path(cfg.workspace))
    console.print(
        Panel(
            f"[bold]Target config (reference):[/bold] {target_path}\n"
            f"[bold]Official guide:[/bold] {doc_url}",
            title=f"MCP Setup: {harness}",
            border_style="cyan",
        )
    )
    console.print(snippet, markup=False, soft_wrap=True)

    if apply:
        targets = _inst_attr("_resolve_harness_config_paths", _resolve_harness_config_paths)(harness)
        if not targets:
            print(f"No automatic config file path known for harness '{harness}'; paste the JSON above manually.")
            return
        entry = json.loads(snippet)["mcpServers"]["local-ai"]
        for cfg_file in targets:
            if dry_run:
                print(f"[DRY-RUN] Would merge 'local-ai' into {cfg_file}")
                continue
            existing: dict[str, Any] = {}
            if cfg_file.is_file():
                try:
                    existing = json.loads(cfg_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    existing = {}
            if not isinstance(existing, dict):
                existing = {}
            servers = existing.get("mcpServers")
            if not isinstance(servers, dict):
                servers = {}
                existing["mcpServers"] = servers
            servers["local-ai"] = entry
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            cfg_file.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
            print(f"Updated MCP config: {cfg_file}")


def _cleanup_harness_mcp_configs(dry_run: bool = False) -> list[Path]:
    """Remove 'local-ai' from known harness mcpServers JSON configs while preserving other entries."""
    home = Path.home()
    resolve_fn = _inst_attr("_resolve_harness_config_paths", _resolve_harness_config_paths)
    candidates: list[Path] = []
    for harness in ("antigravity", "claude-code", "cursor", "cline", "windsurf", "zed"):
        for p in resolve_fn(harness, home_dir=home):
            if p not in candidates:
                candidates.append(p)
    updated: list[Path] = []
    for cfg_file in candidates:
        if not cfg_file.is_file():
            continue
        try:
            data = json.loads(cfg_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        servers = data.get("mcpServers")
        if not isinstance(servers, dict) or "local-ai" not in servers:
            continue
        if dry_run:
            print(f"[DRY-RUN] Would remove 'local-ai' from {cfg_file}")
            updated.append(cfg_file)
            continue
        del servers["local-ai"]
        if not servers:
            del data["mcpServers"]
        cfg_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"Removed 'local-ai' from {cfg_file}")
        updated.append(cfg_file)
    return updated
