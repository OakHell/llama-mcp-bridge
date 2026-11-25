"""Cross-platform installer CLI for llama-mcp-bridge."""

import argparse
import configparser
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress
from rich.prompt import Confirm, Prompt
from rich.table import Table

from llama_mcp_bridge.config import get_config_home, load_config
from llama_mcp_bridge.hardware import SystemProfile, detect_hardware
from llama_mcp_bridge.presets import (
    _VALID_PRESETS,
    generate_models_ini,
    get_tier_model_specs,
    probe_model_metadata,
    search_hf_gguf_models,
    verify_tier_models,
)
from llama_mcp_bridge.supervisor import LlamaSupervisor


def _macos_release_asset(arch: str) -> str:
    """Map macOS architecture to llama.cpp release asset suffix."""
    return "macos-arm64" if arch.lower() in ("arm64", "aarch64") else "macos-x64"


def _extract_archive(archive_path: Path, dest_dir: Path) -> None:
    """Extract .zip (Windows) or .tar.gz/.tar.xz (POSIX) archives."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = archive_path.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(dest_dir)
    elif name.endswith((".tar.gz", ".tgz", ".tar.xz")):
        with tarfile.open(archive_path, "r:*") as tf:
            tf.extractall(dest_dir)
    else:
        raise ValueError(f"Unsupported archive format: {archive_path}")


def _check_hardware_prerequisites(profile: SystemProfile, mode: str) -> list[str]:
    """Check required runtime/build tools and print a guided prerequisite summary."""
    missing: list[str] = []
    print("\n--- Hardware Prerequisite Check (Drivers/SDKs are BYO) ---")
    if profile.gpu_vendor == "nvidia":
        if not shutil.which("nvidia-smi"):
            missing.append("nvidia-smi (install NVIDIA GPU drivers)")
        if mode in ("source-hermetic", "source-system") and not shutil.which("nvcc"):
            missing.append("nvcc (install CUDA Toolkit)")
    elif profile.gpu_vendor == "amd":
        if not shutil.which("rocminfo") and not shutil.which("vulkaninfo"):
            missing.append("rocminfo or vulkaninfo (install ROCm or Vulkan drivers)")
        if mode in ("source-hermetic", "source-system") and not shutil.which("hipcc"):
            missing.append("hipcc (install ROCm HIP SDK)")
    elif profile.gpu_vendor == "intel":
        if not shutil.which("vulkaninfo"):
            missing.append("vulkaninfo (install Intel Vulkan drivers/tools)")
    elif (
        profile.gpu_vendor == "apple"
        and mode in ("source-hermetic", "source-system")
        and not shutil.which("xcode-select")
    ):
        missing.append("xcode-select (run: xcode-select --install)")
    elif profile.gpu_vendor == "cpu":
        print(
            "NOTICE: No supported GPU detected; using lightweight 'cpu' (6gb) presets in system RAM. "
            ">= 16 GB system RAM is recommended. Vision and long-context tools will be slower than "
            "ask_digest and ask_coder_fast."
        )

    if mode in ("source-hermetic", "source-system") and not shutil.which("cmake"):
        missing.append("cmake (install CMake 3.21+)")

    if missing:
        print("WARNING: Missing prerequisites detected:")
        for item in missing:
            print(f"  - {item}")
    else:
        print("All detected hardware prerequisites are present.")
    return missing


def _find_llama_server(workspace: Path, configured_bin: str) -> Path | None:
    """Return path to llama-server if already installed, else None."""
    exe_name = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    if configured_bin and Path(configured_bin).is_file():
        return Path(configured_bin)
    local_bin = workspace / "bin" / exe_name
    if local_bin.is_file():
        return local_bin
    which_bin = shutil.which(exe_name)
    return Path(which_bin) if which_bin else None


def _persist_server_bin(workspace: Path, rel_bin: str) -> None:
    """Persist llama_server_bin in <workspace>/bridge.ini."""
    ini_path = workspace / "bridge.ini"
    parser = configparser.ConfigParser()
    if ini_path.exists():
        parser.read(ini_path, encoding="utf-8")
    if "bridge" not in parser:
        parser["bridge"] = {}
    parser["bridge"]["llama_server_bin"] = rel_bin
    with ini_path.open("w", encoding="utf-8") as fh:
        parser.write(fh)


def _select_llama_release_asset(
    assets: list[dict[str, Any]], profile: SystemProfile
) -> dict[str, Any] | None:
    """Select the best binary llama.cpp release asset for (os_name, arch, gpu_vendor)."""
    os_tok = "win" if profile.os_name == "windows" else ("macos" if profile.os_name == "darwin" else "ubuntu")
    is_arm = profile.arch.lower() in ("arm64", "aarch64")
    arch_toks = ("arm64", "aarch64") if is_arm else ("x64", "x86_64")
    bad_arch_toks = ("x64", "x86_64") if is_arm else ("arm64", "aarch64")

    candidates: list[tuple[int, dict[str, Any]]] = []
    for asset in assets:
        name = asset.get("name", "").lower()
        if not name.endswith((".zip", ".tar.gz", ".tgz")):
            continue
        if any(bad in name for bad in ("cudart", "sha256", "source")):
            continue
        if os_tok not in name:
            continue
        if any(bt in name for bt in bad_arch_toks):
            continue
        if not any(at in name for at in arch_toks) and profile.os_name != "darwin":
            continue

        score = 0
        if profile.gpu_vendor == "nvidia" and "cuda" in name or profile.gpu_vendor in ("amd", "intel") and "vulkan" in name:
            score += 10
        elif profile.os_name == "windows" and "vulkan" in name and profile.gpu_vendor != "nvidia":
            score += 5
        elif "cuda" not in name and "vulkan" not in name and "rocm" not in name:
            score += 2
        candidates.append((score, asset))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _auto_install_llama_server(
    profile: SystemProfile, mode: str, workspace: Path, dry_run: bool, yes: bool
) -> None:
    """Prompt and install llama-server into <workspace>/bin when undetected."""
    bin_dir = workspace / "bin"
    exe_name = "llama-server.exe" if profile.os_name == "windows" else "llama-server"

    if mode == "system-bin":
        pkg_hints = {
            "darwin": "brew install llama.cpp",
            "win32": "winget install ggml.llamacpp",
            "linux": "sudo pacman -S llama.cpp  # or your distro package manager",
        }
        hint = pkg_hints.get(sys.platform, "pkg install llama.cpp")
        print(f"Install llama-server via your system package manager:\n  {hint}")
        return

    if dry_run:
        print(f"[DRY-RUN] Would prompt to install llama-server ({mode}) into {bin_dir}")
        return

    if not yes:
        if not sys.stdin.isatty():
            print("Non-interactive session without --yes; skipping llama-server install.")
            return
        ans = input(f"Install llama-server automatically into {bin_dir}? [y/N]: ").strip().lower()
        if ans not in ("y", "yes"):
            print("Skipped llama-server installation.")
            return

    bin_dir.mkdir(parents=True, exist_ok=True)
    if mode == "prebuilt":
        api_url = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
        with urllib.request.urlopen(api_url, timeout=15) as resp:
            release = json.loads(resp.read().decode("utf-8"))
        match = _select_llama_release_asset(release.get("assets", []), profile)
        if not match:
            list_url = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=10"
            with urllib.request.urlopen(list_url, timeout=15) as resp:
                releases = json.loads(resp.read().decode("utf-8"))
            for rel in releases:
                if rel.get("draft"):
                    continue
                match = _select_llama_release_asset(rel.get("assets", []), profile)
                if match:
                    break
        if not match:
            raise RuntimeError(f"No prebuilt llama.cpp release asset found for {profile.os_name}/{profile.arch}")
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = Path(tmpdir) / match["name"]
            with urllib.request.urlopen(match["browser_download_url"], timeout=60) as dl:
                archive_path.write_bytes(dl.read())
            _extract_archive(archive_path, bin_dir)
            found_bin = next(bin_dir.rglob(exe_name), None)
            if found_bin and found_bin.parent != bin_dir:
                nested_dir = found_bin.parent
                for item in list(nested_dir.iterdir()):
                    dest = bin_dir / item.name
                    if dest.exists() or dest.is_symlink():
                        if dest.is_dir() and not dest.is_symlink():
                            shutil.rmtree(dest)
                        else:
                            dest.unlink()
                    if item.is_symlink():
                        os.symlink(os.readlink(item), dest)
                    else:
                        shutil.move(str(item), str(dest))
                shutil.rmtree(nested_dir, ignore_errors=True)
    else:
        backend_flags = {
            "nvidia": "-DGGML_CUDA=ON",
            "amd": "-DGGML_HIP=ON",
            "intel": "-DGGML_VULKAN=ON",
            "apple": "-DGGML_METAL=ON",
        }
        flag = backend_flags.get(profile.gpu_vendor, "-DGGML_NATIVE=ON")
        with tempfile.TemporaryDirectory() as tmpdir:
            src_dir = Path(tmpdir) / "llama.cpp"
            build_dir = src_dir / "build"
            subprocess.run(
                ["git", "clone", "--depth", "1", "https://github.com/ggml-org/llama.cpp.git", str(src_dir)],
                check=True,
            )
            subprocess.run(["cmake", "-B", str(build_dir), str(src_dir), flag], check=True)
            subprocess.run(["cmake", "--build", str(build_dir), "--config", "Release", "--target", "llama-server"], check=True)
            built_bin = next(build_dir.rglob(exe_name), None)
            if built_bin:
                shutil.copy2(built_bin, bin_dir / exe_name)

    target_bin = bin_dir / exe_name
    if not target_bin.is_file():
        raise RuntimeError(f"Expected binary {target_bin} not found after installation.")
    if sys.platform != "win32":
        os.chmod(target_bin, 0o755)
    _persist_server_bin(workspace, f"bin/{exe_name}")
    print(f"Installed llama-server to {target_bin}")


def _install_reboot_crontab(server_bin: str, models_ini: str, host: str, port: int, dry_run: bool) -> None:
    """Install a deduplicated @reboot user crontab entry tagged with # llama-mcp-bridge."""
    entry = (
        f"@reboot nohup {server_bin} --models-preset {models_ini} "
        f"--host {host} --port {port} >/dev/null 2>&1 & # llama-mcp-bridge"
    )
    if dry_run:
        print(f"[DRY-RUN] Would install crontab entry:\n{entry}")
        return

    res = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
    existing = res.stdout.splitlines() if res.returncode == 0 else []
    filtered = [line for line in existing if "# llama-mcp-bridge" not in line]
    filtered.append(entry)
    subprocess.run(["crontab", "-"], input="\n".join(filtered) + "\n", text=True, check=False)


