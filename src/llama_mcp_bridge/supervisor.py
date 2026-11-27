import atexit
import configparser
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

import requests
from requests.exceptions import ConnectionError, Timeout

from llama_mcp_bridge.config import BridgeConfig

__all__ = [
    "DEFAULT_AUTO_SPAWN",
    "DEFAULT_HEALTH_ENDPOINT",
    "DEFAULT_LLAMA_SERVER_URL",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "DEFAULT_SERVER_BINARY",
    "DEFAULT_TIMEOUT_SECONDS",
    "LlamaServerConfig",
    "LlamaSupervisor",
    "ServerHealthStatus",
    "classify_inference_error",
    "create_supervisor",
]

# British/South African English spelling conventions applied throughout
# e.g. "colour", "analyse", "behaviour", "programme", "centre"

DEFAULT_LLAMA_SERVER_URL = "http://127.0.0.1:8080"
DEFAULT_HEALTH_ENDPOINT = "/health"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_POLL_INTERVAL_SECONDS = 1
DEFAULT_MAX_RETRIES = 30
DEFAULT_AUTO_SPAWN = True
DEFAULT_SERVER_BINARY = "llama-server"


@dataclass
class LlamaServerConfig:
    """Configuration for the llama-server subprocess."""
    host: str = "127.0.0.1"
    port: int = 8080
    binary: str = DEFAULT_SERVER_BINARY
    models_ini: str = ""
    models_dir: str = ""
    residency: str = "moderate"
    models_max: int = 0
    sleep_idle_seconds: int = -1
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    auto_spawn: bool = DEFAULT_AUTO_SPAWN
    env_vars: dict[str, str] = field(default_factory=dict)
    extra_args: list[str] = field(default_factory=list)


@dataclass
class ServerHealthStatus:
    """Represents the health status of the llama-server."""
    is_healthy: bool = False
    last_check_time: float = 0.0
    error_message: str = ""
    process_id: int | None = None


