"""Cross-platform installer CLI for llama-mcp-bridge."""

import argparse
import os
import platform
import tarfile
import zipfile
from pathlib import Path

from llama_mcp_bridge.hardware import detect_hardware
from llama_mcp_bridge.presets import generate_models_ini


def _get_config_home() -> Path:
    """Determine the configuration home directory."""
    env_home = os.environ.get("LLAMA_MCP_HOME")
    if env_home:
        return Path(env_home).expanduser()
    return Path.home() / ".config" / "llama-mcp-bridge"


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


def _generate_service_file(dry_run: bool) -> None:
    """Generate an OS-specific service unit file."""
    os_name = platform.system()
    home = _get_config_home()

    if dry_run:
        print(f"[DRY-RUN] Would generate service file for {os_name} in {home}")
        return

    home.mkdir(parents=True, exist_ok=True)
    if os_name == "Linux":
        service_path = home / "llama-mcp-bridge.service"
        service_path.write_text(
            "[Unit]\nDescription=Llama MCP Bridge\n\n"
            "[Service]\nExecStart=/usr/bin/env python3 -m llama_mcp_bridge\n",
            encoding="utf-8",
        )
    elif os_name == "Darwin":
        plist_path = home / "com.llama-mcp-bridge.plist"
        plist_path.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n'
            "<dict>\n"
            "  <key>Label</key>\n"
            "  <string>com.llama-mcp-bridge</string>\n"
            "  <key>ProgramArguments</key>\n"
            "  <array>\n"
            "    <string>/usr/bin/env</string>\n"
            "    <string>python3</string>\n"
            "    <string>-m</string>\n"
            "    <string>llama_mcp_bridge</string>\n"
            "  </array>\n"
            "  <key>RunAtLoad</key>\n"
            "  <true/>\n"
            "</dict>\n"
            "</plist>\n",
            encoding="utf-8",
        )
    else:
        hint_path = home / "install_windows_hint.txt"
        hint_path.write_text(
            "Create Task Scheduler task: Run 'python -m llama_mcp_bridge'.\n",
            encoding="utf-8",
        )


def main() -> None:
    """Main entry point for the installer CLI."""
    parser = argparse.ArgumentParser(description="Install and configure llama-mcp-bridge.")
    parser.add_argument(
        "--mode",
        choices=["prebuilt", "source-hermetic", "source-system", "system-bin"],
        default="prebuilt",
        help="Installation mode.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions without executing.",
    )
    parser.add_argument(
        "--install-service",
        action="store_true",
        help="Generate service unit file.",
    )
    parser.add_argument(
        "--models-dir",
        type=str,
        help="Path to models directory.",
    )

    args = parser.parse_args()
    home = _get_config_home()
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