def _generate_service_file(dry_run: bool) -> None:
    """Generate an OS-specific service unit or startup task."""
    cfg = load_config()
    server_bin = cfg.llama_server_bin or "llama-server"

    if sys.platform == "win32":
        workspace = Path(cfg.workspace)
        bat_path = workspace / "start-llama-server.bat"
        bat_content = (
            "@echo off\n"
            'cd /d "%~dp0"\n'
            f'start "" /B "{server_bin}" --models-preset "{cfg.models_ini}" '
            f"--host {cfg.host} --port {cfg.port}\n"
        )
        schtasks_cmd = [
            "schtasks", "/Create", "/F", "/TN", "LlamaServer",
            "/SC", "ONLOGON", "/TR", f'"{bat_path}"',
        ]
        if dry_run:
            print(f"[DRY-RUN] Would write {bat_path}:\n{bat_content}")
            print(f"[DRY-RUN] Would run: {' '.join(schtasks_cmd)}")
            return
        workspace.mkdir(parents=True, exist_ok=True)
        bat_path.write_text(bat_content, encoding="utf-8")
        subprocess.run(schtasks_cmd, check=False)
        return

    if sys.platform == "darwin":
        home = get_config_home()
        plist_path = home / "com.llama-server.plist"
        plist_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n'
            "<dict>\n"
            "  <key>Label</key>\n"
            "  <string>com.llama-server</string>\n"
            "  <key>WorkingDirectory</key>\n"
            f"  <string>{cfg.workspace}</string>\n"
            "  <key>ProgramArguments</key>\n"
            "  <array>\n"
            f"    <string>{server_bin}</string>\n"
            "    <string>--models-preset</string>\n"
            f"    <string>{cfg.models_ini}</string>\n"
            "    <string>--host</string>\n"
            f"    <string>{cfg.host}</string>\n"
            "    <string>--port</string>\n"
            f"    <string>{cfg.port}</string>\n"
            "  </array>\n"
            "  <key>RunAtLoad</key>\n"
            "  <true/>\n"
            "</dict>\n"
            "</plist>\n"
        )
        if dry_run:
            print(f"[DRY-RUN] Would write {plist_path}")
            return
        home.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(plist_xml, encoding="utf-8")
        return

    if sys.platform.startswith("linux"):
        if shutil.which("systemctl") and Path("/run/systemd/system").exists():
            systemd_dir = Path.home() / ".config" / "systemd" / "user"
            service_path = systemd_dir / "llama-server.service"
            unit = (
                "[Unit]\n"
                "Description=llama.cpp Router Server for llama-mcp-bridge\n"
                "After=network.target\n\n"
                "[Service]\n"
                f"WorkingDirectory={cfg.workspace}\n"
                f"ExecStart={server_bin} --models-preset {cfg.models_ini} "
                f"--host {cfg.host} --port {cfg.port}\n"
                "Restart=on-failure\n\n"
                "[Install]\n"
                "WantedBy=default.target\n"
            )
            if dry_run:
                print(f"[DRY-RUN] Would write {service_path}:\n{unit}")
                return
            systemd_dir.mkdir(parents=True, exist_ok=True)
            service_path.write_text(unit, encoding="utf-8")
            return

        _install_reboot_crontab(server_bin, cfg.models_ini, cfg.host, cfg.port, dry_run)
        print(
            "\nOpenRC reference (/etc/init.d/llama-server):\n"
            f'command="{server_bin}"\n'
            f'command_args="--models-preset {cfg.models_ini} --host {cfg.host} --port {cfg.port}"\n'
            "command_background=true\n"
        )
        return

    if sys.platform.startswith(("freebsd", "openbsd", "netbsd")):
        _install_reboot_crontab(server_bin, cfg.models_ini, cfg.host, cfg.port, dry_run)
        print(
            "\nFreeBSD rc.d template (/usr/local/etc/rc.d/llama_server via daemon(8)):\n"
            'command="/usr/sbin/daemon"\n'
            f'command_args="-f -P /var/run/llama_server.pid {server_bin} '
            f'--models-preset {cfg.models_ini} --host {cfg.host} --port {cfg.port}"\n'
        )
        return