class LlamaSupervisor:
    """
    Supervisor class for managing llama-server subprocess lifecycle.

    Provides lazy auto-spawning of llama-server instances with health
    monitoring and graceful shutdown capabilities. Ensures the server
    is running and healthy before attempting to communicate with it.
    """

    def __init__(self, config: BridgeConfig | LlamaServerConfig | None = None):
        """
        Initialize the LlamaSupervisor with optional configuration.

        Args:
            config: Optional LlamaServerConfig or BridgeConfig instance. If not provided,
                   uses default configuration with auto_spawn enabled.
        """
        self.config: Any = config or LlamaServerConfig()
        self._process: subprocess.Popen | None = None
        self._health_status: ServerHealthStatus = ServerHealthStatus()
        self._lock = threading.Lock()
        self._running = False
        self._atexit_registered = False
        self._stderr_tail: deque[str] = deque(maxlen=40)
        self._stderr_thread: threading.Thread | None = None
        self._log_path: Path | None = None

    def _get_server_url(self) -> str:
        """Construct the server URL from configuration."""
        return f"http://{self.config.host}:{self.config.port}"

    def _get_health_url(self) -> str:
        """Construct the health check endpoint URL."""
        return f"{self._get_server_url()}{DEFAULT_HEALTH_ENDPOINT}"

    def _is_process_running(self) -> bool:
        """Check if the subprocess is currently running."""
        if self._process is None:
            return False
        return self._process.poll() is None

    def _start_server(self) -> bool:
        """
        Start the llama-server subprocess.

        Returns:
            True if server started successfully, False otherwise.
        """
        try:
            binary_bin = getattr(self.config, "llama_server_bin", "") or getattr(self.config, "binary", "") or "llama-server"
            if sys.platform == "win32" and not binary_bin.lower().endswith(".exe"):
                binary_bin = f"{binary_bin}.exe"
            cmd = [binary_bin]
            host = getattr(self.config, "host", "") or "127.0.0.1"
            port = getattr(self.config, "port", 8080) or 8080
            cmd.extend(["--host", str(host), "--port", str(port)])
            models_ini = getattr(self.config, "models_ini", "")
            if models_ini and Path(models_ini).is_file():
                cmd.extend(["--models-preset", str(models_ini)])
            models_dir = getattr(self.config, "models_dir", "")
            if models_dir and Path(models_dir).is_dir():
                cmd.extend(["--models-dir", str(models_dir)])
            residency = str(getattr(self.config, "residency", "")).lower()
            cfg_models_max = int(getattr(self.config, "models_max", 0) or 0)
            cfg_idle_s = int(getattr(self.config, "sleep_idle_seconds", -1))
            if residency in ("stingy", "moderate", "snappy"):
                from llama_mcp_bridge.hardware import detect_hardware
                from llama_mcp_bridge.presets import resolve_residency_settings

                tier = getattr(self.config, "tier", "") or detect_hardware().tier
                idle_s, models_max = resolve_residency_settings(tier, residency)
                if cfg_models_max > 0:
                    models_max = cfg_models_max
                if cfg_idle_s >= 0:
                    idle_s = cfg_idle_s
                cmd.extend(["--models-max", str(models_max), "--sleep-idle-seconds", str(idle_s)])
            else:
                if cfg_models_max > 0:
                    cmd.extend(["--models-max", str(cfg_models_max)])
                if cfg_idle_s >= 0:
                    cmd.extend(["--sleep-idle-seconds", str(cfg_idle_s)])
            api_key = getattr(self.config, "api_key", "")
            if api_key:
                cmd.extend(["--api-key", str(api_key)])
            cmd.extend(getattr(self.config, "extra_args", []))

            from llama_mcp_bridge.config import get_config_home

            workspace = Path(getattr(self.config, "workspace", "") or get_config_home())
            workspace.mkdir(parents=True, exist_ok=True)
            custom_log = getattr(self.config, "server_log", "")
            self._log_path = Path(custom_log) if custom_log else (workspace / "llama-server.log")
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            log_fh = self._log_path.open("ab")

            # Prepare Popen kwargs
            kwargs: dict[str, Any] = {
                "stdin": subprocess.DEVNULL,
                "stdout": log_fh,
                "stderr": subprocess.STDOUT,
                "env": {**os.environ, **getattr(self.config, "env_vars", {})},
            }

            if sys.platform == "win32":
                detached = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | detached
            else:
                kwargs["start_new_session"] = True

            try:
                self._process = subprocess.Popen(
                    cmd,
                    **kwargs,
                )
            finally:
                log_fh.close()
            self._health_status.process_id = self._process.pid
            self._running = True
            return True
        except FileNotFoundError:
            error_msg = f"Server binary not found: {binary_bin}"
            self._health_status.is_healthy = False
            self._health_status.error_message = error_msg
            return False
        except (OSError, subprocess.SubprocessError, AttributeError) as e:
            error_msg = f"Failed to start server: {e!s}"
            self._health_status.is_healthy = False
            self._health_status.error_message = error_msg
            return False

    def _start_stderr_reader(self) -> None:
        """Start a daemon thread draining process stderr into a bounded deque."""
        proc = self._process
        if proc is None or proc.stderr is None:
            return
        stderr = proc.stderr

        def _drain() -> None:
            try:
                for line in stderr:
                    text = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else str(line)
                    self._stderr_tail.append(text.rstrip())
            except (OSError, ValueError):
                pass

        self._stderr_thread = threading.Thread(target=_drain, daemon=True)
        self._stderr_thread.start()

    def get_stderr_tail(self) -> str:
        """Return recent stderr lines from the managed llama-server subprocess."""
        if self._stderr_tail:
            return "\n".join(self._stderr_tail)
        if self._log_path and self._log_path.is_file():
            try:
                lines = self._log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                return "\n".join(lines[-40:])
            except OSError:
                pass
        return ""

    def _auth_headers(self) -> dict[str, str]:
        api_key = getattr(self.config, "api_key", "")
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def _check_health(self) -> bool:
        """
        Check the health endpoint of the server.

        Returns:
            True if server is healthy, False otherwise.
        """
        try:
            raw_timeout = getattr(
                self.config,
                "timeout_seconds",
                getattr(self.config, "request_timeout_s", DEFAULT_TIMEOUT_SECONDS),
            )
            headers = self._auth_headers()
            kwargs: dict[str, Any] = {
                "timeout": min(float(raw_timeout), 2.0),
                "allow_redirects": True,
            }
            if headers:
                kwargs["headers"] = headers
            response = requests.get(self._get_health_url(), **kwargs)
            return response.status_code == 200
        except (ConnectionError, Timeout, requests.exceptions.RequestException):
            return False

    def _poll_until_healthy(self) -> bool:
        """
        Poll the health endpoint until server is ready or timeout reached.

        Returns:
            True if server became healthy, False if timeout or error occurred.
        """
        retries = 0
        max_retries = int(getattr(self.config, "max_retries", DEFAULT_MAX_RETRIES))
        poll_interval = float(getattr(self.config, "poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS))
        while retries < max_retries:
            time.sleep(poll_interval)
            if self._check_health():
                self._health_status.is_healthy = True
                self._health_status.last_check_time = time.time()
                self._health_status.error_message = ""
                return True
            retries += 1

        self._health_status.is_healthy = False
        self._health_status.error_message = (
            f"Server failed to become healthy after {max_retries} attempts"
        )
        return False

    def _ensure_process_exists(self) -> bool:
        """Ensure the subprocess exists and is running."""
        if not self._is_process_running():
            return self._start_server()
        return True

    def ensure_running(self) -> bool:
        """
        Ensure the llama-server is running and healthy.

        If auto_spawn is enabled and the server is not running,
        this will spawn a new instance and wait for it to become healthy.

        Returns:
            True if server is healthy and ready, False otherwise.
        """
        with self._lock:
            if self._health_status.is_healthy:
                return True

            if not self.config.auto_spawn:
                self._health_status.is_healthy = False
                self._health_status.error_message = (
                    "Auto-spawn is disabled and server is not running"
                )
                return False

            if not self._ensure_process_exists():
                return False

        return self._poll_until_healthy()

    def _configured_presets(self) -> list[str]:
        """Return presets configured in models.ini, falling back to all valid presets if missing."""
        from llama_mcp_bridge.presets import _VALID_PRESETS

        raw_ini = getattr(self.config, "models_ini", "")
        if raw_ini:
            ini_path = Path(raw_ini)
            if ini_path.is_file():
                cp = configparser.ConfigParser()
                try:
                    cp.read(ini_path, encoding="utf-8")
                    return [sec for sec in cp.sections() if sec != "*"]
                except configparser.Error:
                    pass
        return list(_VALID_PRESETS)

    def get_status(self) -> dict[str, Any]:
        """Return server health, hardware tier, and loaded/unloaded model status."""
        from llama_mcp_bridge.hardware import detect_hardware

        profile = detect_hardware()
        healthy = self._check_health()
        configured = self._configured_presets()
        models: dict[str, str] = {}
        if healthy:
            try:
                headers = self._auth_headers()
                get_kwargs: dict[str, Any] = {"timeout": 2}
                if headers:
                    get_kwargs["headers"] = headers
                resp = requests.get(f"{self._get_server_url()}/models", **get_kwargs)
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    for item in data:
                        mid = item.get("id", "")
                        if mid:
                            status_obj = item.get("status", {})
                            models[mid] = status_obj.get("value", "loaded") if isinstance(status_obj, dict) else "loaded"
            except (requests.exceptions.RequestException, ValueError, KeyError):
                pass
        filtered_models = {name: models.get(name, "idle") for name in configured} if models or configured else models
        return {
            "healthy": healthy,
            "server_url": self._get_server_url(),
            "residency": getattr(self.config, "residency", "moderate"),
            "tier": profile.tier,
            "vram_gb": profile.vram_gb,
            "gpu_vendor": profile.gpu_vendor,
            "configured_presets": configured,
            "models": filtered_models,
        }

    def is_healthy(self) -> bool:
        """
        Check if the llama-server is currently healthy.

        Returns:
            True if server is healthy and ready, False otherwise.
        """
        with self._lock:
            if self._health_status.is_healthy:
                return True

            # Refresh health status if needed
            if self._is_process_running():
                is_healthy = self._check_health()
                self._health_status.is_healthy = is_healthy
                self._health_status.last_check_time = time.time()
                if not is_healthy:
                    self._health_status.error_message = "Health check failed"
                return is_healthy

            self._health_status.is_healthy = False
            self._health_status.error_message = "Server process not running"
            return False

    def stop(self) -> bool:
        """
        Gracefully stop the llama-server subprocess.

        Returns:
            True if server was stopped successfully, False otherwise.
        """
        with self._lock:
            proc = self._process
            if not self._is_process_running() or proc is None:
                return True

            try:
                # Send SIGTERM for graceful shutdown
                if sys.platform != "win32":
                    os.killpg(proc.pid, signal.SIGTERM)
                else:
                    proc.terminate()

                # Wait for process to terminate
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    # Force kill if graceful shutdown fails
                    if sys.platform != "win32":
                        os.killpg(proc.pid, signal.SIGKILL)
                    else:
                        proc.kill()
                    proc.wait()

                # Close pipes on all platforms to prevent resource leaks
                if proc.stdout:
                    proc.stdout.close()
                if proc.stderr:
                    proc.stderr.close()

                self._process = None
                self._running = False
                self._health_status.is_healthy = False
                self._health_status.error_message = "Server stopped"
                return True
            except (OSError, subprocess.SubprocessError, AttributeError) as e:
                self._health_status.error_message = f"Failed to stop server: {e!s}"
                return False

    def restart(self) -> bool:
        """
        Restart the llama-server by stopping and starting it.

        Returns:
            True if restart was successful, False otherwise.
        """
        self.stop()
        return self.ensure_running()

    def get_process_id(self) -> int | None:
        """
        Get the process ID of the running server.

        Returns:
            Process ID if running, None otherwise.
        """
        with self._lock:
            return self._health_status.process_id

    def get_health_status(self) -> ServerHealthStatus:
        """
        Get the current health status of the server.

        Returns:
            Current ServerHealthStatus instance.
        """
        with self._lock:
            return ServerHealthStatus(
                is_healthy=self._health_status.is_healthy,
                last_check_time=self._health_status.last_check_time,
                error_message=self._health_status.error_message,
                process_id=self._health_status.process_id,
            )

    def _cleanup(self) -> None:
        """Internal cleanup method called on shutdown."""
        self.stop()

    def register_atexit(self) -> None:
        """
        Register cleanup handler for atexit.

        Ensures the server is stopped when the Python process exits.
        """
        if not self._atexit_registered:
            atexit.register(self._cleanup)
            self._atexit_registered = True

    def __enter__(self) -> Self:
        """Context manager entry."""
        self.register_atexit()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.stop()


def create_supervisor(
    host: str = "127.0.0.1",
    port: int = 8080,
    binary: str = DEFAULT_SERVER_BINARY,
    auto_spawn: bool = True,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    models_ini: str = "",
    models_dir: str = "",
    residency: str = "moderate",
) -> LlamaSupervisor:
    """
    Factory function to create a configured LlamaSupervisor instance.

    Args:
        host: Server host address.
        port: Server port number.
        binary: Path to llama-server binary.
        auto_spawn: Whether to auto-spawn server if not running.
        timeout_seconds: Health check timeout in seconds.
        poll_interval_seconds: Interval between health check polls.
        max_retries: Maximum number of health check retries.
        models_ini: Path to models.ini preset file.
        models_dir: Path to models directory.
        residency: VRAM residency profile (stingy, moderate, snappy).

    Returns:
        Configured LlamaSupervisor instance.
    """
    config = LlamaServerConfig(
        host=host,
        port=port,
        binary=binary,
        models_ini=models_ini,
        models_dir=models_dir,
        residency=residency,
        auto_spawn=auto_spawn,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        max_retries=max_retries,
    )
    return LlamaSupervisor(config)


# Ensure cleanup on module exit
def _module_cleanup() -> None:
    """Cleanup function for module-level supervisor."""
    # Cleanup handled by instance methods


# Register module-level cleanup (optional, can be overridden)
# atexit.register(_module_cleanup)


_OOM_SIGNATURES = (
    "out of memory",
    "failed to allocate",
    "cudamalloc",
    "vk::outofdevicememoryerror",
    "insufficient memory",
    "cannot allocate memory",
    "metal: out of memory",
    "ggml_metal_buffer_init: failed",
)


def classify_inference_error(
    exc: Exception,
    stderr_tail: str,
    preset: str,
    profile: Any,
    residency: str,
) -> tuple[bool, str]:
    """Classify an inference error as OOM or timeout/other and return (is_oom, message)."""
    err_text = f"{exc}\n{stderr_tail}".lower()
    if any(sig in err_text for sig in _OOM_SIGNATURES):
        msg = (
            f"Error: Preset '{preset}' ran out of memory on tier '{profile.tier}' "
            f"(residency='{residency}'). Recovery: set residency = stingy in bridge.ini, "
            f"close GPU-heavy applications, or use ask_digest."
        )
        return True, msg

    if isinstance(exc, (TimeoutError, requests.exceptions.Timeout)) or "timed out" in err_text:
        msg = (
            f"Error: Preset '{preset}' timed out during inference or cold-swap on tier '{profile.tier}' "
            f"(residency='{residency}'). Recovery: increase request_timeout_s in bridge.ini or "
            f"pre-warm the model before heavy requests."
        )
        return False, msg

    return False, f"Error communicating with llama-server for preset '{preset}': {exc!s}"
