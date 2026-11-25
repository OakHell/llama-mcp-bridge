"""Cross-platform hardware profiler for llama-mcp-bridge."""

import dataclasses
import platform
import subprocess


@dataclasses.dataclass(frozen=True)
class SystemProfile:
    """Represents the detected system hardware profile."""

    os_name: str
    arch: str
    gpu_vendor: str
    gpu_name: str
    vram_gb: float
    compute_cap: str
    distro_family: str
    tier: str


def classify_vram_tier(vram_gb: float) -> str:
    """Classifies VRAM into standard tiers based on capacity."""
    if vram_gb <= 6.5:
        return "6gb"
    if vram_gb <= 9.0:
        return "8gb"
    if vram_gb <= 13.0:
        return "12gb"
    if vram_gb <= 18.0:
        return "16gb"
    return "24gb+"


def _detect_distro_family() -> str:
    """Detects the underlying OS or Linux distribution family."""
    sys_name = platform.system().lower()
    if sys_name != "linux":
        return sys_name
    release = platform.release().lower()
    if "ubuntu" in release:
        return "ubuntu"
    if "debian" in release:
        return "debian"
    if any(tag in release for tag in ("centos", "rocky", "almalinux", "el")):
        return "rhel"
    if "arch" in release:
        return "arch"
    return "linux"


def _probe_nvidia() -> tuple[str, float, str] | None:
    """Probes NVIDIA GPU details using nvidia-smi."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,compute_cap",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            lines = result.stdout.strip().splitlines()
            if lines:
                parts = [p.strip() for p in lines[0].split(",")]
                name = parts[0]
                mem_mib = float(parts[1].replace("MiB", "").strip())
                raw_cap = parts[2] if len(parts) > 2 else ""
                compute = f"sm_{raw_cap.replace('.', '')}" if raw_cap and not raw_cap.startswith("sm_") else raw_cap
                return name, mem_mib / 1024.0, compute
    except Exception:
        pass
    return None


def _probe_amd() -> tuple[str, float] | None:
    """Probes AMD GPU details using rocm-smi."""
    try:
        result = subprocess.run(
            ["rocm-smi", "--showmeminfo", "vram", "--csv"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().splitlines()[1:]:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2:
                    vram_bytes = float(parts[1])
                    return "AMD ROCm", vram_bytes / (1024**3)
    except Exception:
        pass
    return None


def _probe_apple() -> tuple[str, float] | None:
    """Probes Apple Silicon unified memory using sysctl."""
    try:
        result = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            mem_bytes = int(result.stdout.strip())
            vram = mem_bytes * 0.75 / (1024**3)
            return "Apple Silicon", vram
    except Exception:
        pass
    return None


def _probe_intel_or_vulkan() -> tuple[str, float] | None:
    """Probes Intel GPUs or Vulkan-capable devices."""
    try:
        result = subprocess.run(
            ["lspci", "-k", "-n", "-d", "8086:"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return "Intel", 0.0
    except Exception:
        pass
    return None


def _probe_windows_wmi() -> tuple[str, str, float] | None:
    """Probes non-NVIDIA GPUs on Windows via PowerShell Get-CimInstance."""
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_VideoController | Select-Object -First 1 Name,AdapterRAM | ConvertTo-Csv -NoTypeInformation",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            lines = [ln.strip() for ln in result.stdout.strip().splitlines() if ln.strip()]
            if len(lines) >= 2:
                parts = [p.strip().strip('"') for p in lines[1].split(",")]
                name = parts[0]
                ram_bytes = float(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0.0
                vendor = "amd" if "amd" in name.lower() or "radeon" in name.lower() else "intel"
                return vendor, name, ram_bytes / (1024**3)
    except Exception:
        pass
    return None


def detect_hardware() -> SystemProfile:
    """Main entry point to detect system hardware profile."""
    os_name = platform.system().lower()
    arch = platform.machine().lower()
    distro_family = _detect_distro_family()

    gpu_vendor = "cpu"
    gpu_name = "CPU"
    vram_gb = 0.0
    compute_cap = ""

    if os_name == "darwin":
        apple = _probe_apple()
        if apple:
            gpu_vendor = "apple"
            gpu_name, vram_gb = apple
    elif os_name in ("linux", "windows"):
        nvidia = _probe_nvidia()
        if nvidia:
            gpu_vendor = "nvidia"
            gpu_name, vram_gb, compute_cap = nvidia
        elif os_name == "windows":
            wmi = _probe_windows_wmi()
            if wmi:
                gpu_vendor, gpu_name, vram_gb = wmi
        elif os_name == "linux":
            amd = _probe_amd()
            if amd:
                gpu_vendor = "amd"
                gpu_name, vram_gb = amd
            else:
                intel = _probe_intel_or_vulkan()
                if intel:
                    gpu_vendor = "intel"
                    gpu_name, vram_gb = intel

    tier = classify_vram_tier(vram_gb)
    return SystemProfile(
        os_name=os_name,
        arch=arch,
        gpu_vendor=gpu_vendor,
        gpu_name=gpu_name,
        vram_gb=round(vram_gb, 2),
        compute_cap=compute_cap,
        distro_family=distro_family,
        tier=tier,
    )