def _compute_sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file in 1 MiB streaming chunks."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _verify_and_finalize_part(part: Path, target: Path, filename: str, expected_sha256: str | None) -> None:
    """Verify .part SHA-256 when expected_sha256 is present, then atomically rename."""
    if expected_sha256:
        actual = _compute_sha256(part)
        if actual.lower() != expected_sha256.lower():
            part.unlink(missing_ok=True)
            raise RuntimeError(
                f"SHA-256 mismatch for {filename}: expected {expected_sha256}, got {actual}"
            )
        print(f"Verified SHA-256: {actual[:12]}...")
    part.replace(target)


def _download_tier_models(
    profile: SystemProfile,
    models_dir: Path,
    dry_run: bool = False,
    presets: Sequence[str] | None = None,
) -> None:
    """Download curated GGUF models for the detected tier with .part resume support."""
    specs = get_tier_model_specs(profile.tier, presets=presets)
    models_dir.mkdir(parents=True, exist_ok=True)

    for spec in specs:
        target = models_dir / spec["filename"]
        if target.exists() and target.stat().st_size > 0:
            print(f"Skipping existing model: {target.name}")
            continue
        if dry_run:
            print(f"[DRY-RUN] Would download {spec['filename']} ({spec['size_gb']} GB) from {spec['url']}")
            continue

        ok, _, expected_sha256 = probe_model_metadata(spec["url"])
        if not ok:
            print(f"WARNING: {spec['filename']} not found at {spec['url']}; skipping.")
            stem = spec["filename"].replace(".gguf", "").split("-")[0]
            alts = search_hf_gguf_models(stem, limit=3)
            if alts:
                print(f"  Suggested HF GGUF alternatives: {', '.join(a['id'] for a in alts)}")
            continue

        part = models_dir / f"{spec['filename']}.part"
        offset = part.stat().st_size if part.exists() else 0
        req = urllib.request.Request(spec["url"])
        if offset > 0:
            req.add_header("Range", f"bytes={offset}-")

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                status = getattr(resp, "status", 200)
                mode = "ab" if status == 206 and offset > 0 else "wb"
                total = int(resp.headers.get("Content-Length", "0") or 0)
                is_resume = status == 206 and offset > 0
                full_total = (offset + total) if (is_resume and total > 0) else (total or None)
                completed = offset if is_resume else 0
                with Progress() as progress:
                    task = progress.add_task(
                        f"Downloading {spec['filename']}", total=full_total, completed=completed
                    )
                    with part.open(mode) as fh:
                        while True:
                            chunk = resp.read(1024 * 1024)
                            if not chunk:
                                break
                            fh.write(chunk)
                            progress.update(task, advance=len(chunk))
            _verify_and_finalize_part(part, target, spec["filename"], expected_sha256)
        except urllib.error.HTTPError as err:
            if err.code == 416 and part.exists():
                _verify_and_finalize_part(part, target, spec["filename"], expected_sha256)
            else:
                raise


