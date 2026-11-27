# Troubleshooting

## 1. `--doctor` reports `Standby`
This is normal when `auto_spawn = true` in `bridge.ini`. `llama-server` spawns lazily on the first MCP tool call. Run `llama-mcp-install --selftest` to verify end-to-end auto-spawn.

## 2. Newly added `ask_<name>` tool missing in MCP client
Most MCP hosts cache `tools/list` at startup. Restart or reload your MCP client, or call `ask_preset(preset="<name>", ...)` immediately without restarting.

## 3. VRAM Out-of-Memory (OOM) or cold-swap timeout
- Set `residency = stingy` or `fallback_on_oom = true` in `bridge.ini`.
- Lower `ctx-size` in `models.ini`, or re-run `llama-mcp-models add --tune-mode conservative`.
- See [failure-classification.md](failure-classification.md).

## 4. `Config schema | WARN | unversioned (< 1)`
Run `llama-mcp-install --migrate-config` to add `schema_version = 1` with an atomic `.ini.bak` backup.

## 5. Windows `llama-server.exe` missing CUDA DLLs
Re-run `llama-mcp-install --mode prebuilt -y` so companion CUDA runtime DLLs (`cudart64_*.dll`, `cublas64_*.dll`, `cublasLt64_*.dll`) are co-located alongside `llama-server.exe`.

## 6. macOS Gatekeeper / `codesign` errors
Run `codesign --force --deep -s - <workspace>/bin/llama-server` (or re-run `llama-mcp-install --mode prebuilt -y` to apply ad-hoc signing to extracted Mach-O binaries and `.dylib`/`.metallib` files).
