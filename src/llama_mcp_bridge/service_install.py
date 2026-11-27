"""Server binary installation, OS service generation, and uninstallation for llama-mcp-bridge."""

from __future__ import annotations

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
from typing import Any

from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table

from llama_mcp_bridge.client_config import _cleanup_harness_mcp_configs
from llama_mcp_bridge.config import get_config_home, load_config
from llama_mcp_bridge.hardware import SystemProfile
from llama_mcp_bridge.http_resilience import ensure_disk_space
from llama_mcp_bridge.paths import unregister_global_workspace
from llama_mcp_bridge.signing import verify_release_signature
from llama_mcp_bridge.supervisor import LlamaSupervisor

__all__ = [
    "_auto_install_llama_server",
    "_check_hardware_prerequisites",
    "_extract_archive",
    "_find_llama_server",
    "_flatten_extracted_dir",
    "_generate_service_file",
    "_install_reboot_crontab",
    "_macos_release_asset",
    "_persist_server_bin",
    "_select_cudart_release_asset",
    "_select_llama_release_asset",
    "_uninstall_bridge",
]


def _inst_attr(name: str, default: Any) -> Any:
    mod = sys.modules.get("llama_mcp_bridge.installer")
    return getattr(mod, name, default) if mod is not None else default


def _macos_release_asset(arch: str) -> str:
    """Map macOS architecture to llama.cpp release asset suffix."""
    return "macos-arm64" if arch.lower() in ("arm64", "aarch64") else "macos-x64"