_PRESET_NAMES = (
    "digest",
    "coder-fast",
    "coder",
    "coder-long",
    "vision",
    "vision-deep",
    "reasoner",
    "analyst",
    "generalist",
)

_HARNESS_GUIDES: dict[str, tuple[str, str]] = {
    "claude-code": ("~/.claude.json", "https://docs.anthropic.com/en/docs/claude-code/mcp"),
    "cursor": ("~/.cursor/mcp.json", "https://docs.cursor.com/context/model-context-protocol"),
    "cline": ("VS Code globalStorage cline_mcp_settings.json", "https://docs.cline.bot/mcp-servers/configuring-mcp-servers"),
    "windsurf": ("~/.codeium/windsurf/mcp_config.json", "https://docs.windsurf.com/windsurf/mcp"),
    "zed": ("~/.config/zed/settings.json", "https://zed.dev/docs/assistant/model-context-protocol"),
    "antigravity": ("~/.gemini/antigravity/mcp_config.json", "https://antigravity.google/docs/mcp"),
    "other": ("Your MCP client's mcpServers JSON config", "https://modelcontextprotocol.io/quickstart/user"),
}


def ps_main() -> None:
    """Render an Ollama-style live status table for server health and loaded presets."""
    console = Console()
    cfg = load_config()
    status = LlamaSupervisor(cfg).get_status()
    state_label = "[green]ONLINE[/green]" if status["healthy"] else "[red]OFFLINE[/red]"
    console.print(
        f"Server: [bold]{cfg.host}:{cfg.port}[/bold] | State: {state_label} | "
        f"Residency: [cyan]{status.get('residency', cfg.residency)}[/cyan] | "
        f"Tier: [cyan]{status['tier']}[/cyan]"
    )
    table = Table(title="Specialist Presets")
    table.add_column("Preset", style="bold")
    table.add_column("Status")
    loaded_map = status.get("models", {})
    for name in _PRESET_NAMES:
        st = loaded_map.get(name, "idle")
        badge = "[green]LOADED[/green]" if st == "loaded" else "[dim]IDLE[/dim]"
        table.add_row(name, badge)
    console.print(table)


