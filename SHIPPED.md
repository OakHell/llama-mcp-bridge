# Shipped Releases — `llama-mcp-bridge`

For upcoming milestones, see [ROADMAP.md](ROADMAP.md). For the full issue and feature checklist, see [TRACKER.md](TRACKER.md).

### `v0.1.0` — Core Router, Hygiene & Cross-Platform Setup
Introduced 9 specialist MCP tools (`ask_digest`, `ask_coder_fast`, `ask_coder`, `ask_coder_long`, `ask_vision`, `ask_vision_deep`, `ask_reasoner`, `ask_analyst`, `ask_generalist`) routed to `llama-server`, alongside the output hygiene engine (`hygiene.py`) and `.gitignore`-aware file discovery. Added lazy `llama-server` process supervision (`supervisor.py`), cross-platform hardware profiling (`hardware.py`), VRAM-tier `models.ini` generation (`presets.py`), and the installer (`installer.py`) with `--mode` and `--install-service`.

### `v0.2.0` — Telemetry, TUI Wizard & Resumable Starter Packs
Added the `get_bridge_status` MCP tool, `ask_coder_long` context-budget telemetry footer, and the `rich`-powered interactive TUI setup wizard (`llama-mcp-install` / `--tui`). Implemented resumable `.part` GGUF starter-pack downloads with HTTP `Range` support and concurrent Hugging Face URL verification (`--verify-models` / `--tier`) with 404 fallback suggestions.

### `v0.3.0` — Integrity, Residency, Recovery & Diagnostics (Shipped)
Added `X-Linked-ETag` SHA-256 checksum verification for `.part` GGUF downloads, configurable memory residency profiles (`stingy`, `moderate`, `snappy`), and graceful OOM vs. cold-swap timeout classification with opt-in `fallback_on_oom = true` auto-retry on `digest`. Shipped `llama-mcp-ps` / `--ps`, `--doctor`, and `--configure-client` for live diagnostics and zero-mutation harness onboarding.

### `v0.4.0` — Dedicated Model Management (`llama-mcp-models`) & Selective Presets (Shipped)
Added the dedicated `llama-mcp-models` CLI (`models_cli.py`) with an interactive checkbox TUI, scripted `list`/`add`/`remove` subcommands, deduplicated `models.ini` merging, and safe shared-GGUF removal (`remove_models_from_ini()`). Enforced `n-gpu-layers = 0` and `device = none` on CPU-only profiles, flattened nested prebuilt release directories with symlinks intact, added `--uninstall` cleanup, and added multi-release fallback for `llama.cpp` prebuilt assets.

### `v0.5.0` — Phase 1: Cross-Platform Binary Extraction & Runtime Libraries (Shipped)
Rejected `cudart-llama-*.zip` runtime-only archives in `_select_llama_release_asset()` and automatically fetched and co-located companion `cudart` DLLs alongside `llama-server.exe` on Windows NVIDIA setups. Preserved companion `.dylib` and `.metallib` files on macOS and ran ad-hoc `codesign --force --deep -s -` on extracted Mach-O binaries.

### `v0.5.1` — Phase 2: Native Background Service Lifecycles (Windows & macOS) (Shipped)
Hardened Windows Task Scheduler registration with `/F`, quoted `/TR` paths containing spaces, and windowless launch via `conhost.exe --headless`. Migrated macOS `launchd` management from `launchctl load/unload` to `launchctl bootstrap gui/<uid>` / `bootout` with `WorkingDirectory` and `LLAMA_MCP_HOME` environment variables in the `.plist`.

### `v0.5.2` — Phase 3: Windows Interactive TUI & MCP Client Path Resolution (Shipped)
Added Windows `msvcrt.getch()` two-byte arrow prefix handling (`b"\xe0"` / `b"\x00"` + `H`/`P`) in `_read_key()` / `select_presets_interactive()`. Resolved `"command"` in `mcp_config.json` snippets to the full `.exe`/binary path on Windows or when off `PATH`.

### `v0.5.3` — Source Build Divergence & Documentation Sync (Shipped)
Diverged `--mode source-hermetic` (static `-DBUILD_SHARED_LIBS=OFF -DLLAMA_CURL=OFF` in a scrubbed environment with `uvx cmake` fallback) from `--mode source-system` (dynamic `-DBUILD_SHARED_LIBS=ON` with host toolchains and shared library copying). Synced `README.md` CLI flags, source build caveats, and Roadmap summary.

### `v0.6.0` — Unconstrained Hugging Face & Local GGUF Models (`llama-mcp-models`) (Shipped)
Added `--hf <owner>/<repo>[:<quant-or-filename>]` and `--file <path/to/model.gguf>` support to `llama-mcp-models add`, dynamic `ask_preset` / `ask_<preset>` MCP tool routing, and global workspace registration (`--register` / `--unregister` active home pointer). Emitted generic baseline tuning notices and MCP client restart reminders when modifying custom `[preset]` sections.

### `v0.7.0` — GGUF Binary Header Heuristics & Auto-Tuning (Shipped)
Implemented zero-dependency GGUF v2/v3 binary header inspection (`gguf_header.py`) supporting GQA, SWA patterns, hybrid SSM state, DeepSeek MLA, and `kv_shared_layers` without reading tensor weights. Wired `--tune-mode conservative|approximate` into `llama-mcp-models add` for VRAM/RAM KV-cache auto-sizing.

### `v0.8.0` — Agent Self-Bootstrap Skill (`SKILL.md`) (Shipped)
Shipped the cross-harness `skills/llama-mcp-setup/SKILL.md` bundle inside the wheel via Hatchling `force-include` with a deterministic bootstrap and black-box diagnostic workflow. Added `llama-mcp-install --install-skill [TARGET]` (`--skill-link` optional) supporting `auto`, `claude`, `antigravity`, `gemini`, `cursor`, `workspace`, and custom paths.

### `v0.8.1` — Dynamic Tool Filtering, Zero-Handholding Bootstrap & GUI `PATH` Resilience (Shipped)
Filtered `tools/list` at startup to expose only configured built-in specialists plus `get_bridge_status`, `ask_preset`, and custom `ask_<preset>` tools, and added `--tool-name` to `llama-mcp-models add`. Resolved full executable paths on POSIX and Windows, added `--configure-client <harness> --apply` (plus `--uninstall` cleanup), preserved existing `models.ini` on `--install-service`, reported `STANDBY (auto-spawn)` in `--doctor`/`llama-mcp-ps`, and hardened `SKILL.md` for POSIX and Windows non-login shells.

### `v0.9.0` — Security, Cross-Platform Parity & README Architecture (Shipped)
Hardened `_extract_archive()` against Zip Slip / Tar path traversal, symlink escapes, and oversized/zip-bomb archives (`S1`, `S2`, `M7`), added 64-bit Windows display-adapter registry VRAM probing (`S3`), enforced absolute-path `@reboot` crontab validation (`S5`, `B6`), and made `--apply` opt-in in `SKILL.md` (`S4`, `D22`–`D24`). Fixed AMD ROCm scoring (`B1`, `B2`), BSD GPU detection (`B3`), `/etc/os-release` distro detection (`B4`), moved `models_dir` rewriting (`B7`), `xcode-select -p` (`B8`), `models.ini`-missing tool fallback (`B9`, `B10`), image OOM fallback notice (`B11`), missing `cmake`/`uvx` error (`B12`), `llama-server --version` doctor check (`M10`), dead-code removal (`N12`), and rewrote `README.md` (`D1`–`D19`).
