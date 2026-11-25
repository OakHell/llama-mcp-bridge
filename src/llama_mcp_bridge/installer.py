"""Cross-platform installer CLI for llama-mcp-bridge."""

import argparse
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

from llama_mcp_bridge.config import get_config_home, load_config
from llama_mcp_bridge.hardware import detect_hardware
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
    parser.add_argument("--models-dir", type=str, help="Path to models directory.")

    args = parser.parse_args()
    home = get_config_home()
    models_dir = Path(args.models_dir) if args.models_dir else home / "models"

    profile = detect_hardware()
    ini_content = generate_models_ini(profile, models_dir=str(models_dir))

    print(f"Mode: {args.mode}")
    print(f"Hardware: {profile.os_name}/{profile.arch} {profile.gpu_vendor} ({profile.vram_gb} GB, tier={profile.tier})")
    print(f"Models Dir: {models_dir}")

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