def _run_doctor() -> int:
    """Run an end-to-end diagnostic check and return exit code (0=ok/warn, 1=fail)."""
    console = Console()
    cfg = load_config()
    workspace = Path(cfg.workspace)
    table = Table(title="llama-mcp-bridge Doctor")
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Details")

    failed = False

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
        table.add_row("Config files", "[green]PASS[/green]", f"{bridge_ini} & {models_ini}")
    else:
        failed = True
        table.add_row("Config files", "[red]FAIL[/red]", "Missing or invalid bridge.ini / models.ini")

    srv_bin = _find_llama_server(workspace, cfg.llama_server_bin)
    if srv_bin and (sys.platform == "win32" or os.access(srv_bin, os.X_OK)):
        table.add_row("llama-server", "[green]PASS[/green]", str(srv_bin))
    else:
        failed = True
        table.add_row("llama-server", "[red]FAIL[/red]", "Binary not found or not executable")

    missing_models: list[str] = []
    if models_ini.is_file():
        cp = configparser.ConfigParser()
        cp.read(models_ini, encoding="utf-8")
        for sec in cp.sections():
            for key in ("model", "mmproj"):
                if cp.has_option(sec, key):
                    mp = Path(cp.get(sec, key))
                    if not mp.is_file() or mp.stat().st_size == 0:
                        missing_models.append(str(mp))
    if missing_models:
        failed = True
        table.add_row(
            "GGUF files",
            "[red]FAIL[/red]",
            f"{len(missing_models)} missing. Run: llama-mcp-install --download-models --models-dir {cfg.models_dir}",
        )
    else:
        table.add_row("GGUF files", "[green]PASS[/green]", f"All referenced files present in {cfg.models_dir}")

    status = LlamaSupervisor(cfg).get_status()
    if status["healthy"]:
        table.add_row("Server /health", "[green]PASS[/green]", f"Reachable at {status['server_url']}")
    else:
        table.add_row("Server /health", "[yellow]WARN[/yellow]", "Stopped (lazy startup will spawn on demand)")

    console.print(table)
    return 1 if failed else 0


