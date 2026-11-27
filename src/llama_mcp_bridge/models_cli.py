"""Dedicated model-management CLI (llama-mcp-models) for llama-mcp-bridge."""

import argparse
import configparser
import importlib.metadata
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from llama_mcp_bridge.config import load_config
from llama_mcp_bridge.gguf_header import parse_gguf_header, recommend_gguf_tuning
from llama_mcp_bridge.hardware import SystemProfile, detect_hardware
from llama_mcp_bridge.installer import _download_spec_list, _download_tier_models
from llama_mcp_bridge.logging_utils import configure_logging
from llama_mcp_bridge.ollama_import import import_ollama_models
from llama_mcp_bridge.presets import (
    _VALID_PRESETS,
    get_tier_model_specs,
    merge_custom_preset_ini,
    merge_models_ini,
    prune_unreferenced_models,
    remove_models_from_ini,
    resolve_hf_gguf_repo,
)

__all__ = [
    "_configured_presets",
    "_handle_custom_add",
    "_preset_total_size_gb",
    "_print_models_list",
    "_read_key",
    "main",
    "select_presets_interactive",
]

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

        raw = msvcrt.getch()
        if raw in (b"\x00", b"\xe0", "\x00", "\xe0"):
            nxt = msvcrt.getch()
            code = nxt.decode("latin-1") if isinstance(nxt, bytes) else nxt
            return {"H": "UP", "P": "DOWN"}.get(code, "")
        return raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else raw
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

    preset_sizes = {name: _preset_total_size_gb(profile.tier, name) for name in _VALID_PRESETS}

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


def _configured_presets(models_ini: Path, include_custom: bool = False) -> set[str]:
    if not models_ini.is_file():
        return set()
    cp = configparser.ConfigParser()
    cp.read(models_ini, encoding="utf-8")
    if include_custom:
        return {s for s in cp.sections() if s != "*"}
    return {s for s in cp.sections() if s in _VALID_PRESETS}


def _preset_total_size_gb(tier: str, preset: str) -> float:
    return sum(s["size_gb"] for s in get_tier_model_specs(tier, presets=[preset]))


def _print_models_list(profile: SystemProfile, models_ini: Path, models_dir: Path) -> None:
    console = Console()
    configured = _configured_presets(models_ini, include_custom=True)
    table = Table(title=f"llama-mcp-models (tier={profile.tier})")
    table.add_column("Preset", style="bold")
    table.add_column("Configured (models.ini)")
    table.add_column("GGUF On Disk")
    table.add_column("Size")
    table.add_column("Filename")
    for name in _VALID_PRESETS:
        p_specs = get_tier_model_specs(profile.tier, presets=[name])
        base_specs = [s for s in p_specs if not s["role"].endswith("-mmproj")]
        mmproj_specs = [s for s in p_specs if s["role"].endswith("-mmproj")]
        base_fname = base_specs[0]["filename"] if base_specs else "-"
        fname = f"{base_fname} (+ {mmproj_specs[0]['filename']})" if mmproj_specs else base_fname
        on_disk = bool(p_specs) and all((models_dir / s["filename"]).is_file() for s in p_specs)
        cfg_badge = "[green]YES[/green]" if name in configured else "[dim]NO[/dim]"
        disk_badge = "[green]YES[/green]" if on_disk else "[dim]NO[/dim]"
        size_str = f"{_preset_total_size_gb(profile.tier, name):.1f} GB" if p_specs else "-"
        table.add_row(name, cfg_badge, disk_badge, size_str, fname)
    custom_names = sorted(configured - set(_VALID_PRESETS))
    if custom_names and models_ini.is_file():
        cp = configparser.ConfigParser()
        cp.read(models_ini, encoding="utf-8")
        for cname in custom_names:
            mpath = Path(cp.get(cname, "model", fallback=""))
            mmpath = Path(cp.get(cname, "mmproj", fallback="")) if cp.has_option(cname, "mmproj") else None
            fname = f"{mpath.name} (+ {mmpath.name})" if mmpath else (mpath.name or "-")
            on_disk = mpath.is_file() and (mmpath is None or mmpath.is_file())
            disk_badge = "[green]YES[/green]" if on_disk else "[dim]NO[/dim]"
            table.add_row(f"{cname} [dim](custom)[/dim]", "[green]YES[/green]", disk_badge, "-", fname)
    console.print(table)


