"""Dedicated model-management CLI (llama-mcp-models) for llama-mcp-bridge."""

import argparse
import configparser
import sys
from collections.abc import Sequence
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from llama_mcp_bridge.config import load_config
from llama_mcp_bridge.hardware import SystemProfile, detect_hardware
from llama_mcp_bridge.installer import _download_tier_models
from llama_mcp_bridge.presets import (
    _VALID_PRESETS,
    get_tier_model_specs,
    merge_models_ini,
    remove_models_from_ini,
)

_PRESET_DESCRIPTIONS: dict[str, str] = {
    "digest": "High-speed structured JSON & log extraction",
    "coder-fast": "Rapid boilerplate & targeted edits",
    "coder": "Standard code generation & refactoring",
    "coder-long": "Multi-file codebase analysis",
    "vision": "Fast UI inspection, screenshots & OCR",
    "vision-deep": "Complex visual reasoning & diagrams",
    "reasoner": "Mathematical & logical reasoning",
    "analyst": "Deep technical auditing & prose",
    "generalist": "General writing & synthesis",
}


def _read_key() -> str:
    """Read a single keypress on POSIX or Windows."""
    if sys.platform == "win32":
        import msvcrt

        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            nxt = msvcrt.getwch()
            return {"H": "UP", "P": "DOWN"}.get(nxt, "")
        return ch
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            seq = sys.stdin.read(2)
            return {"[A": "UP", "[B": "DOWN"}.get(seq, "")
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def select_presets_interactive(
    profile: SystemProfile,
    initial_selected: Sequence[str] | None = None,
    title: str = "Select Model Presets",
) -> list[str]:
    """Interactive checkbox selector (Up/Down or j/k, Space toggle, a all/none, Enter apply)."""
    console = Console()
    selected = set(initial_selected) if initial_selected is not None else set(_VALID_PRESETS)
    if not sys.stdin.isatty():
        console.print(f"[bold]{title}[/bold] (numbered fallback):")
        for idx, name in enumerate(_VALID_PRESETS, 1):
            mark = "x" if name in selected else " "
            console.print(f"  {idx}. [{mark}] {name} — {_PRESET_DESCRIPTIONS.get(name, '')}")
        raw = Prompt.ask("Enter comma-separated preset names or numbers (Enter to keep)", default="")
        if not raw.strip():
            return [p for p in _VALID_PRESETS if p in selected]
        chosen: list[str] = []
        for tok in raw.split(","):
            t = tok.strip()
            if t.isdigit() and 1 <= int(t) <= len(_VALID_PRESETS):
                chosen.append(_VALID_PRESETS[int(t) - 1])
            elif t in _VALID_PRESETS:
                chosen.append(t)
        return chosen

    preset_sizes = {
        name: sum(s["size_gb"] for s in get_tier_model_specs(profile.tier, presets=[name]))
        for name in _VALID_PRESETS
    }

    def _render_selector(cur: int) -> Group:
        table = Table(title=f"{title} (tier={profile.tier})")
        table.add_column("Select", style="bold")
        table.add_column("Preset", style="cyan")
        table.add_column("Size", justify="right", style="magenta")
        table.add_column("Description")
        for idx, name in enumerate(_VALID_PRESETS):
            pointer = ">" if idx == cur else " "
            box = "[x]" if name in selected else "[ ]"
            sel_text = Text(f"{pointer} {box}", style="bold green" if idx == cur else "bold")
            table.add_row(sel_text, name, f"{preset_sizes[name]:.1f} GB", _PRESET_DESCRIPTIONS.get(name, ""))
        return Group(table, "[dim]Up/Down or j/k: move | Space: toggle | a: all/none | Enter: apply[/dim]")

    cursor = 0
    with Live(_render_selector(cursor), console=console, screen=False, auto_refresh=False) as live:
        while True:
            key = _read_key()
            if key in ("UP", "k"):
                cursor = (cursor - 1) % len(_VALID_PRESETS)
            elif key in ("DOWN", "j"):
                cursor = (cursor + 1) % len(_VALID_PRESETS)
            elif key == " ":
                name = _VALID_PRESETS[cursor]
                if name in selected:
                    selected.remove(name)
                else:
                    selected.add(name)
            elif key.lower() == "a":
                selected = set() if len(selected) == len(_VALID_PRESETS) else set(_VALID_PRESETS)
            elif key in ("\r", "\n"):
                return [p for p in _VALID_PRESETS if p in selected]
            live.update(_render_selector(cursor), refresh=True)


