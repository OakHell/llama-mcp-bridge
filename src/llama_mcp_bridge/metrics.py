"""Prometheus /metrics and /healthz sidecar HTTP server for llama-mcp-bridge."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

__all__ = [
    "METRICS",
    "BridgeMetrics",
    "start_metrics_server",
]

_DURATION_BUCKETS = (0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0)


def _esc(val: str) -> str:
    return val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class BridgeMetrics:
    """Thread-safe Prometheus metrics collector for llama-mcp-bridge."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[tuple[str, str], int] = {}
        self._duration_sum: dict[str, float] = {}
        self._duration_count: dict[str, int] = {}
        self._duration_buckets: dict[tuple[str, float], int] = {}
        self._model_loads: dict[str, int] = {}
        self._oom_events: dict[str, int] = {}

    def record_request(self, preset: str, status: str, duration_s: float) -> None:
        """Record a completed tool/inference request and its duration."""
        dur = max(0.0, float(duration_s))
        with self._lock:
            self._requests[(preset, status)] = self._requests.get((preset, status), 0) + 1
            self._duration_sum[preset] = self._duration_sum.get(preset, 0.0) + dur
            self._duration_count[preset] = self._duration_count.get(preset, 0) + 1
            for bound in _DURATION_BUCKETS:
                if dur <= bound:
                    key = (preset, bound)
                    self._duration_buckets[key] = self._duration_buckets.get(key, 0) + 1

    def record_model_load(self, preset: str) -> None:
        """Increment model load counter for a preset."""
        with self._lock:
            self._model_loads[preset] = self._model_loads.get(preset, 0) + 1

    def record_oom(self, preset: str) -> None:
        """Increment OOM counter for a preset."""
        with self._lock:
            self._oom_events[preset] = self._oom_events.get(preset, 0) + 1

    def render_prometheus(self) -> str:
        """Render all collected metrics in Prometheus text exposition format."""
        lines: list[str] = []
        with self._lock:
            lines.append("# HELP llama_mcp_requests_total Total MCP inference requests by preset and status.")
            lines.append("# TYPE llama_mcp_requests_total counter")
            for (preset, status), count in sorted(self._requests.items()):
                lines.append(
                    f'llama_mcp_requests_total{{preset="{_esc(preset)}",status="{_esc(status)}"}} {count}'
                )

            lines.append("# HELP llama_mcp_request_duration_seconds Inference request latency histogram.")
            lines.append("# TYPE llama_mcp_request_duration_seconds histogram")
            for preset in sorted(self._duration_count):
                p_esc = _esc(preset)
                for bound in _DURATION_BUCKETS:
                    b_count = self._duration_buckets.get((preset, bound), 0)
                    lines.append(
                        f'llama_mcp_request_duration_seconds_bucket{{preset="{p_esc}",le="{bound}"}} {b_count}'
                    )
                total_c = self._duration_count[preset]
                lines.append(f'llama_mcp_request_duration_seconds_bucket{{preset="{p_esc}",le="+Inf"}} {total_c}')
                lines.append(
                    f'llama_mcp_request_duration_seconds_sum{{preset="{p_esc}"}} {self._duration_sum[preset]:.6f}'
                )
                lines.append(f'llama_mcp_request_duration_seconds_count{{preset="{p_esc}"}} {total_c}')

            lines.append("# HELP llama_mcp_model_loads_total Total model load / warmup events by preset.")
            lines.append("# TYPE llama_mcp_model_loads_total counter")
            for preset, count in sorted(self._model_loads.items()):
                lines.append(f'llama_mcp_model_loads_total{{preset="{_esc(preset)}"}} {count}')

            lines.append("# HELP llama_mcp_oom_events_total Total classified OOM events by preset.")
            lines.append("# TYPE llama_mcp_oom_events_total counter")
            for preset, count in sorted(self._oom_events.items()):
                lines.append(f'llama_mcp_oom_events_total{{preset="{_esc(preset)}"}} {count}')

        return "\n".join(lines) + "\n"


METRICS = BridgeMetrics()


def start_metrics_server(
    port: int,
    metrics: BridgeMetrics | None = None,
    host: str = "127.0.0.1",
    *,
    enterprise_mode: bool = False,
    readiness_check: Any = None,
) -> ThreadingHTTPServer:
    """Start a background daemon HTTP server exposing /metrics, /healthz, and (when enterprise) /livez + /readyz."""
    collector = metrics or METRICS

    class _MetricsHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.startswith("/metrics"):
                body = collector.render_prometheus().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path.startswith("/healthz"):
                body = json.dumps({"status": "ok"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif enterprise_mode and self.path.startswith("/livez"):
                body = json.dumps({"status": "alive"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif enterprise_mode and self.path.startswith("/readyz"):
                from llama_mcp_bridge.enterprise.probes import check_readiness

                fn = readiness_check or (lambda: (True, "ok"))
                code, payload = check_readiness(fn)
                body = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer((host, port), _MetricsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
