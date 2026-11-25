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

---

## Shipped Releases — Cross-Platform Hardening (`v0.5.x`)

### `v0.5.0` — Phase 1: Cross-Platform Binary Extraction & Runtime Libraries (Shipped)
- **Windows CUDA Runtime Co-Location (`installer.py`)**: Rejects `cudart-llama-*.zip` runtime-only archives in `_select_llama_release_asset()` and automatically fetches + extracts companion `cudart` DLLs alongside `llama-server.exe` on Windows NVIDIA setups.
- **macOS Runtime Libraries & Ad-Hoc Signing (`installer.py`)**: Preserves companion `.dylib` and `.metallib` files alongside `llama-server` when flattening extracted archives and runs `codesign --force --deep -s -` on extracted Mach-O binaries on Apple Silicon.

### `v0.5.1` — Phase 2: Native Background Service Lifecycles (Windows & macOS) (Shipped)
- **Windows (`_generate_service_file()`)**: Passes `/F` to `schtasks /Create`, wraps `/TR` batch paths containing spaces in quotes, and launches `start-llama-server.bat` windowlessly via `conhost.exe --headless`.
- **macOS (`_generate_service_file()` & `_uninstall_bridge()`)**: Migrated from legacy `launchctl load/unload` to `launchctl bootstrap gui/<uid>` / `bootout`, and includes `<key>WorkingDirectory</key>` plus `<key>EnvironmentVariables</key>` (`LLAMA_MCP_HOME`) in the `.plist`.

### `v0.5.2` — Phase 3: Windows Interactive TUI & MCP Client Path Resolution (Shipped)
- **Windows TUI Key Handling (`models_cli.py`)**: Handles Windows `msvcrt.getch()` two-byte arrow prefixes (`b"\xe0"` / `b"\x00"` + `H`/`P`) in `_read_key()` / `select_presets_interactive()`.
- **MCP Client Binary Resolution (`installer.py`)**: Resolves `"command"` in `mcp_config.json` snippets to the full `.exe`/binary path on Windows or when `llama-mcp-bridge` is off `PATH`, while keeping `"llama-mcp-bridge"` when resolved on POSIX `PATH`.

### `v0.5.3` — Source Build Divergence & Documentation Sync (Shipped)
- **`source-hermetic` vs. `source-system` (`installer.py`)**: `source-hermetic` builds a static binary (`-DBUILD_SHARED_LIBS=OFF -DLLAMA_CURL=OFF`) in a scrubbed environment with `uvx cmake` fallback, whereas `source-system` builds dynamically (`-DBUILD_SHARED_LIBS=ON`) with host toolchains and copies built shared libraries into `<workspace>/bin/`.
- **Docs Sync (`README.md`)**: Updated CLI flags, source build caveats (Windows host compilers, macOS Gatekeeper/`codesign`), and the Roadmap summary.

---

## Upcoming Milestones

### `v0.6.0` — Unconstrained Hugging Face & Local GGUF Models (`llama-mcp-models`)
- **Arbitrary HF Repo & Local File Support (`llama-mcp-models add`)**: Support `llama-mcp-models add <preset> --hf <owner>/<repo>[:<quant-or-filename>]` (resolving `.gguf` and optional `mmproj` files via the Hugging Face API with resumable `.part` downloads) and `--file <path/to/model.gguf>`, allowing users either to override a built-in specialist slot or define a new custom `[preset]` section in `models.ini`.
- **Generic Baseline Warning & Client Restart Reminder**: Emit a clear CLI notice when adding an uncurated Hugging Face or local GGUF model explaining that a generic hardware-tier `models.ini` entry has been generated and manual tuning (`ctx-size`, KV cache quantisation, GPU layers) in `models.ini` is up to the user, and remind the user whenever `llama-mcp-models add` or `remove` modifies custom `[preset]` sections to restart active MCP clients (Claude Code, Cursor, Cline, Antigravity) so `tools/list` refreshes.
- **Dynamic MCP Tool Routing**: Expose custom `models.ini` sections to MCP clients via a generic `ask_preset` tool or dynamically registered `ask_<preset>` tools at bridge startup.

### `v0.7.0` — GGUF Binary Header Heuristics & Auto-Tuning
- **Zero-Dependency GGUF Header Inspection**: Parse binary header metadata (`general.architecture`, `*.block_count`, `*.attention.head_count_kv`, `*.context_length`) from the first few kilobytes of downloaded or local `.gguf` files without loading model weights.
- **KV-Cache & VRAM Auto-Sizing (`--tune-mode`)**: Compute exact KV-cache memory per token against detected hardware VRAM/RAM to estimate safe `n-gpu-layers` and `ctx-size` values for any model architecture, exposed via `--tune-mode conservative|approximate` on `llama-mcp-models add` (with an explicit heuristic-tuning disclaimer).

### `v0.8.0` — Agent Self-Bootstrap Skill (`SKILL.md`)
- **Cross-Harness `SKILL.md` Bundle (`skills/llama-mcp-setup/SKILL.md`)**: Ship a standard Agent Skills folder with YAML frontmatter and a deterministic setup/diagnostic workflow (`detect hardware/workspace -> llama-mcp-install --yes -> llama-mcp-install --doctor -> wire mcpServers -> restart harness`) plus a black-box troubleshooting tree (`--doctor` / `llama-mcp-ps`) so Claude Code, Google Antigravity, Cursor, and Gemini CLI can autonomously bootstrap local specialist models.
- **One-Command Skill Installation (`llama-mcp-install --install-skill [harness]`)**: Copy or symlink the bundled `llama-mcp-setup` skill into the target harness's global or workspace skill directory (`~/.claude/skills/`, `~/.gemini/config/skills/`, `~/.gemini/skills/`, `~/.cursor/skills/`, or `.agents/skills/`).
