import atexit
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Self

import requests
from requests.exceptions import ConnectionError, Timeout

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

    def __init__(self, config: LlamaServerConfig | None = None):
        """
        Initialize the LlamaSupervisor with optional configuration.

        Args:
            config: Optional LlamaServerConfig instance. If not provided,
                   uses default configuration with auto_spawn enabled.
        """
        self.config = config or LlamaServerConfig()
        self._process: subprocess.Popen | None = None
        self._health_status: ServerHealthStatus = ServerHealthStatus()
        self._lock = threading.Lock()
        self._running = False
        self._atexit_registered = False

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
            cmd.extend(getattr(self.config, "extra_args", []))

            # Prepare Popen kwargs
            kwargs = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "env": {**os.environ, **getattr(self.config, "env_vars", {})},
            }

            if sys.platform == "win32":
                # Windows specific process creation flags
                kwargs["creationflags"] = (
                    subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
                )
                # preexec_fn is ignored on Windows
            else:
                # POSIX specific session creation
                kwargs["preexec_fn"] = os.setsid

            self._process = subprocess.Popen(
                cmd,
                **kwargs,
            )
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

    def _check_health(self) -> bool:
        """
        Check the health endpoint of the server.

        Returns:
            True if server is healthy, False otherwise.
        """
        try:
            response = requests.get(
                self._get_health_url(),
                timeout=self.config.timeout_seconds,
                allow_redirects=True,
            )
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
        while retries < self.config.max_retries:
            time.sleep(self.config.poll_interval_seconds)
            if self._check_health():
                self._health_status.is_healthy = True
                self._health_status.last_check_time = time.time()
                self._health_status.error_message = ""
                return True
            retries += 1

        self._health_status.is_healthy = False
        self._health_status.error_message = (
            f"Server failed to become healthy after {self.config.max_retries} attempts"
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

    def get_status(self) -> dict[str, Any]:
        """Return server health, hardware tier, and loaded/unloaded model status."""
        from llama_mcp_bridge.hardware import detect_hardware

        profile = detect_hardware()
        healthy = self._check_health()
        models: dict[str, str] = {}
        if healthy:
            try:
                resp = requests.get(f"{self._get_server_url()}/models", timeout=2)
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    for item in data:
                        mid = item.get("id", "")
                        if mid:
                            status_obj = item.get("status", {})
                            models[mid] = status_obj.get("value", "loaded") if isinstance(status_obj, dict) else "loaded"
            except (requests.exceptions.RequestException, ValueError, KeyError):
                pass
        return {
            "healthy": healthy,
            "server_url": self._get_server_url(),
            "residency": getattr(self.config, "residency", "moderate"),
            "tier": profile.tier,
            "vram_gb": profile.vram_gb,
            "gpu_vendor": profile.gpu_vendor,
            "models": models,
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
            if not self._is_process_running():
                return True

            try:
                # Send SIGTERM for graceful shutdown
                if sys.platform != "win32":
                    os.killpg(self._process.pid, signal.SIGTERM)
                else:
                    self._process.terminate()

                # Wait for process to terminate
                try:
                    self._process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    # Force kill if graceful shutdown fails
                    if sys.platform != "win32":
                        os.killpg(self._process.pid, signal.SIGKILL)
                    else:
                        self._process.kill()
                    self._process.wait()

                # Close pipes on all platforms to prevent resource leaks
                if self._process.stdout:
                    self._process.stdout.close()
                if self._process.stderr:
                    self._process.stderr.close()

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

    Returns:
        Configured LlamaSupervisor instance.
    """
    config = LlamaServerConfig(
        host=host,
        port=port,
        binary=binary,
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