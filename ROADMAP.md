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

### `v0.3.0` — Integrity, Residency, Recovery & Diagnostics (Shipped)

#### Milestone 3.1 — Download Integrity (`X-Linked-ETag` SHA-256 Checksums) ✅
- Verified completed `.part` GGUF downloads against Hugging Face's `X-Linked-ETag` SHA-256 digest before atomic rename.

#### Milestone 3.2 — Memory Residency Profiles (`stingy`, `moderate`, `snappy`) ✅
- Added configurable `residency` setting in `bridge.ini`, `models.ini`, and the TUI/CLI controlling `models-max` and `sleep-idle-seconds` (`15s` / `120s` / `600s`).

#### Milestone 3.3 — Graceful OOM & Cold-Swap Timeout Recovery ✅
- Added bounded stderr capture, structured OOM vs. cold-swap timeout classification, and opt-in `fallback_on_oom = true` auto-retry on `digest`.

#### Milestone 3.4 — Diagnostics & Safe Harness Onboarding (`--ps`, `--doctor`, `--configure-client`) ✅
- Added `llama-mcp-ps` / `--ps` for live loaded-preset inspection, `--doctor` for end-to-end environment checks, and `--configure-client` for zero-mutation MCP client onboarding.

---

### `v0.4.0` — Dedicated Model Management (`llama-mcp-models`) & Selective Presets (Shipped)
- **Dedicated `llama-mcp-models` CLI (`models_cli.py`)**: Interactive checkbox TUI (`Up`/`Down` or `j`/`k`, `Space` toggle, `a` all/none, `Enter` apply, plus non-TTY numbered fallback) and scripted subcommands (`list`, `add`, `remove`, `--add`, `--remove`, `-y`/`--yes`, `--dry-run`).
- **Selective Preset Generation & Deduplicated Merging (`presets.py`)**: Added `selected_presets` filtering to `generate_models_ini()`, `merge_models_ini()` to add or update presets without duplicate `[section]` headers, and `--presets` on `llama-mcp-install` (wired into `_run_tui_wizard()`).
- **Safe Shared-GGUF Removal (`remove_models_from_ini()`)**: Unlinks `.gguf` and `mmproj` files only when no remaining preset in `models.ini` references them.
- **CPU Backend Guard & Installer Polish**: Enforces `n-gpu-layers = 0` and `device = none` on CPU-only profiles, flattens nested prebuilt release directories with symlinks intact, supports `--uninstall` cleanup, and falls back across recent `llama.cpp` releases.
