"""Cross-platform installer CLI for llama-mcp-bridge."""

import argparse
import configparser
import importlib.metadata
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress
from rich.prompt import Confirm, Prompt
from rich.table import Table

from llama_mcp_bridge.client_config import (
    _HARNESS_GUIDES,
    _build_mcp_snippet,
    _cleanup_harness_mcp_configs,
    _configure_client,
    _resolve_bridge_command,
    _resolve_harness_config_paths,
)
from llama_mcp_bridge.config import get_config_home, load_config
from llama_mcp_bridge.diagnostics import (
    check_for_update,
    diff_config_against_defaults,
    export_merged_config,
    get_model_info,
    run_profile_benchmark,
    run_selftest,
)
from llama_mcp_bridge.hardware import detect_hardware
from llama_mcp_bridge.model_download import (
    _compute_sha256,
    _download_spec_list,
    _download_tier_models,
    _verify_and_finalize_part,
)
from llama_mcp_bridge.ollama_import import import_ollama_models
from llama_mcp_bridge.paths import (
    get_default_workspace,
    register_global_workspace,
    resolve_workspace,
    unregister_global_workspace,
)
from llama_mcp_bridge.presets import (
    _VALID_PRESETS,
    TIER_MODEL_REGISTRY,
    generate_models_ini,
    get_tier_model_specs,
    probe_model_metadata,
    search_hf_gguf_models,
    verify_tier_models,
)
from llama_mcp_bridge.service_install import (
    _auto_install_llama_server,
    _check_hardware_prerequisites,
    _extract_archive,
    _find_llama_server,
    _flatten_extracted_dir,
    _generate_service_file,
    _install_reboot_crontab,
    _macos_release_asset,
    _persist_server_bin,
    _select_cudart_release_asset,
    _select_llama_release_asset,
    _uninstall_bridge,
)
from llama_mcp_bridge.skill_install import (
    _install_skill,
    _resolve_bundled_skill_path,
    _resolve_skill_destinations,
)
from llama_mcp_bridge.supervisor import LlamaSupervisor

__all__ = [
    "_HARNESS_GUIDES",
    "Progress",
    "_auto_install_llama_server",
    "_build_mcp_snippet",
    "_check_hardware_prerequisites",
    "_cleanup_harness_mcp_configs",
    "_compute_sha256",
    "_configure_client",
    "_download_spec_list",
    "_download_tier_models",
    "_extract_archive",
    "_find_llama_server",
    "_flatten_extracted_dir",
    "_generate_service_file",
    "_install_reboot_crontab",
    "_install_skill",
    "_macos_release_asset",
    "_persist_server_bin",
    "_resolve_bridge_command",
    "_resolve_bundled_skill_path",
    "_resolve_harness_config_paths",
    "_resolve_skill_destinations",
    "_rewrite_models_ini_dir",
    "_run_doctor",
    "_run_tui_wizard",
    "_select_cudart_release_asset",
    "_select_llama_release_asset",
    "_uninstall_bridge",
    "_verify_and_finalize_part",
    "detect_hardware",
    "main",
    "probe_model_metadata",
    "ps_main",
    "search_hf_gguf_models",
    "shutil",
    "urllib",
]


def _free_disk_gb(path: Path) -> float | None:
    """Return free disk space in GiB for path or its nearest existing ancestor."""
    cur = path
    while not cur.exists() and cur.parent != cur:
        cur = cur.parent
    try:
        return shutil.disk_usage(cur).free / (1024**3)
    except OSError:
        return None


