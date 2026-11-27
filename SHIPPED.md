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

### `v0.9.0.post1` — FreeBSD CI Caching & `--install-service --dry-run` Fix (Shipped)
Allowed `llama-mcp-install --install-service --dry-run` to print the `@reboot` crontab preview without a pre-installed `llama-server` binary while keeping the strict `ValueError` guard for `dry_run=False`, and enabled `cache-after-prepare: true` on the FreeBSD 15 VM CI job (`T24`).

### `v0.9.0.post2` — FreeBSD 15 CI `python312` + `rust` Package Restoration (Shipped)
Restored the `pkg install -y python312 uv rust py312-*` prebuilt binary block in the `freebsd` CI `prepare:` step alongside `cache-after-prepare: true` so `watchfiles` and runtime dependencies build/install cleanly on FreeBSD 15.

### `v0.10.0` — Modular Architecture, Medium Polish & Community Docs (Shipped)
Split `installer.py` into `service_install.py`, `model_download.py`, `client_config.py`, and `skill_install.py` (`C16`), extracted file discovery into `discovery.py` (`C17`), consolidated image data-URI encoding (`C1`, `M3`, `N5`) and preset definitions (`C18`, `C19`), and added PEP 561 `py.typed` (`C2`), `__all__` exports (`C10`), and CI `mypy` type-checking (`C3`, `C15`). Hardened harness cleanup across all 6 file-based harnesses (`M1`, `M2`, `M8`), added tier-aware custom preset defaults & quant heuristics (`M4`, `M5`, `M15`), mtime-cached `load_config()` (`M17`, `N4`), process-cached hardware error classification (`M16`), `--json` and workspace surfacing on `--doctor` / `--ps` (`M11`, `M12`, `N7`–`N9`), `--version`/`--log-level`/`server_log` (`C4`–`C7`, `C9`), community docs (`SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `ARCHITECTURE.md`, `docs/`, issue/PR templates, `Makefile`, `.pre-commit-config.yaml`), and 25 new tests (`T12`–`T14`, `T16`–`T19`, `T21`–`T22`).

### `v0.11.0` — Ollama Import, Diagnostics & Config Introspection (Shipped)
Added zero-copy Ollama manifest parsing and GGUF/mmproj blob symlinking (`ollama_import.py`, `--import-ollama` / `--ollama-dir`, `F5`) with GGUF header auto-tuning, split-blob rejection, and safetensors warnings. Shipped `--verify-all` (`F3`), 1-hour TTL caching for `--verify-models` (`F4`, `--no-cache`), `--doctor --verify-sha` (`F9`), `llama-mcp-models --prune` (`F10`), `--profile` benchmarking (`F11`), `--selftest` (`F16`), `llama-mcp-config check <path>` (`F18`, `config_cli.py`), `--export-config` / `--diff-config` (`F8`), `--model-info` (`F19`), `--check-update` (`F21`), opt-in `@pytest.mark.live_server` testing (`T23`), and optional FreeBSD `--selftest` CI wiring (`T25`).

### `v0.11.0.post1` — Windows NTFS `load_config()` Cache-Key Fix (Shipped)
Included `bridge.ini` raw file bytes alongside `st_mtime_ns` in `load_config()`'s cache key (`config.py`) so rapid sub-15.6 ms overwrites on Windows NTFS never return stale cached `BridgeConfig` instances, and added a regression test pinning identical `st_mtime_ns` across mutations.

### `v0.12.0` — Streaming, Hot-Reload, Auth, Metrics & Multi-Workspace (Shipped)
Added opt-in SSE streaming (`stream=True`, `F1`), `models.ini` hot-reload with `notifications/tools/list_changed` (`F2`), parallel `ask_batch` tool bounded by `parallel` slots (`F6`), `notifications/progress` heartbeats after 30s (`F7`), `allowed_presets` access control (`F14`), and per-call `timeout_s` overrides (`F22`). Shipped `--api-key` / `LLAMA_MCP_API_KEY` Bearer auth (`F12`), Prometheus `/metrics` & `/healthz` sidecar (`metrics.py`, `F13`), named multi-workspace pointers (`--register-name` / `--workspace-name`, `F15`), `--warmup` preset preloading (`F17`), and `cosign`/`gpg` release signature verification (`signing.py`, `F20`).

### `v0.14.0` — Opt-In `[enterprise]` Extra (`E3`, `E7`, `E11`)
Added opt-in `[enterprise]` packaging extra (`--enterprise` / `enterprise_mode = true`), rotating JSONL audit logging (`enterprise/audit.py`, `E3`), `/livez` + `/readyz` + `systemd` `sd_notify` watchdog probes (`enterprise/probes.py`, `E7`), and multi-tenant workspace isolation with advisory file locks (`enterprise/tenant.py`, `E11`).
- **`v0.14.0.post2`:** Shortened `AF_UNIX` test socket path under `/tmp` for macOS/FreeBSD 104-byte `sun_path` compatibility.

### `v0.13.0` — Core Supply Chain, Telemetry & Resilience (Shipped)
Added `.github/workflows/release.yml` with `uv build`, CycloneDX 1.5 SBOM generation (`E5`), and Sigstore signing (`E6`), plus [STABILITY.md](STABILITY.md) (`E8`). Shipped `--log-format json` (`logging_utils.py`, `E1`), `--metrics` CLI alias (`E2`), `schema_version` + `--migrate-config` (`E4`), `ResilientHttpClient` exponential backoff & circuit breaker (`http_resilience.py`, `E9`), pre-flight disk-space & `ENOSPC`/`EMFILE`/thread-limit guards (`E10`), and fault-injection chaos tests (`E12`).
