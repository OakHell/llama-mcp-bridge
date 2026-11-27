# Configuration (`LLAMA_MCP_HOME`, `bridge.ini` & `models.ini`)

## Workspace Resolution (`LLAMA_MCP_HOME`)

- **POSIX default:** `~/.llama-mcp-bridge`
- **Windows default:** `%LOCALAPPDATA%\llama-mcp-bridge`
- Override via `LLAMA_MCP_HOME=/path`, `--dir /path`, or named workspaces (`--workspace-name <name>`).

## `bridge.ini` (`[bridge]`, `schema_version = 1`)

`bridge.ini` is the bridge's orchestration contract (see [`bridge.example.ini`](../bridge.example.ini)):

```ini
[bridge]
schema_version = 1
host = 127.0.0.1
port = 8080
auto_spawn = true
residency = moderate
models_max = 0
sleep_idle_seconds = -1
fallback_on_oom = false
health_timeout_s = 60
request_timeout_s = 180
log_format = text
```

- **Fresh Install:** `llama-mcp-install` stamps `schema_version = 1`, persists `llama_server_bin` whenever a binary is resolved, and includes commented defaults matching `bridge.example.ini`.
- **Residency Profiles:** `stingy` (`15s` idle, `1` model), `moderate` (`120s` idle, `1`–`2` models), `snappy` (`600s` idle, `2`–`3` models on `12gb+`).
- **Explicit Overrides:** Set `models_max` (`> 0`) or `sleep_idle_seconds` (`>= 0`) in `bridge.ini` to override tier+residency defaults.
- **Schema Migration:** Run `llama-mcp-install --migrate-config` to upgrade unversioned `bridge.ini` files with a `.ini.bak` backup.

## `models.ini`

`models.ini` is passed directly to `llama-server --models-preset`. Note that `llama-mcp-models add`/`remove` re-serialise `models.ini` via Python's `ConfigParser` (which strips inline or standalone `#` comments) and reject reserved section names (`""`, `"*"`, `"DEFAULT"`). Use `llama-mcp-install --export-config` or `--diff-config` to inspect effective merged settings.
