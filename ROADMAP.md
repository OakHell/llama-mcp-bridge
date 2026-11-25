# Roadmap — `llama-mcp-bridge`

## Shipped Releases

### `v0.1.0` — Core Router, Hygiene & Cross-Platform Setup
- 9 specialist MCP tools (`ask_digest`, `ask_coder_fast`, `ask_coder`, `ask_coder_long`, `ask_vision`, `ask_vision_deep`, `ask_reasoner`, `ask_analyst`, `ask_generalist`) routed to `llama-server`.
- Output hygiene engine (`hygiene.py`) stripping internal monologue tags while preserving fenced code blocks, LaTeX, and structured content.
- `.gitignore`-aware file discovery with `!` negations, `include > ignore` precedence, binary skipping, and smallest-first context budgets.
- Lazy `llama-server` process supervision (`supervisor.py`) with `/health` polling and Windows/POSIX detachment.
- Cross-platform hardware profiler (`hardware.py`) and VRAM-tier `models.ini` preset generator (`presets.py`) for `6gb`, `8gb`, `12gb`, `16gb`, and `24gb+`.
- Cross-platform installer (`installer.py`) with `--mode` (`prebuilt`, `source-hermetic`, `source-system`, `system-bin`) and `--install-service` (`systemd`, non-systemd `@reboot` cron/OpenRC, `launchd`, Windows `.bat` + Task Scheduler, BSD `@reboot` cron/`rc.d`).

### `v0.2.0` — Telemetry, TUI Wizard & Resumable Starter Packs
- `get_bridge_status` MCP tool reporting server health, active VRAM tier, and live loaded/unloaded presets.
- `ask_coder_long` context-budget telemetry footer (`[Context Budget: X/Y files included, Z chars]`) with omitted path reporting.
- `rich`-powered interactive TUI setup wizard (`llama-mcp-install` / `--tui`) with guided BYO driver/SDK prerequisite checks and opt-in `llama-server` auto-installation.
- Resumable `.part` GGUF starter-pack downloader (`--download-models`) with HTTP `Range` support and Rich progress bars.
- Concurrent Hugging Face URL verification (`--verify-models` / `--tier`) with live GGUF search fallback on 404.

---

## Upcoming: `v0.3.0` Roadmap

### Milestone 3.1 — Download Integrity (`X-Linked-ETag` SHA-256 Checksums)
- Verify completed `.part` GGUF downloads against Hugging Face's `X-Linked-ETag` SHA-256 digest before atomic rename.
- Surface clear mismatch errors and preserve `.part` files only when safe to resume.

### Milestone 3.2 — Memory Residency Profiles (`stingy`, `moderate`, `snappy`)
- Add a configurable `residency` setting in `bridge.ini`, `models.ini`, and the TUI/CLI controlling `models-max` and idle eviction speed (`15s` / `120s` / `600s`).

### Milestone 3.3 — Graceful OOM & Cold-Swap Timeout Recovery
- Structured detection of VRAM OOM vs. cold-swap timeouts in `supervisor.py` and `bridge.py` with actionable recovery hints.
- Optional `fallback_on_oom = true` auto-retry on `digest` when a heavier preset exhausts VRAM.

### Milestone 3.4 — Diagnostics & Safe Harness Onboarding (`--ps`, `--doctor`, `--configure-client`)
- `llama-mcp-ps` / `--ps` for Ollama-style live loaded-preset inspection.
- `--doctor` for end-to-end verification of `bridge.ini`, `models.ini`, `llama-server` binary, GGUF files, and `/health`.
- `--configure-client` for zero-mutation MCP client onboarding: prints the ready-to-paste `mcpServers` JSON snippet and points directly to the official MCP setup documentation URL for the user's chosen coding harness (Claude Code, Cursor, Cline, Windsurf, Zed, Antigravity) without modifying external config files.
