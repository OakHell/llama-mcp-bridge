# Documentation Index

- [install.md](install.md) — Parallel installation paths (TUI wizard, non-interactive CLI, agent bootstrap).
- [install-wheel.md](install-wheel.md) — Pinned and offline/air-gapped release wheel installation.
- [install-bsd.md](install-bsd.md) — FreeBSD/BSD `system-bin` setup and GPU notes.
- [cli.md](cli.md) — Full CLI flag reference (`llama-mcp-install`, `llama-mcp-bridge`, `llama-mcp-models`, `llama-mcp-config`, `Makefile`).
- [config.md](config.md) — `LLAMA_MCP_HOME`, `bridge.ini` (`schema_version = 1`), `models.ini`, and residency profiles.
- [tools.md](tools.md) — Specialist, Dispatcher (`ask_preset`, `ask_batch`), and Diagnostics (`get_bridge_status`) tools.
- [models.md](models.md) — Model management (`add`, `remove`, `--import-ollama`, `--prune`, `--verify-models`).
- [advanced.md](advanced.md) — Bearer auth, named workspaces, `--warmup`, `--profile`, and signature verification.
- [services.md](services.md) — OS-native autostart (`systemd`, `launchd`, Windows `schtasks`, `@reboot` `crontab`).
- [enterprise.md](enterprise.md) — Opt-in `--enterprise` features (`E3` audit logs, `E7` probes, `E11` multi-tenant isolation).
- [troubleshooting.md](troubleshooting.md) — Common issues, macOS `codesign`, Windows CUDA DLLs, and OOM recovery.
- [gguf-auto-tuning.md](gguf-auto-tuning.md) — GGUF v2/v3 binary header inspection and KV-cache sizing.
- [reasoning-management.md](reasoning-management.md) — `reasoning-format = deepseek` budgets and `hygiene.py` tag stripping.
- [failure-classification.md](failure-classification.md) — Server-log tail OOM vs. timeout classification and `fallback_on_oom`.