def _configure_client(harness: str) -> None:
    """Print zero-mutation MCP client JSON snippet, target config path, and official guide URL."""
    console = Console()
    if harness == "prompt":
        harness = Prompt.ask(
            "Choose your coding harness",
            choices=[k for k in _HARNESS_GUIDES if k != "prompt"],
            default="claude-code",
        )
    cfg = load_config()
    target_path, doc_url = _HARNESS_GUIDES.get(harness, _HARNESS_GUIDES["other"])
    snippet = json.dumps(
        {
            "mcpServers": {
                "local-ai": {
                    "command": "llama-mcp-bridge",
                    "env": {"LLAMA_MCP_HOME": cfg.workspace},
                }
            }
        },
        indent=2,
    )
    console.print(
        Panel(
            f"[bold]Target config (reference):[/bold] {target_path}\n"
            f"[bold]Official guide:[/bold] {doc_url}",
            title=f"MCP Setup: {harness}",
            border_style="cyan",
        )
    )
    console.print(snippet, markup=False, soft_wrap=True)


def _uninstall_bridge(dry_run: bool, yes: bool) -> None:
    """Remove managed binaries, build trees, and OS services while preserving models & INIs."""
    console = Console()
    cfg = load_config()
    workspace = Path(cfg.workspace)

    summary = Table(title="llama-mcp-bridge Uninstall Plan")
    summary.add_column("Action", style="bold")
    summary.add_column("Targets")
    summary.add_row(
        "[red]Remove[/red]",
        f"{workspace / 'bin'}, {workspace / 'llama.cpp'}, {workspace / 'build'}, OS background service",
    )
    summary.add_row(
        "[green]Preserve[/green]",
        f"{workspace / 'bridge.ini'}, {cfg.models_ini}, {cfg.models_dir}",
    )
    console.print(summary)

    if dry_run:
        print("[DRY-RUN] No files or services were modified.")
        return

    if not yes:
        if not sys.stdin.isatty():
            print("Non-interactive session without --yes; aborting uninstall.")
            return
        if not Confirm.ask("Proceed with uninstall?", default=False):
            print("Uninstall cancelled.")
            return

    try:
        LlamaSupervisor(cfg).stop()
    except (OSError, subprocess.SubprocessError):
        pass

    if sys.platform == "win32":
        subprocess.run(["schtasks", "/Delete", "/TN", "LlamaServer", "/F"], check=False)
        bat_path = workspace / "start-llama-server.bat"
        bat_path.unlink(missing_ok=True)
    elif sys.platform == "darwin":
        for plist_path in (
            Path(get_config_home()) / "com.llama-server.plist",
            Path.home() / "Library" / "LaunchAgents" / "com.llama-server.plist",
        ):
            if plist_path.exists():
                subprocess.run(["launchctl", "unload", str(plist_path)], check=False)
                plist_path.unlink(missing_ok=True)
    elif sys.platform.startswith("linux"):
        systemd_unit = Path.home() / ".config" / "systemd" / "user" / "llama-server.service"
        if shutil.which("systemctl") and Path("/run/systemd/system").exists():
            subprocess.run(["systemctl", "--user", "disable", "--now", "llama-server.service"], check=False)
            systemd_unit.unlink(missing_ok=True)
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        else:
            res = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
            if res.returncode == 0:
                kept = [
                    ln
                    for ln in res.stdout.splitlines()
                    if "# llama-mcp-bridge" not in ln and "llama-server" not in ln
                ]
                if kept:
                    subprocess.run(["crontab", "-"], input="\n".join(kept) + "\n", text=True, check=False)
                else:
                    subprocess.run(["crontab", "-r"], check=False)
    elif sys.platform.startswith(("freebsd", "openbsd", "netbsd")):
        res = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
        if res.returncode == 0:
            kept = [
                ln
                for ln in res.stdout.splitlines()
                if "# llama-mcp-bridge" not in ln and "llama-server" not in ln
            ]
            if kept:
                subprocess.run(["crontab", "-"], input="\n".join(kept) + "\n", text=True, check=False)
            else:
                subprocess.run(["crontab", "-r"], check=False)

    for folder in (workspace / "bin", workspace / "llama.cpp", workspace / "build"):
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)

    print("Uninstall complete. Your models and .ini files were preserved.")
    print("To remove the CLI package itself: uv tool uninstall llama-mcp-bridge")


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

    os.environ["LLAMA_MCP_HOME"] = str(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    ini_content = generate_models_ini(
        profile,
        models_dir=str(models_dir),
        residency=residency,
        selected_presets=selected_presets,
    )
    (workspace / "models.ini").write_text(ini_content, encoding="utf-8")

    specs = get_tier_model_specs(profile.tier, presets=selected_presets or None)
    total_gb = sum(s["size_gb"] for s in specs)
    if selected_presets and Confirm.ask(
        f"Download GGUF models for your selected presets now (~{total_gb:.1f} GB)?",
        default=False,
    ):
        _download_tier_models(profile, models_dir, dry_run=False, presets=selected_presets)

    bridge_ini = workspace / "bridge.ini"
    if not bridge_ini.exists():
        bridge_ini.write_text(
            f"[bridge]\nworkspace = {workspace}\nmodels_dir = {models_dir}\n"
            f"models_ini = {workspace / 'models.ini'}\nresidency = {residency}\n",
            encoding="utf-8",
        )

    if not found_bin and mode != "skip":
        _auto_install_llama_server(profile, mode, workspace, dry_run=False, yes=True)

    if install_svc:
        _generate_service_file(dry_run=False)

    mcp_snippet = json.dumps(
        {"mcpServers": {"local-ai": {"command": "llama-mcp-bridge", "env": {"LLAMA_MCP_HOME": str(workspace)}}}},
        indent=2,
    )
    console.print(Panel(mcp_snippet, title="Add to mcp_config.json", border_style="green"))
    gguf_lines = [ln.strip() for ln in ini_content.splitlines() if ln.strip().startswith("model = ")]
    if gguf_lines:
        console.print("[bold]Required GGUF model paths:[/bold]")
        for ln in gguf_lines:
            console.print(f"  {ln}")
    if Confirm.ask("Show MCP client setup snippet for a specific harness?", default=False):
        _configure_client("prompt")


    print("Manage, add, or remove models anytime with: llama-mcp-models")


def main() -> None:
    """Main entry point for the installer CLI."""
    parser = argparse.ArgumentParser(
        description="Install and configure llama-mcp-bridge.",
        epilog="Manage, add, or remove models anytime with: llama-mcp-models",
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
    parser.add_argument("--tier", type=str, help="Target VRAM tier for --verify-models.")
    parser.add_argument(
        "--residency",
        choices=["stingy", "moderate", "snappy"],
        default="moderate",
        help="Memory residency profile (stingy, moderate, snappy).",
    )
    parser.add_argument("--ps", action="store_true", help="Show live server and preset status.")
    parser.add_argument("--doctor", action="store_true", help="Run end-to-end diagnostic checklist.")
    parser.add_argument("--uninstall", action="store_true", help="Remove binaries, build dirs, and service while preserving models and INIs.")
    parser.add_argument(
        "--configure-client",
        nargs="?",
        const="prompt",
        choices=["prompt", "claude-code", "cursor", "cline", "windsurf", "zed", "antigravity", "other"],
        help="Print ready-to-paste MCP client JSON and official guide URL.",
    )
    parser.add_argument("--tui", action="store_true", help="Launch interactive rich TUI wizard.")
    parser.add_argument("--models-dir", type=str, help="Path to models directory.")
    parser.add_argument(
        "--presets",
        nargs="+",
        metavar="PRESET",
        help="Only emit selected presets in models.ini.",
    )

    args = parser.parse_args()
    if args.uninstall:
        _uninstall_bridge(dry_run=args.dry_run, yes=args.yes)
        return
    if args.ps:
        ps_main()
        return
    if args.doctor:
        sys.exit(_run_doctor())
    if args.configure_client:
        _configure_client(args.configure_client)
        return
    if args.verify_models:
        console = Console()
        results = verify_tier_models(args.tier)
        table = Table(title="Hugging Face Model Verification")
        table.add_column("Tier")
        table.add_column("Filename")
        table.add_column("Status")
        table.add_column("Details")
        any_missing = False
        for item in results:
            if item["ok"]:
                size_mb = f"{item['size_bytes'] / (1024**2):.1f} MB" if item["size_bytes"] else "OK"
                table.add_row(item["tier"], item["filename"], "[green]OK[/green]", size_mb)
            else:
                any_missing = True
                sugg = ", ".join(s["id"] for s in item["suggestions"]) or "none"
                table.add_row(item["tier"], item["filename"], "[red]MISSING[/red]", f"Try: {sugg}")
        console.print(table)
        if any_missing:
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
    (home / "models.ini").write_text(ini_content, encoding="utf-8")
    bridge_ini = home / "bridge.ini"
    if not bridge_ini.exists():
        bridge_ini.write_text(
            f"[bridge]\nworkspace = {home}\nmodels_dir = {models_dir}\n"
            f"models_ini = {home / 'models.ini'}\nresidency = {args.residency}\n",
            encoding="utf-8",
        )
    print(f"Wrote {home / 'models.ini'}")
    print("Manage, add, or remove models anytime with: llama-mcp-models")


if __name__ == "__main__":
    main()
