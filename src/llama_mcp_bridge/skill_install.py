"""Agent skill installation helpers for llama-mcp-bridge."""

from __future__ import annotations

import importlib.resources
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "_install_skill",
    "_resolve_bundled_skill_path",
    "_resolve_skill_destinations",
]


def _inst_attr(name: str, default: Any) -> Any:
    mod = sys.modules.get("llama_mcp_bridge.installer")
    return getattr(mod, name, default) if mod is not None else default


def _resolve_bundled_skill_path() -> Path:
    """Locate skills/llama-mcp-setup/SKILL.md via importlib.resources or repo-root fallback."""
    try:
        pkg_skill = importlib.resources.files("llama_mcp_bridge").joinpath("skills", "llama-mcp-setup", "SKILL.md")
        pkg_path = Path(str(pkg_skill))
        if pkg_path.is_file():
            return pkg_path
    except (ModuleNotFoundError, TypeError, OSError):
        pass
    # Fallback for editable/source checkouts (src/llama_mcp_bridge/skill_install.py -> repo root is parents[2])
    # where package_data is not yet built into site-packages.
    repo_skill = Path(__file__).resolve().parents[2] / "skills" / "llama-mcp-setup" / "SKILL.md"
    if repo_skill.is_file():
        _LOGGER.debug("Using repo-root fallback for SKILL.md: %s", repo_skill)
        return repo_skill
    raise FileNotFoundError("Bundled skills/llama-mcp-setup/SKILL.md not found.")


def _resolve_skill_destinations(
    target: str,
    home_dir: Path | None = None,
    cwd: Path | None = None,
) -> list[Path]:
    """Resolve destination SKILL.md paths for a harness name, 'auto', 'workspace', or custom dir."""
    home = home_dir or Path.home()
    work = cwd or Path.cwd()
    norm = target.strip().lower()

    if norm == "claude":
        return [home / ".claude" / "skills" / "llama-mcp-setup" / "SKILL.md"]
    if norm == "antigravity":
        dests = [home / ".gemini" / "config" / "skills" / "llama-mcp-setup" / "SKILL.md"]
        if (home / ".gemini" / "antigravity").exists():
            dests.append(home / ".gemini" / "antigravity" / "skills" / "llama-mcp-setup" / "SKILL.md")
        return dests
    if norm == "gemini":
        return [home / ".gemini" / "skills" / "llama-mcp-setup" / "SKILL.md"]
    if norm == "cursor":
        return [home / ".cursor" / "skills" / "llama-mcp-setup" / "SKILL.md"]
    if norm == "workspace":
        return [work / ".agents" / "skills" / "llama-mcp-setup" / "SKILL.md"]
    if norm == "auto":
        found: list[Path] = []
        if (home / ".claude").exists():
            found.append(home / ".claude" / "skills" / "llama-mcp-setup" / "SKILL.md")
        if (home / ".gemini" / "config").exists():
            found.append(home / ".gemini" / "config" / "skills" / "llama-mcp-setup" / "SKILL.md")
        if (home / ".gemini" / "antigravity").exists():
            found.append(home / ".gemini" / "antigravity" / "skills" / "llama-mcp-setup" / "SKILL.md")
        if (home / ".gemini").exists():
            found.append(home / ".gemini" / "skills" / "llama-mcp-setup" / "SKILL.md")
        if (home / ".cursor").exists():
            found.append(home / ".cursor" / "skills" / "llama-mcp-setup" / "SKILL.md")
        if not found:
            found.append(work / ".agents" / "skills" / "llama-mcp-setup" / "SKILL.md")
        return list(dict.fromkeys(found))

    custom = Path(target).expanduser()
    if custom.name == "llama-mcp-setup":
        return [custom / "SKILL.md"]
    return [custom / "llama-mcp-setup" / "SKILL.md"]


def _install_skill(
    target: str = "auto",
    symlink: bool = False,
    dry_run: bool = False,
    home_dir: Path | None = None,
    cwd: Path | None = None,
) -> list[Path]:
    """Install the bundled llama-mcp-setup SKILL.md into target harness skill directories."""
    src = _inst_attr("_resolve_bundled_skill_path", _resolve_bundled_skill_path)()
    dests = _inst_attr("_resolve_skill_destinations", _resolve_skill_destinations)(
        target, home_dir=home_dir, cwd=cwd
    )
    for dest in dests:
        if dry_run:
            action = "symlink" if symlink else "copy"
            print(f"[DRY-RUN] Would {action} skill to {dest}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() or dest.is_symlink():
            dest.unlink()
        if symlink:
            os.symlink(src, dest)
        else:
            shutil.copy2(src, dest)
        print(f"Installed llama-mcp-setup skill to {dest}")
    return dests
