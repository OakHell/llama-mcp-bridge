"""Readiness/liveness probe helpers and pure-Python systemd sd_notify watchdog (E7)."""

from __future__ import annotations

import os
import socket
import threading
from collections.abc import Callable
from typing import Any

__all__ = ["check_readiness", "sd_notify", "start_watchdog_loop"]


def sd_notify(state: str, notify_socket: str | None = None) -> bool:
    """Send a systemd sd_notify datagram over NOTIFY_SOCKET (safe no-op on Windows/macOS)."""
    sock_path = notify_socket if notify_socket is not None else os.environ.get("NOTIFY_SOCKET", "")
    if not sock_path or not hasattr(socket, "AF_UNIX"):
        return False
    addr = f"\0{sock_path[1:]}" if sock_path.startswith("@") else sock_path
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(addr)
            sock.sendall(state.encode("utf-8"))
        return True
    except OSError:
        return False


def start_watchdog_loop(
    interval_s: float | None = None,
    *,
    notify_socket: str | None = None,
    stop_event: threading.Event | None = None,
) -> threading.Thread | None:
    """Start a daemon thread emitting WATCHDOG=1 at half WATCHDOG_USEC (or interval_s)."""
    if interval_s is None:
        raw_usec = os.environ.get("WATCHDOG_USEC", "").strip()
        if not raw_usec.isdigit() or int(raw_usec) <= 0:
            return None
        interval_s = max(0.05, (int(raw_usec) / 1_000_000.0) / 2.0)

    ev = stop_event or threading.Event()

    def _loop() -> None:
        while not ev.wait(interval_s):
            sd_notify("WATCHDOG=1", notify_socket=notify_socket)

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
    return thread


def check_readiness(
    health_check: Callable[[], tuple[bool, str]],
) -> tuple[int, dict[str, Any]]:
    """Evaluate readiness probe and return (HTTP status code, JSON payload)."""
    ok, reason = health_check()
    if ok:
        return 200, {"status": "ready"}
    return 503, {"status": "not_ready", "reason": reason}
