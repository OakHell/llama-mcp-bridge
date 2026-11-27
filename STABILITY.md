# Stability & Semantic Versioning Policy

`llama-mcp-bridge` follows [Semantic Versioning 2.0.0](https://semver.org/).

## Version Lines

- **`0.x` (Pre-1.0):** Minor releases (`0.13.0`, `0.14.0`) may introduce new CLI flags, configuration keys, and MCP tools. Breaking changes to existing documented flags or schemas are avoided where possible and called out in `SHIPPED.md`.
- **`1.x` (Stable):**
  - **MAJOR (`1.0.0` → `2.0.0`):** Breaking changes to CLI flags, `--json` output schemas, exit codes, `bridge.ini` schema, or MCP tool signatures.
  - **MINOR (`1.0.0` → `1.1.0`):** Backwards-compatible new CLI flags, optional `bridge.ini` keys, new MCP tools, or new optional parameters with defaults.
  - **PATCH (`1.0.0` → `1.0.1`):** Bug fixes, security patches, and documentation updates.

## Stability Guarantees

1. **CLI Entrypoints & Exit Codes:**
   - `llama-mcp-bridge`, `llama-mcp-install`, `llama-mcp-ps`, `llama-mcp-models`, `llama-mcp-config`.
   - Exit `0` indicates success (or `Standby` in `--doctor`); non-zero (`1`) indicates a check, validation, or runtime failure.
2. **`--json` Output Schemas:**
   - Existing top-level keys in `--doctor --json`, `--ps --json`, `--selftest --json`, `--profile --json`, `--check-update --json`, and `--model-info --json` are preserved within a major version; new keys may be added additively.
3. **Configuration Contracts (`bridge.ini` & `models.ini`):**
   - `bridge.ini` carries `schema_version` (`CURRENT_SCHEMA_VERSION = 1`). Backwards-incompatible `bridge.ini` changes require incrementing `schema_version` and providing an automated `llama-mcp-install --migrate-config` path.
   - `models.ini` delegates `llama-server` preset flags verbatim and is preserved across upgrades.
4. **MCP Tool Signatures:**
   - Core specialist tools (`ask_digest`, `ask_coder_fast`, `ask_coder`, `ask_coder_long`, `ask_vision`, `ask_vision_deep`, `ask_reasoner`, `ask_analyst`, `ask_generalist`, `ask_preset`, `ask_batch`, `get_bridge_status`) maintain backwards-compatible parameter names and defaults.
5. **Opt-In `[enterprise]` Extra — Provisional (Unvalidated in Production):**
   - The `[enterprise]` extra and its flags/endpoints (`--enterprise`, `--audit-log`, `/livez`, `/readyz`, `--shared-models-dir`, `--tenant`) are **Provisional (Unvalidated in Production)** until field deployment feedback is collected.
