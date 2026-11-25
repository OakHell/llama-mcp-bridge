"""Cross-platform installer CLI for llama-mcp-bridge."""

import argparse
import configparser
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from llama_mcp_bridge.config import get_config_home, load_config
from llama_mcp_bridge.hardware import SystemProfile, detect_hardware
from llama_mcp_bridge.presets import generate_models_ini


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


def _auto_install_llama_server(
    profile: SystemProfile, mode: str, workspace: Path, dry_run: bool, yes: bool
) -> None:
    """Prompt and install llama-server into <workspace>/bin when undetected."""
    bin_dir = workspace / "bin"
    exe_name = "llama-server.exe" if sys.platform == "win32" else "llama-server"

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
        suffix = (
            _macos_release_asset(profile.arch)
            if profile.os_name == "darwin"
            else ("win" if profile.os_name == "windows" else "ubuntu")
        )
        assets = release.get("assets", [])
        match = next((a for a in assets if suffix in a.get("name", "").lower()), None)
        if not match:
            raise RuntimeError(f"No prebuilt llama.cpp release asset found for {suffix}")
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = Path(tmpdir) / match["name"]
            with urllib.request.urlopen(match["browser_download_url"], timeout=60) as dl:
                archive_path.write_bytes(dl.read())
            _extract_archive(archive_path, bin_dir)
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
    if target_bin.exists() and sys.platform != "win32":
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

    mode = "skip"
    if not found_bin:
        mode = Prompt.ask(
            "\nStep 3: llama-server install mode",
            choices=["prebuilt", "system-bin", "source-hermetic", "source-system", "skip"],
            default="prebuilt",
        )

    install_svc = Confirm.ask("\nStep 4: Register background service?", default=False)

    summary = Table(title="Step 5: Planned Configuration")
    summary.add_column("Setting", style="bold")
    summary.add_column("Value")
    summary.add_row("Workspace", str(workspace))
    summary.add_row("Models Dir", str(models_dir))
    summary.add_row("llama-server", mode if not found_bin else str(found_bin))
    summary.add_row("Install Service", "Yes" if install_svc else "No")
    console.print(summary)

    if not Confirm.ask("Proceed with installation?", default=True):
        console.print("[yellow]Aborted.[/yellow]")
        return

    os.environ["LLAMA_MCP_HOME"] = str(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    ini_content = generate_models_ini(profile, models_dir=str(models_dir))
    (workspace / "models.ini").write_text(ini_content, encoding="utf-8")

    bridge_ini = workspace / "bridge.ini"
    if not bridge_ini.exists():
        bridge_ini.write_text(
            f"[bridge]\nworkspace = {workspace}\nmodels_dir = {models_dir}\n"
            f"models_ini = {workspace / 'models.ini'}\n",
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


def main() -> None:
    """Main entry point for the installer CLI."""
    parser = argparse.ArgumentParser(description="Install and configure llama-mcp-bridge.")
    parser.add_argument(
        "--mode",
        choices=["prebuilt", "source-hermetic", "source-system", "system-bin"],
        default="prebuilt",
        help="Installation mode.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print planned actions without executing.")
    parser.add_argument("--install-service", action="store_true", help="Generate service unit file.")
    parser.add_argument("-y", "--yes", action="store_true", help="Auto-confirm llama-server installation.")
    parser.add_argument("--tui", action="store_true", help="Launch interactive rich TUI wizard.")
    parser.add_argument("--models-dir", type=str, help="Path to models directory.")

    args = parser.parse_args()
    if args.tui or (len(sys.argv) == 1 and sys.stdin.isatty()):
        _run_tui_wizard()
        return
    home = get_config_home()
    cfg = load_config()
    models_dir = Path(args.models_dir) if args.models_dir else home / "models"

    profile = detect_hardware()
    ini_content = generate_models_ini(profile, models_dir=str(models_dir))

    print(f"Mode: {args.mode}")
    print(f"Hardware: {profile.os_name}/{profile.arch} {profile.gpu_vendor} ({profile.vram_gb} GB, tier={profile.tier})")
    print(f"Models Dir: {models_dir}")

    _check_hardware_prerequisites(profile, args.mode)

    if not _find_llama_server(home, cfg.llama_server_bin):
        _auto_install_llama_server(profile, args.mode, home, args.dry_run, args.yes)

    if args.install_service:
        _generate_service_file(args.dry_run)

    if args.dry_run:
        print(f"[DRY-RUN] Would write models.ini ({len(ini_content)} bytes) to {home / 'models.ini'}")
        return

    home.mkdir(parents=True, exist_ok=True)
    (home / "models.ini").write_text(ini_content, encoding="utf-8")
    print(f"Wrote {home / 'models.ini'}")


if __name__ == "__main__":
    main()