def ps_main(json_output: bool = False) -> None:
    """Render an Ollama-style live status table (or JSON) for server health and loaded presets."""
    if not json_output and "--json" in sys.argv[1:]:
        json_output = True
    console = Console()
    cfg = load_config()
    env_active = bool(os.environ.get("LLAMA_MCP_HOME"))
    status = LlamaSupervisor(cfg).get_status()
    loaded_map = status.get("models", {})
    preset_list = status.get("configured_presets") or list(loaded_map.keys()) or list(_VALID_PRESETS)

    if json_output:
        payload = {
            "workspace": cfg.workspace,
            "llama_mcp_home_active": env_active,
            "host": cfg.host,
            "port": cfg.port,
            "healthy": bool(status["healthy"]),
            "auto_spawn": cfg.auto_spawn,
            "residency": status.get("residency", cfg.residency),
            "tier": status["tier"],
            "presets": {name: loaded_map.get(name, "idle") for name in preset_list},
        }
        print(json.dumps(payload, indent=2))
        return

    if status["healthy"]:
        state_label = "[green]ONLINE[/green]"
    elif cfg.auto_spawn:
        state_label = "[cyan]STANDBY (auto-spawn)[/cyan]"
    else:
        state_label = "[red]OFFLINE[/red]"
    home_badge = "LLAMA_MCP_HOME=active" if env_active else "LLAMA_MCP_HOME=unset"
    console.print(
        f"Workspace: [bold]{cfg.workspace}[/bold] ({home_badge})\n"
        f"Server: [bold]{cfg.host}:{cfg.port}[/bold] | State: {state_label} | "
        f"Residency: [cyan]{status.get('residency', cfg.residency)}[/cyan] | "
        f"Tier: [cyan]{status['tier']}[/cyan]"
    )
    table = Table(title="Specialist Presets")
    table.add_column("Preset", style="bold")
    table.add_column("Status")
    for name in preset_list:
        st = loaded_map.get(name, "idle")
        badge = "[green]LOADED[/green]" if st == "loaded" else "[dim]IDLE[/dim]"
        table.add_row(name, badge)
    console.print(table)


