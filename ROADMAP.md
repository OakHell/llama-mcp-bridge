# Roadmap — `llama-mcp-bridge`

For shipped milestones (`v0.1.0`–`v1.0.1`), see [SHIPPED.md](SHIPPED.md) and [CHANGELOG.md](CHANGELOG.md). For the active issue and feature checklist, see [TRACKER.md](TRACKER.md).

## Current Release

### `v1.0.1` — Stability & Auto-Spawn Patch
- Active `1.x` stability contract ([STABILITY.md](STABILITY.md)) across CLI entrypoints, `--json` schemas, `bridge.ini` `schema_version = 1`, and core MCP tools (`[enterprise]` remains **Pre-1.0 Provisional**).

## Post-`v1.0.1` Themes (Tracked in [TRACKER.md](TRACKER.md))

- **Bug & Security Hardening:** Remaining `B14`–`B22` and `S8`–`S9` items (download preflight/retry, `</file>` prompt escaping, Windows `msvcrt.locking` empty-file handling).
- **Test & CI Coverage:** Expanding `tests/test_model_download.py`, `tests/test_enterprise.py`, and `tests/test_discovery.py` (`T28`–`T29`).
- **Field Validation:** Gathering staging/production feedback on `[enterprise]` (`E3`, `E7`, `E11`) before graduating it into the `1.x` SemVer contract.
