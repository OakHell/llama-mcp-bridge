"""Resilient HTTP client with exponential backoff, circuit breaker, and resource-error helpers."""

from __future__ import annotations

import errno
import logging
import random
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from llama_mcp_bridge.metrics import METRICS

__all__ = [
    "DEFAULT_HTTP_CLIENT",
    "CircuitBreakerOpenError",
    "ResilientHttpClient",
    "classify_resource_error",
    "ensure_disk_space",
]

_LOGGER = logging.getLogger(__name__)
_TRANSIENT_HTTP_CODES = frozenset({429, 502, 503, 504})


class CircuitBreakerOpenError(RuntimeError):
    """Raised when the HTTP circuit breaker is open after repeated transient failures."""


def ensure_disk_space(target_dir: Path, required_bytes: int, label: str = "operation") -> None:
    """Fail fast with an actionable error if free disk space is below required_bytes."""
    if required_bytes <= 0:
        return
    cur = target_dir
    while not cur.exists() and cur.parent != cur:
        cur = cur.parent
    try:
        free_bytes = shutil.disk_usage(cur).free
    except OSError:
        return
    if free_bytes < required_bytes:
        free_gb = free_bytes / (1024**3)
        req_gb = required_bytes / (1024**3)
        raise OSError(
            errno.ENOSPC,
            f"Insufficient disk space for {label}: {free_gb:.2f} GiB free, {req_gb:.2f} GiB required in {cur}.",
        )


def classify_resource_error(exc: BaseException, context: str = "operation") -> RuntimeError | None:
    """Translate ENOSPC, EMFILE/ENFILE, and thread-exhaustion errors into actionable RuntimeErrors."""
    if isinstance(exc, OSError):
        if exc.errno == errno.ENOSPC:
            return RuntimeError(
                f"Disk full (ENOSPC) during {context}. Free disk space or set --models-dir to another volume."
            )
        if exc.errno in (errno.EMFILE, errno.ENFILE):
            return RuntimeError(
                f"Open file descriptor limit reached (EMFILE/ENFILE) during {context}. Raise 'ulimit -n' and retry."
            )
    if isinstance(exc, RuntimeError) and "can't start new thread" in str(exc).lower():
        return RuntimeError(
            f"Thread limit reached during {context}. Reduce --max-workers or close background tasks."
        )
    return None


class ResilientHttpClient:
    """HTTP wrapper with jittered exponential backoff and a thread-safe circuit breaker."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_s: float = 30.0,
        max_retries: int = 3,
        base_delay_s: float = 0.5,
    ) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_timeout_s = max(0.1, recovery_timeout_s)
        self.max_retries = max(0, max_retries)
        self.base_delay_s = max(0.0, base_delay_s)
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None

    def reset(self) -> None:
        """Reset circuit breaker state."""
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def _check_circuit(self) -> None:
        with self._lock:
            if self._opened_at is None:
                return
            if (time.monotonic() - self._opened_at) < self.recovery_timeout_s:
                METRICS.record_request("http", "circuit_open", 0.0)
                raise CircuitBreakerOpenError(
                    f"HTTP circuit breaker is open after {self._failures} consecutive transient failures; "
                    f"retry after {self.recovery_timeout_s:.0f}s."
                )

    def _record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def _record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold and self._opened_at is None:
                self._opened_at = time.monotonic()
                _LOGGER.warning(
                    "HTTP circuit breaker opened",
                    extra={"event": "circuit_open", "failures": self._failures},
                )

    def urlopen(self, url_or_req: str | urllib.request.Request, timeout: float = 10.0) -> Any:
        """Open a URL with retries on transient 429/5xx/network errors and circuit-breaker protection."""
        self._check_circuit()
        last_exc: BaseException | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = urllib.request.urlopen(url_or_req, timeout=timeout)
                self._record_success()
                return resp
            except urllib.error.HTTPError as exc:
                if exc.code not in _TRANSIENT_HTTP_CODES:
                    raise
                last_exc = exc
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last_exc = exc

            self._record_failure()
            METRICS.record_request("http", "retry", 0.0)
            if attempt >= self.max_retries:
                break
            delay = self.base_delay_s * (2**attempt) + random.uniform(0.0, 0.1 * self.base_delay_s)
            _LOGGER.warning(
                "Transient HTTP error; retrying",
                extra={"event": "http_retry", "attempt": attempt + 1, "delay_s": round(delay, 3)},
            )
            if delay > 0:
                time.sleep(delay)

        assert last_exc is not None
        raise last_exc


DEFAULT_HTTP_CLIENT = ResilientHttpClient()