def _run_doctor(json_output: bool = False, verify_sha: bool = False) -> int:
    """Run an end-to-end diagnostic check and return exit code (0=ok/warn, 1=fail)."""
    from llama_mcp_bridge.bridge import _BUILTIN_TOOL_MAP

    console = Console()
    cfg = load_config()
    workspace = Path(cfg.workspace)
    env_active = bool(os.environ.get("LLAMA_MCP_HOME"))
    table = Table(title="llama-mcp-bridge Doctor")
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Details")

    checks: list[dict[str, str]] = []
    failed = False

    def _record(name: str, state: str, details: str) -> None:
        checks.append({"check": name, "status": state, "details": details})
        badge = {"PASS": "[green]PASS[/green]", "WARN": "[yellow]WARN[/yellow]", "FAIL": "[red]FAIL[/red]"}[state]
        table.add_row(name, badge, details)

    _record(
        "Workspace",
        "PASS",
        f"{workspace} (LLAMA_MCP_HOME={'active' if env_active else 'unset'})",
    )

    bridge_ini = workspace / "bridge.ini"
    models_ini = Path(cfg.models_ini)
    ini_ok = True
    for p in (bridge_ini, models_ini):
        if not p.is_file():
            ini_ok = False
        else:
            try:
                cp = configparser.ConfigParser()
                cp.read(p, encoding="utf-8")
            except configparser.Error:
                ini_ok = False
    if ini_ok:
        _record("Config files", "PASS", f"{bridge_ini} & {models_ini}")
    else:
        failed = True
        _record("Config files", "FAIL", "Missing or invalid bridge.ini / models.ini")

    srv_bin = _find_llama_server(workspace, cfg.llama_server_bin)
    if srv_bin:
        try:
            ver_res = subprocess.run([str(srv_bin), "--version"], capture_output=True, text=True, timeout=5, check=False)
            if ver_res.returncode == 0:
                _record("llama-server", "PASS", str(srv_bin))
            else:
                failed = True
                _record("llama-server", "FAIL", f"{srv_bin} exited {ver_res.returncode} on --version")
        except (OSError, subprocess.SubprocessError) as exc:
            failed = True
            _record("llama-server", "FAIL", f"{srv_bin} failed to execute: {exc}")
    else:
        failed = True
        _record("llama-server", "FAIL", "Binary not found or not executable")

    missing_models: list[str] = []
    unregistered_presets: list[str] = []
    preset_tools: list[str] = []
    if models_ini.is_file():
        cp = configparser.ConfigParser()
        cp.read(models_ini, encoding="utf-8")
        for sec in cp.sections():
            for key in ("model", "mmproj"):
                if cp.has_option(sec, key):
                    mp = Path(cp.get(sec, key))
                    if not mp.is_file() or mp.stat().st_size == 0:
                        missing_models.append(str(mp))
            if sec != "*":
                if sec in _BUILTIN_TOOL_MAP:
                    preset_tools.append(_BUILTIN_TOOL_MAP[sec].__name__)
                else:
                    safe_suffix = re.sub(r"[^a-zA-Z0-9_]", "_", sec).strip("_")
                    if safe_suffix:
                        preset_tools.append(f"ask_{safe_suffix}")
                    else:
                        unregistered_presets.append(sec)

    free_gb = _free_disk_gb(Path(cfg.models_dir))
    disk_note = f" ({free_gb:.1f} GB free)" if free_gb is not None else ""
    if missing_models:
        failed = True
        low_disk = " [low disk space <5 GB!]" if (free_gb is not None and free_gb < 5.0) else ""
        _record(
            "GGUF files",
            "FAIL",
            f"{len(missing_models)} missing{disk_note}.{low_disk} Run: llama-mcp-install --download-models --models-dir {cfg.models_dir}",
        )
    else:
        _record("GGUF files", "PASS", f"All referenced files present in {cfg.models_dir}{disk_note}")

    if verify_sha and models_ini.is_file():
        url_by_name = {
            spec["filename"]: spec["url"]
            for specs in TIER_MODEL_REGISTRY.values()
            for spec in specs
        }
        sha_mismatches: list[str] = []
        sha_checked = 0
        sha_skipped = 0
        seen_paths: set[Path] = set()
        cp = configparser.ConfigParser()
        cp.read(models_ini, encoding="utf-8")
        for sec in cp.sections():
            for key in ("model", "mmproj"):
                if cp.has_option(sec, key):
                    mp = Path(cp.get(sec, key))
                    if mp in seen_paths or not mp.is_file():
                        continue
                    seen_paths.add(mp)
                    url = url_by_name.get(mp.name)
                    if not url:
                        sha_skipped += 1
                        continue
                    ok, _, expected_sha = probe_model_metadata(url)
                    if not ok or not expected_sha:
                        sha_skipped += 1
                        continue
                    sha_checked += 1
                    actual_sha = _compute_sha256(mp)
                    if actual_sha.lower() != expected_sha.lower():
                        sha_mismatches.append(f"{mp.name} (expected {expected_sha[:12]}, got {actual_sha[:12]})")
        if sha_mismatches:
            failed = True
            _record("GGUF SHA-256", "FAIL", f"Mismatch: {', '.join(sha_mismatches)}")
        elif sha_checked > 0:
            _record("GGUF SHA-256", "PASS", f"Verified {sha_checked} file(s) ({sha_skipped} uncurated/no-etag skipped)")
        else:
            _record("GGUF SHA-256", "WARN", f"No curated HF SHA-256 headers available ({sha_skipped} skipped)")

    if unregistered_presets:
        failed = True
        _record("Preset tools", "FAIL", f"Unregistrable preset section(s): {', '.join(unregistered_presets)}")
    elif preset_tools:
        _record("Preset tools", "PASS", f"{len(preset_tools)} tool(s) mapped ({', '.join(preset_tools[:4])}{'...' if len(preset_tools) > 4 else ''})")
    else:
        _record("Preset tools", "WARN", "No specialist presets configured in models.ini")

    status = LlamaSupervisor(cfg).get_status()
    if status["healthy"]:
        _record("Server /health", "PASS", f"Reachable at {status['server_url']}")
    elif cfg.auto_spawn:
        _record("Server /health", "PASS", "Standby — will auto-spawn on first tool call")
    else:
        _record("Server /health", "WARN", "Stopped (auto_spawn=false)")

    if json_output:
        print(
            json.dumps(
                {
                    "workspace": str(workspace),
                    "llama_mcp_home_active": env_active,
                    "passed": not failed,
                    "checks": checks,
                },
                indent=2,
            )
        )
    else:
        console.print(table)
    return 1 if failed else 0


