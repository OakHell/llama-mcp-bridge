# CLI Reference

All entrypoints support `--version`, `--log-level {DEBUG,INFO,WARNING,ERROR}`, and `--log-format {text,json}`.

## `llama-mcp-install`

| Flag | Description |
| :--- | :--- |
| `--mode {prebuilt,source-hermetic,source-system,system-bin}` | `llama-server` installation mode (default `prebuilt`). |
| `--residency {stingy,moderate,snappy}` | Memory residency profile (default `moderate`). |
| `--dry-run` | Preview planned actions without writing files or downloading. |
| `-y`, `--yes` | Auto-confirm `llama-server` installation. |
| `--download-models [PRESET ...]` | Download curated GGUF models for the detected tier. |
| `--verify-models` / `--verify-all` | Verify Hugging Face URLs (`--no-cache` bypasses 1h TTL; `--verify-all` also runs `--doctor`). |
| `--doctor` / `--ps` | Run diagnostic checklist or show live server/preset status (supports `--json`, `--verify-sha`). |
| `--profile` / `--selftest` | Benchmark presets or run live `ask_digest('ping')` smoke test (supports `--json`). |
| `--configure-client [HARNESS]` | Print MCP client JSON (`--apply` merges into target harness config). |
| `--import-ollama` | Symlink Ollama GGUF blobs into `models.ini` (`--ollama-dir` to override). |
| `--migrate-config` | Upgrade `bridge.ini` to `schema_version = 1` with `.ini.bak` backup. |
| `--install-service` / `--uninstall` | Install OS autostart service or remove binaries/service while preserving models/INIs. |
| `--install-skill [TARGET]` | Install `SKILL.md` (`--skill-link` to symlink). |
| `--register` / `--register-name NAME` | Register active or named workspace pointer. |
| `--enterprise` | Enable `enterprise_mode = true` in `bridge.ini`. |

## `llama-mcp-bridge`

| Flag | Description |
| :--- | :--- |
| `--dir PATH` / `--workspace-name NAME` | Target explicit or named workspace. |
| `--metrics` / `--metrics-port PORT` | Enable Prometheus `/metrics` + `/healthz` sidecar (`--metrics` defaults to `9090`). |
| `--warmup [PRESETS]` | Preload configured presets asynchronously on startup. |
| `--enterprise` | Enable opt-in `[enterprise]` mode (`/livez`, `/readyz`, `--audit-log`, `--watchdog`, `--tenant`, `--shared-models-dir`). |

## `llama-mcp-models` & `llama-mcp-config`

- `llama-mcp-models list | add | remove | --prune`: Manage `models.ini` presets (`--hf`, `--file`, `--tune-mode`, `--tool-name`).
- `llama-mcp-config check <path>`: Validate `bridge.ini` / `models.ini` syntax and schema.
- `llama-mcp-ps`: Standalone alias for `llama-mcp-install --ps`.

## `Makefile` Targets

- `make test`: `uv run pytest -v`
- `make lint`: `uv run ruff check .`
- `make typecheck`: `uv run mypy src`
- `make doctor`: `uv run llama-mcp-install --doctor`
- `make verify-all`: `uv run llama-mcp-install --verify-all`
- `make check`: `lint`, `typecheck`, and `test`