def _configured_presets(models_ini: Path) -> set[str]:
    if not models_ini.is_file():
        return set()
    cp = configparser.ConfigParser()
    cp.read(models_ini, encoding="utf-8")
    return {s for s in cp.sections() if s in _VALID_PRESETS}


def _print_models_list(profile: SystemProfile, models_ini: Path, models_dir: Path) -> None:
    console = Console()
    configured = _configured_presets(models_ini)
    specs_by_role = {s["role"]: s for s in get_tier_model_specs(profile.tier)}
    table = Table(title=f"llama-mcp-models (tier={profile.tier})")
    table.add_column("Preset", style="bold")
    table.add_column("Configured (models.ini)")
    table.add_column("GGUF On Disk")
    table.add_column("Size")
    table.add_column("Filename")
    for name in _VALID_PRESETS:
        spec = specs_by_role.get(name) or specs_by_role.get("coder") or {}
        fname = spec.get("filename", "-")
        on_disk = fname != "-" and (models_dir / fname).is_file()
        cfg_badge = "[green]YES[/green]" if name in configured else "[dim]NO[/dim]"
        disk_badge = "[green]YES[/green]" if on_disk else "[dim]NO[/dim]"
        size_str = f"{spec.get('size_gb', '?')} GB" if spec else "-"
        table.add_row(name, cfg_badge, disk_badge, size_str, fname)
    console.print(table)


def main() -> None:
    """Entry point for llama-mcp-models CLI."""
    parser = argparse.ArgumentParser(description="Manage configured presets and GGUF models.")
    parser.add_argument("action", nargs="?", choices=["list", "add", "remove"], help="Action to run.")
    parser.add_argument("presets", nargs="*", help="Preset names for add/remove.")
    parser.add_argument("--add", dest="add_flag", nargs="+", metavar="PRESET", help="Add presets.")
    parser.add_argument("--remove", dest="remove_flag", nargs="+", metavar="PRESET", help="Remove presets.")
    parser.add_argument("-y", "--yes", action="store_true", help="Confirm destructive removals.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without modifying files.")
    args = parser.parse_args()

    cfg = load_config()
    profile = detect_hardware()
    models_ini = Path(cfg.models_ini)
    models_dir = Path(cfg.models_dir)

    add_targets = args.add_flag or (args.presets if args.action == "add" else None)
    remove_targets = args.remove_flag or (args.presets if args.action == "remove" else None)

    if args.action == "list":
        _print_models_list(profile, models_ini, models_dir)
        return
    if add_targets:
        if args.dry_run:
            print(f"[DRY-RUN] Would add presets {add_targets} to {models_ini}")
        else:
            merge_models_ini(models_ini, profile, cfg.residency, models_dir, add_targets)
        _download_tier_models(profile, models_dir, dry_run=args.dry_run, presets=add_targets)
        return
    if remove_targets:
        if args.dry_run:
            print(f"[DRY-RUN] Would remove presets {remove_targets} from {models_ini}")
            return
        if not args.yes and (not sys.stdin.isatty() or not Confirm.ask(f"Remove {remove_targets} and unshared GGUFs?", default=False)):
            print("Cancelled.")
            return
        deleted = remove_models_from_ini(models_ini, remove_targets, models_dir, delete_files=True)
        print(f"Removed {remove_targets}. Deleted unreferenced files: {deleted}")
        return

    current = _configured_presets(models_ini)
    chosen = set(select_presets_interactive(profile, initial_selected=sorted(current)))
    to_add = [p for p in _VALID_PRESETS if p in chosen - current]
    to_remove = [p for p in _VALID_PRESETS if p in current - chosen]
    if to_add and not args.dry_run:
        merge_models_ini(models_ini, profile, cfg.residency, models_dir, to_add)
        _download_tier_models(profile, models_dir, dry_run=False, presets=to_add)
    if (
        to_remove
        and not args.dry_run
        and (args.yes or (sys.stdin.isatty() and Confirm.ask(f"Remove unchecked presets {to_remove}?", default=False)))
    ):
        remove_models_from_ini(models_ini, to_remove, models_dir, delete_files=True)
    if not to_add and not to_remove:
        print("No preset changes selected.")


if __name__ == "__main__":
    main()