def _run_tui_wizard() -> None:
    """Interactive rich-powered setup wizard for llama-mcp-bridge."""
    console = Console()
    profile = detect_hardware()
    default_home = get_config_home()
    cfg = load_config()
    found_bin = _find_llama_server(default_home, cfg.llama_server_bin)

    console.print(Panel.fit("[bold cyan]llama-mcp-bridge Setup Wizard[/bold cyan]", border_style="cyan"))

    hw_table = Table(title="Step 1: Hardware & BYO Prerequisites")
    hw_table.add_column("Property", style="bold")
    hw_table.add_column("Detected Value")
    hw_table.add_row("OS / Arch", f"{profile.os_name} / {profile.arch}")
    hw_table.add_row("GPU / VRAM", f"{profile.gpu_vendor} ({profile.vram_gb} GB, tier={profile.tier})")
    hw_table.add_row("llama-server", str(found_bin) if found_bin else "[yellow]Not detected[/yellow]")
    console.print(hw_table)
    _check_hardware_prerequisites(profile, "prebuilt")

    ws_str = Prompt.ask("\nStep 2: Workspace root", default=str(default_home))
    workspace = Path(ws_str).expanduser()
    models_str = Prompt.ask("Models directory", default=str(workspace / "models"))
    models_dir = Path(models_str).expanduser()
    found_bin = _find_llama_server(workspace, cfg.llama_server_bin)

    residency = Prompt.ask(
        "\nStep 3: Memory residency profile",
        choices=["stingy", "moderate", "snappy"],
        default="moderate",
    )

    default_presets = ["digest"] if profile.gpu_vendor == "cpu" else list(_VALID_PRESETS)
    from llama_mcp_bridge.models_cli import select_presets_interactive

    selected_presets = select_presets_interactive(
        profile,
        initial_selected=default_presets,
        title="Step 4: Select Specialist Presets",
    )

    mode = "skip"
    if not found_bin:
        mode = Prompt.ask(
            "\nStep 5: llama-server install mode",
            choices=["prebuilt", "system-bin", "source-hermetic", "source-system", "skip"],
            default="prebuilt",
        )

    install_svc = Confirm.ask("\nStep 6: Register background service?", default=False)

    summary = Table(title="Step 7: Planned Configuration")
    summary.add_column("Setting", style="bold")
    summary.add_column("Value")
    summary.add_row("Workspace", str(workspace))
    summary.add_row("Models Dir", str(models_dir))
    summary.add_row("Residency", residency)
    summary.add_row("llama-server", mode if not found_bin else str(found_bin))
    summary.add_row("Install Service", "Yes" if install_svc else "No")
    console.print(summary)

    if not Confirm.ask("Proceed with installation?", default=True):
        console.print("[yellow]Aborted.[/yellow]")
        return

    prev_llama_mcp_home = os.environ.get("LLAMA_MCP_HOME")
    os.environ["LLAMA_MCP_HOME"] = str(workspace)
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        models_ini_path = workspace / "models.ini"
        bridge_ini = workspace / "bridge.ini"
        had_models_ini = models_ini_path.exists()
        had_bridge_ini = bridge_ini.exists()

        if (
            workspace.expanduser().resolve() != get_default_workspace(sys.platform).expanduser().resolve()
            and Confirm.ask("Register this custom workspace globally?", default=True)
        ):
            ptr = register_global_workspace(workspace)
            console.print(f"[green]Registered global workspace pointer:[/green] {ptr}")
        ini_content = generate_models_ini(
            profile,
            models_dir=str(models_dir),
            residency=residency,
            selected_presets=selected_presets,
        )
        models_ini_path.write_text(ini_content, encoding="utf-8")

        specs = get_tier_model_specs(profile.tier, presets=selected_presets or None)
        total_gb = sum(s["size_gb"] for s in specs)
        if selected_presets and Confirm.ask(
            f"Download GGUF models for your selected presets now (~{total_gb:.1f} GB)?",
            default=False,  # type: ignore[arg-type]
        ):
            _download_tier_models(profile, models_dir, dry_run=False, presets=selected_presets)

        if not had_bridge_ini:
            bridge_ini.write_text(
                f"[bridge]\nworkspace = {workspace}\nmodels_dir = {models_dir}\n"
                f"models_ini = {models_ini_path}\nresidency = {residency}\n",
                encoding="utf-8",
            )

        if not found_bin and mode != "skip":
            _auto_install_llama_server(profile, mode, workspace, dry_run=False, yes=True)

        if install_svc:
            _generate_service_file(dry_run=False)

        changes = [
            f"models.ini: {'updated' if had_models_ini else 'created'} ({models_ini_path})",
            f"bridge.ini: {'preserved existing' if had_bridge_ini else 'created'} ({bridge_ini})",
            f"llama-server: {'installed (' + mode + ')' if (not found_bin and mode != 'skip') else 'preserved/skipped'}",
        ]
        console.print("[bold]Wizard Change Summary:[/bold]")
        for item in changes:
            console.print(f"  - {item}")

        mcp_snippet = _build_mcp_snippet(workspace)
        console.print(Panel(mcp_snippet, title="Add to mcp_config.json", border_style="green"))
        gguf_lines = [ln.strip() for ln in ini_content.splitlines() if ln.strip().startswith("model = ")]
        if gguf_lines:
            console.print("[bold]Required GGUF model paths:[/bold]")
            for ln in gguf_lines:
                console.print(f"  {ln}")
        if Confirm.ask("Show MCP client setup snippet for a specific harness?", default=False):
            _configure_client("prompt")
        if Confirm.ask("Install the llama-mcp-setup agent skill into detected harnesses?", default=False):
            _install_skill("auto", dry_run=False)

        print("Manage, add, or remove models anytime with: llama-mcp-models")
    finally:
        if prev_llama_mcp_home is None:
            os.environ.pop("LLAMA_MCP_HOME", None)
        else:
            os.environ["LLAMA_MCP_HOME"] = prev_llama_mcp_home