def _handle_custom_add(
    preset_name: str,
    hf_spec: str | None,
    file_path: str | None,
    mmproj_hint: str | None,
    profile: SystemProfile,
    residency: str,
    models_ini: Path,
    models_dir: Path,
    dry_run: bool,
    tune_mode: str = "conservative",
) -> None:
    """Resolve and add an unconstrained Hugging Face (--hf) or local (--file) GGUF model."""
    if hf_spec:
        repo_id, model_file, mmproj_file = resolve_hf_gguf_repo(
            hf_spec, mmproj_hint=mmproj_hint, tier=profile.tier
        )
        specs = [
            {
                "role": preset_name,
                "filename": Path(model_file).name,
                "url": f"https://huggingface.co/{repo_id}/resolve/main/{model_file}",
                "size_gb": 0,
            }
        ]
        if mmproj_file:
            specs.append(
                {
                    "role": f"{preset_name}-mmproj",
                    "filename": Path(mmproj_file).name,
                    "url": f"https://huggingface.co/{repo_id}/resolve/main/{mmproj_file}",
                    "size_gb": 0,
                }
            )
        _download_spec_list(specs, models_dir, dry_run=dry_run)
        model_dest = models_dir / Path(model_file).name
        mmproj_dest = (models_dir / Path(mmproj_file).name) if mmproj_file else None
    else:
        src = Path(file_path or "").expanduser().resolve()
        if not src.is_file():
            raise FileNotFoundError(f"Local GGUF file not found: {src}")
        models_dir.mkdir(parents=True, exist_ok=True)
        model_dest = models_dir / src.name
        if not dry_run and src != model_dest.resolve():
            shutil.copy2(src, model_dest)
        mmproj_dest = None
        if mmproj_hint:
            mm_src = Path(mmproj_hint).expanduser().resolve()
            if not mm_src.is_file():
                raise FileNotFoundError(f"Local mmproj file not found: {mm_src}")
            mmproj_dest = models_dir / mm_src.name
            if not dry_run and mm_src != mmproj_dest.resolve():
                shutil.copy2(mm_src, mmproj_dest)

    if dry_run:
        print(f"[DRY-RUN] Would add custom preset '{preset_name}' ({model_dest}) to {models_ini}")
        return

    rec_ctx: int | None = None
    rec_ngl: int | None = None
    try:
        meta = parse_gguf_header(model_dest)
        rec = recommend_gguf_tuning(meta, profile, tune_mode=tune_mode)
        rec_ctx = rec.ctx_size
        rec_ngl = rec.n_gpu_layers
        kv_count = sum(1 for h in meta.head_count_kv if h > 0)
        ssm_count = len(meta.head_count_kv) - kv_count
        swa_count = sum(1 for s in meta.swa_layers if s)
        print(
            f"GGUF Header ({meta.architecture}): {meta.n_layers} layers "
            f"(KV={kv_count}, SWA={swa_count}, SSM={ssm_count}), max_ctx={meta.max_context_length}"
        )
        print(
            f"Auto-Tune ({rec.tune_mode}): n-gpu-layers={rec.n_gpu_layers}, ctx-size={rec.ctx_size} "
            f"(est. KV={rec.kv_bytes_at_ctx / (1024**2):.1f} MiB) — {rec.notes}"
        )
    except (ValueError, OSError):
        pass

    merge_custom_preset_ini(
        models_ini,
        profile,
        residency,
        models_dir,
        preset_name,
        model_dest,
        mmproj_dest,
        ctx_size=rec_ctx,
        n_gpu_layers=rec_ngl,
    )
    if rec_ctx is not None:
        print(
            f"Disclaimer: Heuristic auto-tuning for '{preset_name}' is an estimate; "
            f"manual adjustment in {models_ini} may still be needed."
        )
    else:
        print(
            f"Notice: Added uncurated model '{preset_name}' with a generic baseline models.ini entry. "
            f"Manual tuning (ctx-size, KV cache quantisation, GPU layers) in {models_ini} is up to you."
        )
    if preset_name not in _VALID_PRESETS:
        print("Reminder: Restart active MCP clients (Claude Code, Cursor, Cline, Antigravity) so tools/list refreshes.")