def _extract_archive(
    archive_path: Path,
    dest_dir: Path,
    max_bytes: int = 2 * 1024**3,
    max_entries: int = 5000,
) -> None:
    """Extract .zip (Windows) or .tar.* (POSIX) archives with path-traversal, symlink, and size bounds."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    resolved_dest = dest_dir.resolve()
    name = archive_path.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive_path, "r") as zf:
            infos = zf.infolist()
            if len(infos) > max_entries:
                raise ValueError(f"Archive entry count {len(infos)} exceeds limit {max_entries}.")
            total_bytes = 0
            for info in infos:
                total_bytes += max(0, info.file_size)
                if total_bytes > max_bytes:
                    raise ValueError(f"Archive uncompressed size exceeds limit {max_bytes} bytes.")
                target = (resolved_dest / info.filename).resolve()
                if not target.is_relative_to(resolved_dest):
                    raise ValueError(f"Unsafe zip path traversal entry: {info.filename}")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    link_target = zf.read(info).decode("utf-8", errors="replace")
                    resolved_link = (target.parent / link_target).resolve()
                    if not resolved_link.is_relative_to(resolved_dest):
                        raise ValueError(f"Unsafe zip symlink target outside dest_dir: {info.filename} -> {link_target}")
            zf.extractall(dest_dir)
    elif name.endswith((".tar.gz", ".tgz", ".tar.xz")):
        with tarfile.open(archive_path, "r:*") as tf:
            members = tf.getmembers()
            if len(members) > max_entries:
                raise ValueError(f"Archive entry count {len(members)} exceeds limit {max_entries}.")
            total_bytes = 0
            for m in members:
                total_bytes += max(0, m.size)
                if total_bytes > max_bytes:
                    raise ValueError(f"Archive uncompressed size exceeds limit {max_bytes} bytes.")
                target = (resolved_dest / m.name).resolve()
                if not target.is_relative_to(resolved_dest):
                    raise ValueError(f"Unsafe tar path traversal entry: {m.name}")
                if m.issym() or m.islnk():
                    base = target.parent if m.issym() else resolved_dest
                    resolved_link = (base / m.linkname).resolve()
                    if not resolved_link.is_relative_to(resolved_dest):
                        raise ValueError(f"Unsafe tar symlink/link target outside dest_dir: {m.name} -> {m.linkname}")
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
        and subprocess.run(["xcode-select", "-p"], capture_output=True, check=False).returncode != 0
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
    """Persist llama_server_bin in <workspace>/bridge.ini while preserving key case."""
    ini_path = workspace / "bridge.ini"
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = lambda optionstr: optionstr  # type: ignore[method-assign]  # typeshed types optionxform as a method
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
        if profile.gpu_vendor == "amd" and ("rocm" in name or "hip" in name):
            score += 12
        elif (profile.gpu_vendor == "nvidia" and "cuda" in name) or (
            profile.gpu_vendor in ("amd", "intel") and "vulkan" in name
        ):
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


def _select_cudart_release_asset(
    assets: list[dict[str, Any]], main_asset_name: str, profile: SystemProfile
) -> dict[str, Any] | None:
    """Select the companion cudart archive matching a Windows CUDA llama.cpp release asset."""
    if profile.os_name != "windows" or profile.gpu_vendor != "nvidia" or "cuda" not in main_asset_name.lower():
        return None
    is_arm = profile.arch.lower() in ("arm64", "aarch64")
    arch_tok = "arm64" if is_arm else "x64"
    bad_arch = "x64" if is_arm else "arm64"
    main_lower = main_asset_name.lower()

    candidates: list[tuple[int, dict[str, Any]]] = []
    for asset in assets:
        name = asset.get("name", "").lower()
        if not name.startswith("cudart-") or not name.endswith(".zip"):
            continue
        if "win" not in name or bad_arch in name or arch_tok not in name:
            continue
        score = 0
        for part in main_lower.split("-"):
            if "." in part and part in name:
                score += 10
        candidates.append((score, asset))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _flatten_extracted_dir(root_dir: Path, target_name: str) -> None:
    """Flatten a nested archive directory containing target_name directly into root_dir."""
    found = next(root_dir.rglob(target_name), None)
    if found and found.parent != root_dir:
        nested_dir = found.parent
        for item in list(nested_dir.iterdir()):
            dest = root_dir / item.name
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


def _auto_install_llama_server(
    profile: SystemProfile,
    mode: str,
    workspace: Path,
    dry_run: bool,
    yes: bool,
    verify_signatures: bool = True,
) -> None:
    """Prompt and install llama-server into <workspace>/bin when undetected."""
    bin_dir = workspace / "bin"
    exe_name = "llama-server.exe" if profile.os_name == "windows" else "llama-server"

    if mode == "system-bin":
        linux_hints = {
            "debian": "sudo apt install llama.cpp  # or build from source",
            "ubuntu": "sudo apt install llama.cpp  # or build from source",
            "fedora": "sudo dnf install llama.cpp",
            "arch": "sudo pacman -S llama.cpp",
        }
        pkg_hints = {
            "darwin": "brew install llama.cpp",
            "win32": "winget install ggml.llamacpp",
            "linux": linux_hints.get(profile.distro_family, "sudo pacman -S llama.cpp  # or your distro package manager"),
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
        matched_release_assets = release.get("assets", [])
        match = _inst_attr("_select_llama_release_asset", _select_llama_release_asset)(
            matched_release_assets, profile
        )
        if not match:
            list_url = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=10"
            with urllib.request.urlopen(list_url, timeout=15) as resp:
                releases = json.loads(resp.read().decode("utf-8"))
            for rel in releases:
                if rel.get("draft"):
                    continue
                rel_assets = rel.get("assets", [])
                match = _inst_attr("_select_llama_release_asset", _select_llama_release_asset)(
                    rel_assets, profile
                )
                if match:
                    matched_release_assets = rel_assets
                    break
        if not match:
            raise RuntimeError(f"No prebuilt llama.cpp release asset found for {profile.os_name}/{profile.arch}")
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = Path(tmpdir) / match["name"]
            with urllib.request.urlopen(match["browser_download_url"], timeout=60) as dl:
                archive_path.write_bytes(dl.read())
            verify_release_signature(
                archive_path, matched_release_assets, verify_signatures=verify_signatures
            )
            _inst_attr("_extract_archive", _extract_archive)(archive_path, bin_dir)
            _inst_attr("_flatten_extracted_dir", _flatten_extracted_dir)(bin_dir, exe_name)

            cudart_match = _inst_attr("_select_cudart_release_asset", _select_cudart_release_asset)(
                matched_release_assets, match["name"], profile
            )
            if cudart_match:
                cudart_path = Path(tmpdir) / cudart_match["name"]
                with urllib.request.urlopen(cudart_match["browser_download_url"], timeout=60) as dl:
                    cudart_path.write_bytes(dl.read())
                _inst_attr("_extract_archive", _extract_archive)(cudart_path, bin_dir)
                _inst_attr("_flatten_extracted_dir", _flatten_extracted_dir)(bin_dir, "*.dll")

            if profile.os_name == "darwin":
                for ext in ("*.dylib", "*.metallib"):
                    for lib in list(bin_dir.rglob(ext)):
                        if lib.parent != bin_dir:
                            dest = bin_dir / lib.name
                            if not dest.exists():
                                shutil.move(str(lib), str(dest))
    else:
        backend_flags = {
            "nvidia": "-DGGML_CUDA=ON",
            "amd": "-DGGML_HIP=ON",
            "intel": "-DGGML_VULKAN=ON",
            "apple": "-DGGML_METAL=ON",
        }
        flag = backend_flags.get(profile.gpu_vendor, "-DGGML_NATIVE=ON")
        is_hermetic = mode == "source-hermetic"
        if not shutil.which("cmake") and not (is_hermetic and shutil.which("uvx")):
            raise RuntimeError("cmake not found; install cmake or uvx, or use --mode prebuilt")
        cmake_cmd = ["cmake"] if shutil.which("cmake") else ["uvx", "cmake"]
        build_env: dict[str, str] | None = None
        if is_hermetic:
            scrub = {"CFLAGS", "CXXFLAGS", "LDFLAGS", "CMAKE_PREFIX_PATH"}
            build_env = {k: v for k, v in os.environ.items() if k not in scrub}
            if profile.os_name == "windows" and not (shutil.which("cl") or shutil.which("g++") or shutil.which("clang")):
                print("Warning: No C/C++ compiler (cl.exe/g++/clang) detected on PATH; source-hermetic build may fail.")
            if not shutil.which("ninja") and shutil.which("uvx"):
                cmake_flags_extra = ["-DCMAKE_MAKE_PROGRAM=ninja"]
            else:
                cmake_flags_extra = []
        else:
            cmake_flags_extra = []
        cmake_flags = [
            flag,
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DBUILD_SHARED_LIBS={'OFF' if is_hermetic else 'ON'}",
            *cmake_flags_extra,
        ]
        if is_hermetic:
            cmake_flags.append("-DLLAMA_CURL=OFF")

        ensure_disk_space(workspace, 512 * 1024 * 1024, "llama-server source build")
        with tempfile.TemporaryDirectory() as tmpdir:
            src_dir = Path(tmpdir) / "llama.cpp"
            build_dir = src_dir / "build"
            subprocess.run(
                ["git", "clone", "--depth", "1", "https://github.com/ggml-org/llama.cpp.git", str(src_dir)],
                check=True,
            )
            subprocess.run([*cmake_cmd, "-B", str(build_dir), str(src_dir), *cmake_flags], check=True, env=build_env)
            subprocess.run(
                [*cmake_cmd, "--build", str(build_dir), "--config", "Release", "--target", "llama-server"],
                check=True,
                env=build_env,
            )
            built_bin = next(build_dir.rglob(exe_name), None)
            if built_bin:
                shutil.copy2(built_bin, bin_dir / exe_name)
            if not is_hermetic:
                for pattern in ("*.so*", "*.dylib", "*.dll"):
                    for lib in build_dir.rglob(pattern):
                        if lib.is_file() and not lib.is_symlink():
                            shutil.copy2(lib, bin_dir / lib.name)

    target_bin = bin_dir / exe_name
    if not target_bin.is_file():
        raise RuntimeError(f"Expected binary {target_bin} not found after installation.")
    if sys.platform != "win32":
        os.chmod(target_bin, 0o755)
    if profile.os_name == "darwin":
        for mach_o in [target_bin, *bin_dir.glob("*.dylib")]:
            if mach_o.is_file() and not mach_o.is_symlink():
                try:
                    res = subprocess.run(["codesign", "--force", "--deep", "-s", "-", str(mach_o)], check=False)
                    if res.returncode != 0:
                        print(f"Warning: ad-hoc codesign failed for {mach_o}; run: codesign --force --deep -s - {mach_o}")
                except FileNotFoundError:
                    print(f"Warning: codesign not found; sign manually on macOS: codesign --force --deep -s - {target_bin}")
                    break
    _inst_attr("_persist_server_bin", _persist_server_bin)(workspace, f"bin/{exe_name}")
    print(f"Installed llama-server to {target_bin}")


def _install_reboot_crontab(server_bin: str, models_ini: str, host: str, port: int, dry_run: bool) -> None:
    """Install a deduplicated @reboot user crontab entry tagged with # llama-mcp-bridge."""
    srv_path = Path(server_bin).expanduser()
    is_valid_abs = srv_path.is_absolute() and srv_path.is_file()
    if dry_run:
        entry = (
            f"@reboot nohup {srv_path} --models-preset {models_ini} "
            f"--host {host} --port {port} >/dev/null 2>&1 & # llama-mcp-bridge"
        )
        print(f"[DRY-RUN] Would install crontab entry:\n{entry}")
        if not is_valid_abs:
            print("[DRY-RUN] Note: an existing absolute llama_server_bin path will be required when dry_run=False.")
        return
    if not is_valid_abs:
        raise ValueError(
            f"Refusing to install @reboot crontab: server_bin must be an existing absolute path, got {server_bin!r}"
        )
    entry = (
        f"@reboot nohup {srv_path} --models-preset {models_ini} "
        f"--host {host} --port {port} >/dev/null 2>&1 & # llama-mcp-bridge"
    )

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
            "/SC", "ONLOGON", "/TR", f'conhost.exe --headless "{bat_path}"',
        ]
        if dry_run:
            print(f"[DRY-RUN] Would write {bat_path}:\n{bat_content}")
            print(f"[DRY-RUN] Would run: {' '.join(schtasks_cmd)}")
            return
        workspace.mkdir(parents=True, exist_ok=True)
        bat_path.write_text(bat_content, encoding="utf-8")
        subprocess.run(schtasks_cmd, check=False)
        subprocess.run(["schtasks", "/Run", "/TN", "LlamaServer"], check=False)
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
            "  <key>EnvironmentVariables</key>\n"
            "  <dict>\n"
            "    <key>LLAMA_MCP_HOME</key>\n"
            f"    <string>{cfg.workspace}</string>\n"
            "  </dict>\n"
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
        uid = os.getuid() if hasattr(os, "getuid") else 501
        domain = f"gui/{uid}"
        try:
            subprocess.run(
                ["launchctl", "bootout", domain, str(plist_path)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(["launchctl", "bootstrap", domain, str(plist_path)], check=False)
        except FileNotFoundError:
            pass
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
            try:
                subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
                subprocess.run(["systemctl", "--user", "enable", "--now", "llama-server.service"], check=True)
            except (subprocess.CalledProcessError, FileNotFoundError):
                print(
                    "Note: Could not auto-start systemd user service (no active user D-Bus session). "
                    "Start manually with: systemctl --user enable --now llama-server.service"
                )
            return

        _inst_attr("_install_reboot_crontab", _install_reboot_crontab)(
            server_bin, cfg.models_ini, cfg.host, cfg.port, dry_run
        )
        print(
            "\nOpenRC reference (/etc/init.d/llama-server):\n"
            f'command="{server_bin}"\n'
            f'command_args="--models-preset {cfg.models_ini} --host {cfg.host} --port {cfg.port}"\n'
            "command_background=true\n"
        )
        return

    if sys.platform.startswith(("freebsd", "openbsd", "netbsd")):
        _inst_attr("_install_reboot_crontab", _install_reboot_crontab)(
            server_bin, cfg.models_ini, cfg.host, cfg.port, dry_run
        )
        print(
            "\nFreeBSD rc.d template (/usr/local/etc/rc.d/llama_server via daemon(8)):\n"
            'command="/usr/sbin/daemon"\n'
            f'command_args="-f -P /var/run/llama_server.pid {server_bin} '
            f'--models-preset {cfg.models_ini} --host {cfg.host} --port {cfg.port}"\n'
        )
        return


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

    cleanup_fn = _inst_attr("_cleanup_harness_mcp_configs", _cleanup_harness_mcp_configs)
    if dry_run:
        cleanup_fn(dry_run=True)
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
        uid = os.getuid() if hasattr(os, "getuid") else 501
        domain = f"gui/{uid}"
        for plist_path in (
            Path(get_config_home()) / "com.llama-server.plist",
            Path.home() / "Library" / "LaunchAgents" / "com.llama-server.plist",
        ):
            if plist_path.exists():
                try:
                    subprocess.run(
                        ["launchctl", "bootout", domain, str(plist_path)],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except FileNotFoundError:
                    pass
                plist_path.unlink(missing_ok=True)
    elif sys.platform.startswith("linux"):
        systemd_unit = Path.home() / ".config" / "systemd" / "user" / "llama-server.service"
        if shutil.which("systemctl") and Path("/run/systemd/system").exists():
            subprocess.run(
                ["systemctl", "--user", "disable", "--now", "llama-server.service"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            systemd_unit.unlink(missing_ok=True)
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        else:
            res = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)
            if res.returncode == 0:
                kept = [
                    ln
                    for ln in res.stdout.splitlines()
                    if "# llama-mcp-bridge" not in ln
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
                if "# llama-mcp-bridge" not in ln
            ]
            if kept:
                subprocess.run(["crontab", "-"], input="\n".join(kept) + "\n", text=True, check=False)
            else:
                subprocess.run(["crontab", "-r"], check=False)

    for folder in (workspace / "bin", workspace / "llama.cpp", workspace / "build"):
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)

    unregister_global_workspace(only_if_matches=workspace)
    cleanup_fn(dry_run=False)
    print("Uninstall complete. Your models and .ini files were preserved.")
    print("To remove the CLI package itself: uv tool uninstall llama-mcp-bridge")
