"""Cross-platform installer CLI for llama-mcp-bridge."""

import argparse
import configparser
import hashlib
import importlib.resources
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
from llama_mcp_bridge.paths import (
    get_default_workspace,
    read_registered_workspace,
    register_global_workspace,
    resolve_workspace,
    unregister_global_workspace,
)
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
        matched_release_assets = release.get("assets", [])
        match = _select_llama_release_asset(matched_release_assets, profile)
        if not match:
            list_url = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=10"
            with urllib.request.urlopen(list_url, timeout=15) as resp:
                releases = json.loads(resp.read().decode("utf-8"))
            for rel in releases:
                if rel.get("draft"):
                    continue
                rel_assets = rel.get("assets", [])
                match = _select_llama_release_asset(rel_assets, profile)
                if match:
                    matched_release_assets = rel_assets
                    break
        if not match:
            raise RuntimeError(f"No prebuilt llama.cpp release asset found for {profile.os_name}/{profile.arch}")
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = Path(tmpdir) / match["name"]
            with urllib.request.urlopen(match["browser_download_url"], timeout=60) as dl:
                archive_path.write_bytes(dl.read())
            _extract_archive(archive_path, bin_dir)
            _flatten_extracted_dir(bin_dir, exe_name)

            cudart_match = _select_cudart_release_asset(matched_release_assets, match["name"], profile)
            if cudart_match:
                cudart_path = Path(tmpdir) / cudart_match["name"]
                with urllib.request.urlopen(cudart_match["browser_download_url"], timeout=60) as dl:
                    cudart_path.write_bytes(dl.read())
                _extract_archive(cudart_path, bin_dir)
                _flatten_extracted_dir(bin_dir, "*.dll")

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
    _persist_server_bin(workspace, f"bin/{exe_name}")
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


def _download_spec_list(
    specs: Sequence[dict[str, Any]],
    models_dir: Path,
    dry_run: bool = False,
) -> None:
    """Download a list of model specs into models_dir with .part resume and SHA-256 verification."""
    models_dir.mkdir(parents=True, exist_ok=True)

    for spec in specs:
        target = models_dir / spec["filename"]
        if target.exists() and target.stat().st_size > 0:
            print(f"Skipping existing model: {target.name}")
            continue
        if dry_run:
            size_label = f"{spec['size_gb']} GB" if spec.get("size_gb") else "unknown size"
            print(f"[DRY-RUN] Would download {spec['filename']} ({size_label}) from {spec['url']}")
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


def _download_tier_models(
    profile: SystemProfile,
    models_dir: Path,
    dry_run: bool = False,
    presets: Sequence[str] | None = None,
) -> None:
    """Download curated GGUF models for the detected tier with .part resume support."""
    specs = get_tier_model_specs(profile.tier, presets=presets)
    _download_spec_list(specs, models_dir, dry_run=dry_run)


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
    "antigravity": ("~/.gemini/config/mcp_config.json", "https://antigravity.google/docs/mcp"),
    "other": ("Your MCP client's mcpServers JSON config", "https://modelcontextprotocol.io/quickstart/user"),
}


def ps_main() -> None:
    """Render an Ollama-style live status table for server health and loaded presets."""
    console = Console()
    cfg = load_config()
    status = LlamaSupervisor(cfg).get_status()
    if status["healthy"]:
        state_label = "[green]ONLINE[/green]"
    elif cfg.auto_spawn:
        state_label = "[cyan]STANDBY (auto-spawn)[/cyan]"
    else:
        state_label = "[red]OFFLINE[/red]"
    console.print(
        f"Server: [bold]{cfg.host}:{cfg.port}[/bold] | State: {state_label} | "
        f"Residency: [cyan]{status.get('residency', cfg.residency)}[/cyan] | "
        f"Tier: [cyan]{status['tier']}[/cyan]"
    )
    table = Table(title="Specialist Presets")
    table.add_column("Preset", style="bold")
    table.add_column("Status")
    loaded_map = status.get("models", {})
    preset_list = status.get("configured_presets") or list(loaded_map.keys()) or list(_PRESET_NAMES)
    for name in preset_list:
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
        try:
            ver_res = subprocess.run([str(srv_bin), "--version"], capture_output=True, text=True, timeout=5, check=False)
            if ver_res.returncode == 0:
                table.add_row("llama-server", "[green]PASS[/green]", str(srv_bin))
            else:
                failed = True
                table.add_row("llama-server", "[red]FAIL[/red]", f"{srv_bin} exited {ver_res.returncode} on --version")
        except (OSError, subprocess.SubprocessError) as exc:
            failed = True
            table.add_row("llama-server", "[red]FAIL[/red]", f"{srv_bin} failed to execute: {exc}")
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
    elif cfg.auto_spawn:
        table.add_row("Server /health", "[green]PASS[/green]", "Standby — will auto-spawn on first tool call")
    else:
        table.add_row("Server /health", "[yellow]WARN[/yellow]", "Stopped (auto_spawn=false)")

    console.print(table)
    return 1 if failed else 0


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
    server_cfg: dict[str, Any] = {"command": _resolve_bridge_command()}
    resolved_ws = workspace.expanduser().resolve()
    registered = read_registered_workspace(sys.platform)
    is_default = resolved_ws == get_default_workspace(sys.platform).expanduser().resolve()
    is_registered = registered is not None and resolved_ws == registered.expanduser().resolve()
    if not is_default and not is_registered:
        server_cfg["env"] = {"LLAMA_MCP_HOME": str(workspace)}
    return json.dumps({"mcpServers": {"local-ai": server_cfg}}, indent=2)


def _resolve_bundled_skill_path() -> Path:
    """Locate skills/llama-mcp-setup/SKILL.md via importlib.resources or repo-root fallback."""
    try:
        pkg_skill = importlib.resources.files("llama_mcp_bridge").joinpath("skills", "llama-mcp-setup", "SKILL.md")
        pkg_path = Path(str(pkg_skill))
        if pkg_path.is_file():
            return pkg_path
    except (ModuleNotFoundError, TypeError, OSError):
        pass
    repo_skill = Path(__file__).resolve().parents[2] / "skills" / "llama-mcp-setup" / "SKILL.md"
    if repo_skill.is_file():
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
    src = _resolve_bundled_skill_path()
    dests = _resolve_skill_destinations(target, home_dir=home_dir, cwd=cwd)
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
            base = Path(os.environ.get("APPDATA") or str(home / "AppData" / "Roaming")) / "Code"
        elif sys.platform == "darwin":
            base = home / "Library" / "Application Support" / "Code"
        else:
            base = home / ".config" / "Code"
        return [base / "User" / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json"]
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
    snippet = _build_mcp_snippet(Path(cfg.workspace))
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
        targets = _resolve_harness_config_paths(harness)
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
    candidates = [
        home / ".gemini" / "config" / "mcp_config.json",
        home / ".gemini" / "antigravity" / "mcp_config.json",
        home / ".claude.json",
        home / ".cursor" / "mcp.json",
    ]
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
        cfg_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"Removed 'local-ai' from {cfg_file}")
        updated.append(cfg_file)
    return updated


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
        _cleanup_harness_mcp_configs(dry_run=True)
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
    _cleanup_harness_mcp_configs(dry_run=False)
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
        ps_main()
        return
    if args.doctor:
        sys.exit(_run_doctor())
    if args.configure_client:
        _configure_client(args.configure_client, apply=args.apply, dry_run=args.dry_run)
        return
    if args.install_skill is not None:
        _install_skill(args.install_skill, symlink=args.skill_link, dry_run=args.dry_run)
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