def _rewrite_models_ini_dir(models_ini: Path, new_models_dir: Path) -> None:
    """Rewrite model/mmproj paths in an existing models.ini to point at new_models_dir."""
    if not models_ini.is_file():
        return
    cp = configparser.ConfigParser()
    cp.read(models_ini, encoding="utf-8")
    for sec in cp.sections():
        for key in ("model", "mmproj"):
            if cp.has_option(sec, key):
                cp.set(sec, key, str(new_models_dir / Path(cp.get(sec, key)).name))
    lines: list[str] = []
    for sec in cp.sections():
        lines.append(f"[{sec}]")
        for k, v in cp.items(sec):
            lines.append(f"{k} = {v}")
        lines.append("")
    models_ini.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    """Main entry point for the installer CLI."""
    parser = argparse.ArgumentParser(
        description="Install and configure llama-mcp-bridge.",
        epilog="Manage, add, or remove models anytime with: llama-mcp-models",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"llama-mcp-install {importlib.metadata.version('llama-mcp-bridge')}",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="WARNING",
        help="Logging verbosity level.",
    )
    parser.add_argument(
        "--mode",
        choices=["prebuilt", "source-hermetic", "source-system", "system-bin"],
        default="prebuilt",
        help="Installation mode.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print planned actions without executing.")
    parser.add_argument("--install-service", action="store_true", help="Generate service unit file.")
    parser.add_argument("-y", "--yes", action="store_true", help="Auto-confirm llama-server installation.")
    parser.add_argument(
        "--download-models",
        nargs="*",
        default=None,
        metavar="PRESET",
        help="Download curated GGUF models for tier (optionally filter by preset names).",
    )
    parser.add_argument("--verify-models", action="store_true", help="Verify Hugging Face model URLs.")
    parser.add_argument("--verify-all", action="store_true", help="Run both --verify-models and --doctor.")
    parser.add_argument("--no-cache", action="store_true", help="Bypass the 1-hour --verify-models TTL cache.")
    parser.add_argument("--max-workers", type=int, default=8, help="Max concurrent workers for --verify-models.")
    parser.add_argument("--tier", type=str, help="Target VRAM tier for --verify-models.")
    parser.add_argument(
        "--residency",
        choices=["stingy", "moderate", "snappy"],
        default="moderate",
        help="Memory residency profile (stingy, moderate, snappy).",
    )
    parser.add_argument("--ps", action="store_true", help="Show live server and preset status.")
    parser.add_argument("--doctor", action="store_true", help="Run end-to-end diagnostic checklist.")
    parser.add_argument("--verify-sha", action="store_true", help="Re-verify on-disk GGUFs against HF SHA-256 in --doctor.")
    parser.add_argument("--json", action="store_true", help="Emit machine-parseable JSON output.")
    parser.add_argument("--profile", action="store_true", help="Benchmark configured presets against a live llama-server.")
    parser.add_argument("--selftest", action="store_true", help="Run live ask_digest('ping') end-to-end smoke test.")
    parser.add_argument("--check-update", action="store_true", help="Check Git tags for a newer release.")
    parser.add_argument("--export-config", action="store_true", help="Print effective merged [*] + [preset] models.ini.")
    parser.add_argument("--diff-config", action="store_true", help="Diff current models.ini against tier defaults.")
    parser.add_argument("--model-info", type=str, metavar="PRESET", help="Inspect effective flags and GGUF metadata for a preset.")
    parser.add_argument("--import-ollama", action="store_true", help="Import Ollama GGUF blobs via symlinks.")
    parser.add_argument("--ollama-dir", type=str, help="Override Ollama models directory (default: ~/.ollama/models).")
    parser.add_argument("--uninstall", action="store_true", help="Remove binaries, build dirs, and service while preserving models and INIs.")
    parser.add_argument(
        "--configure-client",
        nargs="?",
        const="prompt",
        choices=["prompt", "claude-code", "cursor", "cline", "windsurf", "zed", "antigravity", "other"],
        help="Print ready-to-paste MCP client JSON and official guide URL.",
    )
    parser.add_argument("--apply", action="store_true", help="Merge 'local-ai' into the target harness config for --configure-client.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing models.ini when re-running installer.")
    parser.add_argument("--tui", action="store_true", help="Launch interactive rich TUI wizard.")
    parser.add_argument("--models-dir", type=str, help="Path to models directory.")
    parser.add_argument("--dir", "--home", dest="workspace_dir", type=str, help="Explicit workspace directory.")
    parser.add_argument("--register", action="store_true", help="Register workspace globally as active home.")
    parser.add_argument("--unregister", action="store_true", help="Clear the global workspace registration pointer.")
    parser.add_argument(
        "--install-skill",
        nargs="?",
        const="auto",
        default=None,
        metavar="TARGET",
        help="Install llama-mcp-setup SKILL.md (auto, claude, antigravity, gemini, cursor, workspace, or path).",
    )
    parser.add_argument("--skill-link", action="store_true", help="Symlink SKILL.md instead of copying.")
    parser.add_argument(
        "--presets",
        nargs="+",
        metavar="PRESET",
        help="Only emit selected presets in models.ini.",
    )

    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if args.workspace_dir:
        os.environ["LLAMA_MCP_HOME"] = str(resolve_workspace(args.workspace_dir))
    if args.unregister:
        if args.dry_run:
            print("[DRY-RUN] Would clear global workspace pointer.")
        else:
            removed = unregister_global_workspace()
            print("Cleared global workspace pointer." if removed else "No global workspace pointer was set.")
        return
    if args.uninstall:
        _uninstall_bridge(dry_run=args.dry_run, yes=args.yes)
        return
    if args.ps:
        ps_main(json_output=args.json)
        return
    if args.doctor:
        sys.exit(_run_doctor(json_output=args.json, verify_sha=args.verify_sha))
    if args.selftest:
        sys.exit(run_selftest(json_output=args.json))
    if args.profile:
        sys.exit(run_profile_benchmark(load_config(), json_output=args.json))
    if args.check_update:
        current, latest, has_update = check_for_update()
        if args.json:
            print(json.dumps({"current": current, "latest": latest, "update_available": has_update}, indent=2))
        elif has_update:
            print(f"Update available: {current} -> {latest}")
        else:
            print(f"Up to date ({current}).")
        return
    if args.export_config:
        cfg = load_config()
        print(export_merged_config(Path(cfg.models_ini)))
        return
    if args.diff_config:
        cfg = load_config()
        diff_text = diff_config_against_defaults(
            Path(cfg.models_ini), detect_hardware(), cfg.models_dir, cfg.residency
        )
        print(diff_text or "No differences from tier defaults.")
        return
    if args.model_info:
        cfg = load_config()
        info = get_model_info(Path(cfg.models_ini), args.model_info)
        if args.json:
            print(json.dumps(info, indent=2))
        else:
            console = Console()
            table = Table(title=f"Model Info: {args.model_info}")
            table.add_column("Flag", style="bold")
            table.add_column("Value")
            for k, v in info["flags"].items():
                table.add_row(k, str(v))
            console.print(table)
            if info["gguf"]:
                meta = info["gguf"]
                print(
                    f"GGUF: arch={meta['architecture']} layers={meta['n_layers']} "
                    f"max_ctx={meta['max_context_length']} heads_kv={meta['head_count_kv']}"
                )
        return
    if args.import_ollama:
        cfg = load_config()
        ollama_root = Path(args.ollama_dir).expanduser() if args.ollama_dir else None
        report = import_ollama_models(
            ollama_dir=ollama_root,
            models_dir=Path(cfg.models_dir),
            models_ini=Path(cfg.models_ini),
            profile=detect_hardware(),
            residency=cfg.residency,
            dry_run=args.dry_run,
        )
        for warn in report.warnings:
            print(f"WARNING: {warn}")
        for entry in report.imported:
            prefix = "[DRY-RUN] Would import" if args.dry_run else "Imported"
            print(f"{prefix} {entry.preset_name} -> {entry.symlink_path} (ctx={entry.ctx_size}, ngl={entry.n_gpu_layers})")
        if not report.imported:
            print("No Ollama GGUF models imported.")
        return
    if args.configure_client:
        _configure_client(args.configure_client, apply=args.apply, dry_run=args.dry_run)
        return
    if args.install_skill is not None:
        _install_skill(args.install_skill, symlink=args.skill_link, dry_run=args.dry_run)
        return
    if args.verify_models or args.verify_all:
        verify_failed = False
        if args.dry_run:
            target_tier = args.tier or "all"
            print(f"[DRY-RUN] Would verify Hugging Face model URLs for tier={target_tier} (max_workers={args.max_workers}).")
        else:
            cfg = load_config()
            cache_path = Path(cfg.workspace) / ".cache" / "verify_models.json"
            console = Console()
            results = verify_tier_models(
                args.tier,
                max_workers=args.max_workers,
                cache_path=cache_path,
                use_cache=not args.no_cache,
            )
            table = Table(title="Hugging Face Model Verification")
            table.add_column("Tier")
            table.add_column("Filename")
            table.add_column("Status")
            table.add_column("Details")
            for item in results:
                if item["ok"]:
                    size_mb = f"{item['size_bytes'] / (1024**2):.1f} MB" if item["size_bytes"] else "OK"
                    table.add_row(item["tier"], item["filename"], "[green]OK[/green]", size_mb)
                else:
                    verify_failed = True
                    sugg = ", ".join(s["id"] for s in item["suggestions"]) or "none"
                    table.add_row(item["tier"], item["filename"], "[red]MISSING[/red]", f"Try: {sugg}")
            console.print(table)
        if args.verify_all:
            doc_rc = 0 if args.dry_run else _run_doctor(json_output=args.json, verify_sha=args.verify_sha)
            sys.exit(1 if (verify_failed or doc_rc != 0) else 0)
        if verify_failed:
            sys.exit(1)
        return

    if args.tui or (len(sys.argv) == 1 and sys.stdin.isatty()):
        _run_tui_wizard()
        return
    home = get_config_home()
    cfg = load_config()
    models_dir = Path(args.models_dir) if args.models_dir else home / "models"

    profile = detect_hardware()
    ini_content = generate_models_ini(
        profile,
        models_dir=str(models_dir),
        residency=args.residency,
        selected_presets=args.presets,
    )

    print(f"Mode: {args.mode}")
    print(f"Hardware: {profile.os_name}/{profile.arch} {profile.gpu_vendor} ({profile.vram_gb} GB, tier={profile.tier})")
    print(f"Models Dir: {models_dir}")

    _check_hardware_prerequisites(profile, args.mode)

    if not _find_llama_server(home, cfg.llama_server_bin):
        _auto_install_llama_server(profile, args.mode, home, args.dry_run, args.yes)

    if args.install_service:
        _generate_service_file(args.dry_run)

    if args.download_models is not None and (
        args.dry_run
        or args.yes
        or (
            sys.stdin.isatty()
            and input(f"Download curated GGUF models into {models_dir}? [y/N]: ").strip().lower() in ("y", "yes")
        )
    ):
        _download_tier_models(profile, models_dir, dry_run=args.dry_run, presets=args.download_models)

    if args.dry_run:
        print(f"[DRY-RUN] Would write models.ini ({len(ini_content)} bytes) to {home / 'models.ini'}")
        return

    home.mkdir(parents=True, exist_ok=True)
    if args.register:
        ptr = register_global_workspace(home)
        print(f"Registered global workspace pointer at {ptr} -> {home}")
    models_ini_path = home / "models.ini"
    preserve_existing = (
        models_ini_path.exists()
        and args.install_service
        and args.presets is None
        and not args.force
    )
    if preserve_existing:
        if args.models_dir:
            _rewrite_models_ini_dir(models_ini_path, models_dir)
        print(f"Preserved existing {models_ini_path}")
    else:
        models_ini_path.write_text(ini_content, encoding="utf-8")
        print(f"Wrote {models_ini_path}")
    bridge_ini = home / "bridge.ini"
    if not bridge_ini.exists():
        bridge_ini.write_text(
            f"[bridge]\nworkspace = {home}\nmodels_dir = {models_dir}\n"
            f"models_ini = {home / 'models.ini'}\nresidency = {args.residency}\n",
            encoding="utf-8",
        )
    print("Manage, add, or remove models anytime with: llama-mcp-models")


if __name__ == "__main__":
    main()