def main() -> None:
    """Entry point for llama-mcp-models CLI."""
    parser = argparse.ArgumentParser(description="Manage configured presets and GGUF models.")
    parser.add_argument(
        "--version",
        action="version",
        version=f"llama-mcp-models {importlib.metadata.version('llama-mcp-bridge')}",
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
        default="text",
        help="Logging output format.",
    )
    parser.add_argument("action", nargs="?", choices=["list", "add", "remove"], help="Action to run.")
    parser.add_argument("presets", nargs="*", help="Preset names for add/remove.")
    parser.add_argument("--dir", "--home", dest="workspace_dir", type=str, help="Explicit workspace directory.")
    parser.add_argument("--add", dest="add_flag", nargs="+", metavar="PRESET", help="Add presets.")
    parser.add_argument("--remove", dest="remove_flag", nargs="+", metavar="PRESET", help="Remove presets.")
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--hf", type=str, metavar="REPO[:QUANT]", help="Add from any Hugging Face repo.")
    source_group.add_argument("--file", type=str, metavar="PATH", help="Add from a local .gguf file.")
    parser.add_argument("--mmproj", type=str, metavar="PATH_OR_NAME", help="Optional mmproj filename or path.")
    parser.add_argument(
        "--tune-mode",
        choices=["conservative", "approximate"],
        default="conservative",
        help="Heuristic auto-tuning mode for custom GGUF models.",
    )
    parser.add_argument(
        "--tool-name",
        type=str,
        metavar="NAME",
        help="Custom MCP tool/section name (e.g. qwen_coder -> [qwen_coder] / ask_qwen_coder).",
    )
    parser.add_argument("--import-ollama", action="store_true", help="Import Ollama GGUF blobs via symlinks.")
    parser.add_argument("--ollama-dir", type=str, help="Override Ollama models directory (default: ~/.ollama/models).")
    parser.add_argument("--prune", action="store_true", help="Remove unreferenced .gguf files in models_dir.")
    parser.add_argument("-y", "--yes", action="store_true", help="Confirm destructive removals.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without modifying files.")
    args = parser.parse_args()
    configure_logging(args.log_level, args.log_format)

    cfg = load_config(explicit_dir=args.workspace_dir)
    profile = detect_hardware()
    models_ini = Path(cfg.models_ini)
    models_dir = Path(cfg.models_dir)

    add_targets = args.add_flag or (args.presets if args.action == "add" else None)
    remove_targets = args.remove_flag or (args.presets if args.action == "remove" else None)

    if args.import_ollama:
        ollama_root = Path(args.ollama_dir).expanduser() if args.ollama_dir else None
        report = import_ollama_models(
            ollama_dir=ollama_root,
            models_dir=models_dir,
            models_ini=models_ini,
            profile=profile,
            residency=cfg.residency,
            dry_run=args.dry_run,
            tune_mode=args.tune_mode,
        )
        for warn in report.warnings:
            print(f"WARNING: {warn}")
        for entry in report.imported:
            prefix = "[DRY-RUN] Would import" if args.dry_run else "Imported"
            print(f"{prefix} {entry.preset_name} -> {entry.symlink_path} (ctx={entry.ctx_size}, ngl={entry.n_gpu_layers})")
        if not report.imported:
            print("No Ollama GGUF models imported.")
        return

    if args.prune:
        candidates = prune_unreferenced_models(models_ini, models_dir, dry_run=True)
        if not candidates:
            print("No unreferenced .gguf files to prune.")
            return
        if args.dry_run:
            for fname in candidates:
                print(f"[DRY-RUN] Would prune unreferenced file: {models_dir / fname}")
            return
        if not args.yes and (not sys.stdin.isatty() or not Confirm.ask(f"Delete {len(candidates)} unreferenced GGUF file(s)?", default=False)):
            print("Prune cancelled.")
            return
        deleted = prune_unreferenced_models(models_ini, models_dir, dry_run=False)
        print(f"Pruned {len(deleted)} unreferenced file(s): {', '.join(deleted)}")
        return

    if args.action == "list":
        _print_models_list(profile, models_ini, models_dir)
        return
    if args.hf or args.file:
        if args.tool_name:
            norm_tool = args.tool_name.strip().replace("-", "_")
            norm_tool = norm_tool.removeprefix("ask_")
            target_preset = norm_tool
        elif add_targets and len(add_targets) == 1:
            target_preset = add_targets[0]
        else:
            parser.error("--hf and --file require a single preset name or --tool-name.")
            return
        _handle_custom_add(
            target_preset,
            args.hf,
            args.file,
            args.mmproj,
            profile,
            cfg.residency,
            models_ini,
            models_dir,
            args.dry_run,
            tune_mode=args.tune_mode,
        )
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
        if any(p not in _VALID_PRESETS for p in remove_targets):
            print("Reminder: Restart active MCP clients (Claude Code, Cursor, Cline, Antigravity) so tools/list refreshes.")
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
