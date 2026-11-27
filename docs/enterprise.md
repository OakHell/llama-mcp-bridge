# Enterprise Extra (`[enterprise]` / `--enterprise`)

> **Warning:** While `E3`, `E7`, and `E11` are verified by automated unit and fault-injection tests, **none of these features have been tested, deployed, or reported on in a real production environment yet**. Audit them in staging before relying on them in production.

Enable enterprise mode via `--enterprise` (or `enterprise_mode = true` in `bridge.ini`, or `LLAMA_MCP_ENTERPRISE=1`); `--enterprise` is a runtime flag requiring no extra dependencies. Base installs never import `llama_mcp_bridge.enterprise.*` unless enterprise mode is active.

## Features

- **Rotating JSONL Audit Log (`E3` — `enterprise/audit.py`):** `--audit-log <path>` records every MCP tool invocation (`timestamp`, `request_id`, `workspace`, `tenant`, `tool`, `preset`, `input_sha256`, hashed `files_accessed` SHA-256 prefixes, `duration_ms`, `status`, `fallback_used`) without storing raw prompt payloads or directory structures.
- **Liveness/Readiness & Watchdog (`E7` — `enterprise/probes.py`):** Exposes `/livez` (`200` even in `Standby`) and `/readyz` (`200` when healthy, `503 {"status": "error", ...}` on probe exceptions or `not_ready`) alongside `/metrics` (`--metrics`), plus pure-Python `sd_notify` (`--watchdog`).
- **Multi-Tenant Isolation (`E11` — `enterprise/tenant.py`):** `--tenant <name>` + `--shared-models-dir <path>` isolate per-tenant `bridge.ini`, `models.ini`, and logs (`0o700` on POSIX, symlink-checked) while sharing read-only GGUF blobs under cross-platform advisory file locks (blocking `fcntl.flock` on POSIX; 1-byte initialized `msvcrt.LK_LOCK` with ~10s retry window on Windows).
- **Structured JSON Logging (`E1`):** `--log-format json` emits single-line JSON log records across all CLI entrypoints.
