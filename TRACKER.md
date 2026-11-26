# Issue & Feature Tracker — `llama-mcp-bridge`

## P0 — Security & Correctness
- [ ] **S1 — Archive extraction path-traversal defense (`installer.py`):** `_extract_archive` uses `zipfile.extractall` and `tarfile.extractall`, which will write outside `dest_dir` if an archive contains `../` entries or absolute paths. Validate every archive member's resolved target path stays strictly inside `dest_dir` before extraction.
- [ ] **S2 — Archive extraction size and entry-count bounds (`installer.py`):** Enforce configurable ceilings on total extracted bytes (e.g. `2 GiB`) and total file count (e.g. `5,000` entries) inside `_extract_archive` to prevent zip bombs or corrupted release archives from exhausting disk/inodes.
- [ ] **S3 — Windows WMI `AdapterRAM` >4 GB overflow (`installer.py`):** `_probe_windows_wmi` reads `Win32_VideoController.AdapterRAM` (`uint32`), which overflows/wraps at 4 GB and misclassifies 8 GB–24 GB AMD/Intel GPUs as `6gb`. Query 64-bit `HardwareInformation.qwMemorySize` from the Windows display adapter registry (`HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\000*`) when `nvidia-smi` is absent, falling back to `AdapterRAM` only if unavailable.
- [ ] **S4 — Make `--apply` opt-in in `SKILL.md` (`skills/llama-mcp-setup/SKILL.md`):** Step 3 currently instructs agents to run `--configure-client <harness> --apply` by default. Restate Step 3 so the agent prints the `mcpServers` JSON snippet (`llama-mcp-install --configure-client <harness>`) by default and only runs `--apply` when the user explicitly authorizes modifying their harness config.
- [ ] **S5 — Require absolute binary path for `@reboot` crontab (`installer.py`):** `_install_reboot_crontab` currently falls back to bare `"llama-server"` if `cfg.llama_server_bin` is unset. Cron runs with a minimal `PATH`, so a bare binary name can resolve to the wrong binary or fail silently. Refuse to install the `@reboot` cron entry unless `server_bin` resolves to an existing absolute path.

## P1 — User-Affecting Bugs
- [ ] **B1 — Parenthesize `_select_llama_release_asset` operator precedence (`installer.py`):** Explicitly parenthesize `(profile.gpu_vendor == "nvidia" and "cuda" in name) or (profile.gpu_vendor in ("amd", "intel") and "vulkan" in name)` so future scoring additions cannot silently break precedence.
- [ ] **B2 — Add AMD ROCm scoring branch (`installer.py`):** Add `rocm` / `hip` token scoring in `_select_llama_release_asset` for `profile.gpu_vendor == "amd"` alongside `vulkan` so AMD users get ROCm prebuilt assets when `llama.cpp` ships them.
- [ ] **B3 — Include BSD in GPU detection (`installer.py`):** Update `detect_hardware` from `elif os_name in ("linux", "windows"):` to include `"freebsd"` (and BSD variants where `nvidia-smi` is available) instead of falling through unconditionally to the CPU tier.
- [ ] **B4 — Replace `platform.release()` in `_detect_distro_family` (`installer.py`):** `platform.release()` returns the kernel version, not the OS distro. Replace with `platform.freedesktop_os_release()` (falling back to `/etc/os-release`).
- [ ] **B6 — Scope `_uninstall_bridge` crontab removal (`installer.py`):** Filter crontab lines strictly by `# llama-mcp-bridge` marker rather than removing any line containing `"llama-server"`, avoiding deletion of unrelated user cron jobs.
- [ ] **B7 — Handle moved `models_dir` in `models.ini` (`installer.py`):** When `--models-dir` is passed on an existing workspace (or via `--repair`), rewrite `[preset]` `model` and `mmproj` paths in `models.ini` to match the new `models_dir` so `--doctor` does not fail on stale paths.
- [ ] **B8 — Fix macOS Xcode CLT check (`installer.py`):** Replace `shutil.which("xcode-select")` (which always succeeds at `/usr/bin/xcode-select`) in `_check_hardware_prerequisites` with `xcode-select -p` checking exit status `0`.
- [ ] **B9 — Safe fallback when `models.ini` is missing (`bridge.py`):** In `_register_configured_tools`, when `models.ini` does not exist, log a warning and register only `get_bridge_status` and `ask_preset` instead of registering all 9 unconfigured built-in tools.
- [ ] **B10 — Guard FastMCP internal tool-clearing (`bridge.py`):** Guard `getattr(srv, "local_provider", None)._components` clearing in `_register_configured_tools` with an explicit check and comment so FastMCP internal API changes fail loudly rather than silently duplicating tools.
- [ ] **B11 — Explicit image-drop notice on vision OOM fallback (`bridge.py`):** When an image request falls back to text-only `digest` on OOM, update the notice string to explicitly state `"(text-only; images dropped)"`.
- [ ] **B12 — Clear error when `cmake` and `uvx` are both missing (`installer.py`):** Raise `RuntimeError("cmake not found; install cmake or uvx, or use --mode prebuilt")` before `subprocess.run` instead of letting `["cmake"]` fail with an opaque `FileNotFoundError`.

## P2 — Medium Edge Cases
- [ ] **M1 — Expand `_cleanup_harness_mcp_configs` to all 7 harnesses (`installer.py`):** Mirror `_resolve_harness_config_paths` so `cline`, `windsurf`, and `zed` are cleaned up on `--uninstall` alongside `antigravity`, `claude-code`, and `cursor`.
- [ ] **M2 — Delete empty `mcpServers` object on cleanup (`installer.py`):** Remove the parent `"mcpServers"` key from harness JSON configs when removing `"local-ai"` leaves `"mcpServers": {}` empty.
- [ ] **M3 — Consolidate duplicate image data-URI encoders (`bridge.py`):** Merge `_encode_image_data_uri` and `_encode_image_to_data_uri` into a single helper.
- [ ] **M4 — Inherit tier defaults in `merge_custom_preset_ini` (`presets.py`):** Populate `ctx-size` and `n-gpu-layers` from the detected hardware tier defaults when adding custom presets instead of hardcoding `8192` / omitting `n-gpu-layers`.
- [ ] **M5 — Tier-aware quant heuristic in `resolve_hf_gguf_repo` (`presets.py`):** Select default GGUF quantisation preference using the detected hardware tier rather than always picking `Q4_K_M`.
- [ ] **M6 — Document basename stripping in `remove_models_from_ini` (`presets.py`):** Add a docstring/comment clarifying that `.name` restricts file deletion strictly to `models_dir` basenames.
- [ ] **M7 — Validate archive symlink targets in `_extract_archive` (`installer.py`):** Reject or bound symlinks inside downloaded archives whose resolved targets point outside `dest_dir` before `_flatten_extracted_dir` moves them.
- [ ] **M8 — Detect VS Code Insiders and VSCodium for `cline` (`installer.py`):** Extend `_resolve_harness_config_paths("cline")` to detect `Code - Insiders` and `VSCodium` globalStorage directories in addition to `Code`.
- [ ] **M9 — Document/harden `_resolve_bundled_skill_path` fallback (`installer.py`):** Prefer `importlib.resources` and document the `parents[2]` repo-root fallback assumption (or log a debug notice when used).
- [ ] **M10 — Validate `llama-server --version` in `_run_doctor` (`installer.py`):** Run `subprocess.run([srv_bin, "--version"], timeout=5)` in `_run_doctor` to verify the binary actually executes rather than only checking file permissions.
- [ ] **M11 — Cross-check `models.ini` presets against registered MCP tools in `_run_doctor` (`installer.py`):** Verify every `[preset]` in `models.ini` has a corresponding registered `ask_<preset>` tool.
- [ ] **M12 — Check free disk space in `_run_doctor` (`installer.py`):** Check available disk space in `models_dir` before recommending `--download-models`.
- [ ] **M13 — Standardize HTTP timeout between `probe_model_metadata` and `verify_model_url` (`presets.py`):** Align `timeout=5` and `timeout=10` to a single consistent constant.
- [ ] **M14 — Expose `--max-workers` on `verify_tier_models` (`installer.py`):** Make the `ThreadPoolExecutor(max_workers=8)` worker count configurable via `--max-workers` to avoid Hugging Face rate limits.
- [ ] **M15 — URL-encode filenames in Hugging Face URLs (`presets.py`):** Use `urllib.parse.quote` on filenames when constructing `https://huggingface.co/{repo}/resolve/main/{filename}` URLs.
- [ ] **M16 — Cache `detect_hardware()` in error classification (`bridge.py`):** Cache the `SystemProfile` instead of spawning hardware probe subprocesses on every inference error.
- [ ] **M17 — Mtime-cache `load_config()` (`bridge.py`):** Cache parsed `bridge.ini` / `models.ini` keyed by file `st_mtime` instead of re-reading from disk on every tool call.
- [ ] **M18 — Restore `LLAMA_MCP_HOME` env state after TUI wizard (`installer.py`):** Avoid mutating `os.environ["LLAMA_MCP_HOME"]` permanently in-process after `_run_tui_wizard` exits.
- [ ] **M19 — Preserve key case in `_persist_server_bin` (`installer.py`):** Use `ConfigParser(default_section=None, interpolation=None)` with `optionxform = str` so mixed-case keys in `bridge.ini` round-trip intact.
- [ ] **M20 — Print change summary in `_run_tui_wizard` (`installer.py`):** Log what changed vs. what was preserved when re-running the TUI wizard on an existing workspace.

## P3 — Polish & Nits
- [ ] **N1:** Remove redundant `os.X_OK` check alongside `sys.platform == "win32"` in `_run_doctor` (`installer.py`).
- [ ] **N2:** Remove unused `server_url` field from `BridgeConfig` (`bridge.py`) or wire it up.
- [ ] **N3:** Remove unused `distro_family` field from `SystemProfile` (`installer.py`) or wire it up.
- [ ] **N4:** Derive `int_fields` and `bool_fields` in `load_config` (`bridge.py`) from `BridgeConfig` dataclass field annotations instead of hardcoding field names.
- [ ] **N5:** Clean up redundant local `base64` / `mimetypes` imports in `bridge.py` when consolidating image helpers (`M3`).
- [ ] **N6:** Add a note to the Windows PowerShell preflight in `skills/llama-mcp-setup/SKILL.md` for environments where `uv tool update-shell` has not yet been run.
- [ ] **N7:** Add `--json` machine-parseable output flag to `llama-mcp-install --doctor`.
- [ ] **N8:** Add `--json` machine-parseable output flag to `llama-mcp-ps` / `llama-mcp-install --ps`.
- [ ] **N9:** Surface the active workspace path and whether `LLAMA_MCP_HOME` is active in `--ps` and `--doctor` output.
- [ ] **N10:** Add `[project.urls]` (`Homepage`, `Repository`, `Issues`) to `pyproject.toml`.
- [ ] **N11:** Add PyPI `classifiers` to `pyproject.toml`.
- [ ] **N12:** Remove dead `_MODEL_LOCK` and unused `import threading` in `bridge.py`.

## Features
- [ ] **F1 — Optional streaming support:** Add opt-in streaming in `query_llama` and MCP tool handlers (`"stream": True`).
- [ ] **F2 — Hot-reload `models.ini` via `notifications/tools/list_changed`:** Emit MCP tool-list change notifications when `models.ini` updates without requiring a client restart.
- [ ] **F3 — `--verify-all` CLI flag:** Combine `--verify-models` and `--doctor` into a single deployment check.
- [ ] **F4 — 1-hour TTL cache for Hugging Face URL verification:** Cache `verify_tier_models` results locally for 1 hour to reduce repeated HF API calls.
- [ ] **F5 — `--import-ollama` manifest parser & blob symlinker (`ollama_import.py`):** Walk `~/.ollama/models/manifests/**`, parse JSON layers (`application/vnd.ollama.image.model` and `.projector`), **symlink** (never copy) matching `blobs/sha256-<hash>` files into `<workspace>/models/<model>-<tag>.gguf`, run `parse_gguf_header` on each symlinked blob to auto-tune `models.ini`, reject split multi-blob models clearly, and catch `Invalid GGUF magic` on safetensors-backed Ollama models with a warning.
- [ ] **F6 — `ask_batch` parallel prompt tool:** Batch N prompts against the same preset when `parallel > 1`.
- [ ] **F7 — MCP `notifications/progress` for long inference:** Emit progress notifications during calls exceeding 30 seconds.
- [ ] **F8 — `--export-config` and `--diff-config`:** Print effective merged `[*]` + `[preset]` config and diff current `models.ini` against freshly generated tier defaults.
- [ ] **F9 — `--doctor --verify-sha`:** Re-verify downloaded GGUF files on disk against Hugging Face `X-Linked-ETag` SHA-256 headers.
- [ ] **F10 — `llama-mcp-models --prune`:** Remove unreferenced `.gguf` and `mmproj` files in `models_dir` not claimed by any `[preset]` in `models.ini`.
- [ ] **F11 — `--profile` benchmarking mode:** Run a standard prompt suite across configured presets and report tokens/sec, latency, and VRAM usage.
- [ ] **F12 — Optional `--api-key` auth for `llama-server`:** Wire `llama-server --api-key` through `bridge.ini` and `Authorization: Bearer` headers.
- [ ] **F13 — Prometheus `/metrics` sidecar endpoint:** Expose request counts, latency histograms, model load events, and OOM counts.
- [ ] **F14 — Per-preset access control (`allowed_presets`):** Allow restricting callable presets per `bridge.ini` profile.
- [ ] **F15 — Named multi-workspace support (`--register-name`):** Support multiple named workspace pointers alongside the default global workspace.
- [ ] **F16 — `--selftest` end-to-end inference probe:** Run a live `ask_digest("ping")` smoke test against `llama-server` and verify a non-empty reply.
- [ ] **F17 — `--warmup` preset preloading:** Optionally preload configured presets on bridge/service startup.
- [ ] **F18 — `llama-mcp-config check <path>` CLI:** Validate `bridge.ini` and `models.ini` syntax/paths standalone.
- [ ] **F19 — `--model-info <preset>` CLI flag:** Display effective merged `llama-server` flags (`[*]` + `[preset]`) for a single preset.
- [ ] **F20 — GPG / sigstore signature verification for downloaded `llama-server` binaries.**
- [ ] **F21 — `--check-update` via Git tags:** Query `git ls-remote --tags https://github.com/OakHell/llama-mcp-bridge`, parse SemVer tags, and compare against installed `pyproject.toml` version.
- [ ] **F22 — Optional per-call `timeout_s` override** on MCP tool invocations.

## Chores
- [ ] **C1:** Consolidate `_encode_image_data_uri` and `_encode_image_to_data_uri` (`bridge.py`).
- [ ] **C2:** Add `src/llama_mcp_bridge/py.typed` PEP 561 marker.
- [ ] **C3:** Add `mypy` (or `pyright`) static type-checking job to `.github/workflows/`.
- [ ] **C4:** Add `--version` flag to `llama-mcp-install`, `llama-mcp-models`, and `llama-mcp-bridge`.
- [ ] **C5:** Add `--log-level` CLI flag and structured Python `logging` setup.
- [ ] **C6:** Make `llama-server.log` path configurable in `bridge.ini` (`server_log`).
- [ ] **C7:** Add `--dry-run` support to `--verify-models`.
- [ ] **C8:** Standardize on `Path.home()` instead of mixing `os.path.expanduser` across modules.
- [ ] **C9:** Protect `_register_configured_tools` with a re-entrant lock for thread safety.
- [ ] **C10:** Define explicit `__all__` exports in every module under `src/llama_mcp_bridge/`.
- [x] **C11:** Separate shipped history (`SHIPPED.md`) from forward-looking `ROADMAP.md` without bloating `README.md`.
- [ ] **C12:** Add `.pre-commit-config.yaml` configured for `ruff` and `mypy`.
- [ ] **C13:** Add `Taskfile.yml` or `Makefile` for common `test`, `lint`, `typecheck`, and `doctor` tasks.
- [ ] **C14:** Add `[project.urls]` and `classifiers` to `pyproject.toml`.
- [ ] **C15:** Add `mypy` and `types-requests` to `[dependency-groups] dev` in `pyproject.toml`.
- [ ] **C16:** Split `installer.py` (~1,500 lines) into focused submodules (`service_install.py`, `model_download.py`, `client_config.py`, `skill_install.py`).
- [ ] **C17:** Split file discovery (`_expand_target_files`, gitignore walking) out of `bridge.py` into `discovery.py`.
- [ ] **C18:** Consolidate `_PRESET_NAMES` in `installer.py` with `_VALID_PRESETS` in `presets.py`.
- [ ] **C19:** Derive `_VALID_PRESETS` directly from `base_config` keys in `generate_models_ini` (`presets.py`).
- [ ] **C20:** Add `ARCHITECTURE.md` (or module call-graph diagram in `README.md`).
- [ ] **C21:** Add `docs/` deep-dive guides (reasoning-budget stack, GGUF header auto-tuning, stderr failure classifier).

## Testing Gaps
- [ ] **T1:** Unit test `_select_llama_release_asset` with AMD ROCm release assets.
- [ ] **T2:** Unit test `detect_hardware` on BSD (`sys.platform = "freebsd14"`).
- [ ] **T3:** Unit test `_probe_windows_wmi` with >4 GB VRAM (64-bit registry path vs. 32-bit `AdapterRAM` overflow).
- [ ] **T4:** Unit test `_extract_archive` path-traversal rejection (Zip Slip and Tar `../` traversal).
- [ ] **T5:** Unit test `_extract_archive` zip-bomb / max-bytes and max-entry-count enforcement.
- [ ] **T6:** Unit test `_extract_archive` symlink target escape rejection.
- [ ] **T7:** Unit test `remove_models_from_ini` with traversal-like preset model paths (`model = /etc/passwd`).
- [ ] **T8:** Concurrency test for simultaneous `_register_configured_tools` calls.
- [ ] **T9:** Unit test `_configure_client --apply` when target JSON file contains a top-level list instead of a dict.
- [ ] **T10:** Unit test `probe_model_metadata` timeout handling.
- [ ] **T11:** Unit test `_find_llama_server` when configured `llama_server_bin` path does not exist.
- [ ] **T12:** Unit test relative `workspace` path resolution in `bridge.ini` (`workspace = ./local`).
- [ ] **T13:** Unit test `_discover_ancestor_gitignores` with directory symlink loops.
- [ ] **T14:** Unit test `_walk_dir_pruned` with directory symlink loops.
- [ ] **T15:** Unit test `query_llama` when `models.ini` is deleted at runtime.
- [ ] **T16:** Unit test `_persist_server_bin` round-trip preservation of existing `bridge.ini` sections and key case.
- [ ] **T17:** Property-based tests for `_expand_target_files` precedence (`include > gitignore > ignore > defaults`).
- [ ] **T18:** Property-based tests for `_build_prompt_with_files` character budget and smallest-first sorting.
- [ ] **T19:** Expand fuzz/chaos tests for `clean_mcp_output` with nested code blocks, LaTeX, and malformed reasoning tags.
- [ ] **T20:** Unit test `_run_doctor` with `auto_spawn = false` and server offline (expects `WARN`, not `FAIL` or `Standby`).
- [ ] **T21:** Unit test `_uninstall_bridge` across `launchd`, Windows `schtasks`, `systemd`, and `crontab`.
- [ ] **T22:** Integration test running `--configure-client --apply` followed by MCP `initialize` and `tools/list`.
- [ ] **T23:** Optional end-to-end test against a real `llama-server` binary.

## Documentation Gaps
- [ ] **D1:** Add a 3–4 sentence **Design Stance** paragraph in `README.md` right after the intro (Ollama ease + `llama.cpp` transparency; hardware/geometry-derived tuning; agency-free specialist boundary; small composable primitives).
- [ ] **D2:** Add an explicit **Tradeoffs & Non-Goals** section in `README.md` (no streaming, reload required for new `ask_<name>` discovery in `tools/list`, HF-only curated set, AMD prebuilt Vulkan default, single `llama-server` process, CPU-only `6gb` tier latency).
- [ ] **D3:** State in `README.md` that `models.ini` is `llama-server`'s native router-mode preset file (full `llama-server` flag surface delegated verbatim).
- [ ] **D4:** Distinguish `models.ini` (`llama.cpp`'s preset contract) from `bridge.ini` (the bridge's orchestration contract) in `README.md`.
- [ ] **D5:** Document the **Reasoning Management** stack in `README.md` (`reasoning-format = deepseek`, per-role `reasoning-budget` table verified from `presets.py`, `reasoning-budget-message` + deterministic stripping in `hygiene.py`).
- [ ] **D6:** Document in `README.md` that `gguf_header.py` parses six attention layouts (MHA/GQA, per-layer GQA variance, SWA-alternating, hybrid SSM state overhead, DeepSeek MLA, and KV-shared layers).
- [ ] **D7:** State in `README.md` that specialist tools are **agency-free** (no function calling, no subprocesses, no network egress; file/image access is brokered by the bridge within `.gitignore` and context budgets).
- [ ] **D8:** State in `README.md` that OOM vs. timeout classification is **server-side** from captured `llama-server` `stderr`, not HTTP error bodies.
- [ ] **D9:** Describe `fallback_on_oom` in `README.md` as a structured degradation loop (retry text prompt on `digest` + prepend fallback notice).
- [ ] **D10:** Document the **Config-as-Contract / Tinkerability** guarantees in `README.md` (hand-edited sections preserved across re-installs and `--install-service`, reference-counted GGUF removal, `--uninstall` preserves `.ini`/`.gguf`, `--dry-run`, `--doctor`).
- [ ] **D11:** Document the 3-OS GitHub Actions CI matrix (`ubuntu-latest`, `macos-latest`, `windows-latest`) in `README.md` Development.
- [ ] **D12:** State the Python `>=3.11` requirement in `README.md`.
- [ ] **D13:** Document `--verify-models` self-healing Hugging Face search suggestions on 404 in `README.md`.
- [ ] **D14:** Mention Hugging Face `X-Linked-ETag` SHA-256 checksum verification in `README.md` Model Management.
- [ ] **D15:** Document in `README.md` that `ask_preset` and custom `ask_<name>` tools also support `json_schema` grammar constraints.
- [ ] **D16:** Add a **Model Family** column to the Specialist Tools table in `README.md` (noting cross-family diversity decorrelates role failures).
- [ ] **D17:** Clarify in `README.md` that client reload is only for `tools/list` discoverability, while `ask_preset` can call newly added presets immediately.
- [ ] **D18:** Expand custom model auto-tuning description in `README.md` (`resolve_hf_gguf_repo` `mmproj` selection + GGUF header geometry tuning via `--tune-mode`).
- [ ] **D19:** Surface `LLAMA_MCP_HOME` as a first-class environment variable option in `README.md` Configuration.
- [ ] **D20:** Add a **Contributing** section in `README.md` linking to `CONTRIBUTING.md`.
- [ ] **D21:** Add a concise **Troubleshooting / FAQ** section in `README.md`.
- [ ] **D22:** Warn in `SKILL.md` that `--apply` modifies the user's harness config and requires explicit user authorization (`S4`).
- [ ] **D23:** Mention `--verify-models` in `SKILL.md` black-box troubleshooting.
- [ ] **D24:** Clarify in `SKILL.md` that client restart refreshes `tools/list` discoverability while `ask_preset` works immediately.
- [x] **D25:** Create `SHIPPED.md` for shipped history (and `CHANGELOG.md` when breaking SemVer changes occur).
- [ ] **D26:** Create `SECURITY.md` with vulnerability reporting scope and process.
- [ ] **D27:** Create `CONTRIBUTING.md` (dev environment, `ruff`, `pytest`, PR guidelines).
- [ ] **D28:** Create `CODE_OF_CONDUCT.md`.
- [ ] **D29:** Create `.github/ISSUE_TEMPLATE/` (bug report, feature request) and `.github/PULL_REQUEST_TEMPLATE.md`.
- [ ] **D30:** Create `ARCHITECTURE.md` with module call graph and data-flow diagram.

## Deferred / Long-Term (Enterprise Positioning)
- [ ] **E1:** Structured JSON logging mode (`--log-format json`).
- [ ] **E2:** Prometheus `/metrics` sidecar endpoint (`--metrics`).
- [ ] **E3:** Rotating JSONL audit log of tool invocations.
- [ ] **E4:** Config schema versioning and `--migrate-config`.
- [ ] **E5:** CycloneDX SBOM generation (`uv export`) per release tag.
- [ ] **E6:** Sigstore / cosign release artifact signing.
- [ ] **E7:** Readiness vs. liveness healthcheck probes (`systemd` `WatchdogSec` / Kubernetes).
- [ ] **E8:** Formal SemVer CLI/tool stability policy.
- [ ] **E9:** Exponential backoff and circuit breaker for transient GitHub/Hugging Face `5xx` errors.
- [ ] **E10:** Explicit resource-exhaustion handling (`EMFILE`, disk-full `ENOSPC`, thread limits).
- [ ] **E11:** Multi-user / multi-tenant shared workspace isolation.
- [ ] **E12:** Fault-injection chaos suite (network partition, disk full, port conflict, mid-download kill).

## Retracted / Not-a-Bug Ledger (Reference Only)
- **B5 (Retracted):** `_MODEL_LOCK` in `bridge.py` is never acquired in `query_llama` (dead variable tracked as `N12`); `parallel = N` is not serialized by the bridge.
- **C22 (Retracted):** Project runs under `uv`, which already enforces `requires-python = ">=3.11"`.
- **Windows `os.access(..., os.X_OK)` (Not a bug):** Works on Windows; redundant check tracked as `N1`.
- **`remove_models_from_ini` path traversal (Not a bug):** `.name` strips directories; documentation tracked as `M6`.
- **`_install_reboot_crontab` line filter (Not a bug):** Properly scoped to `# llama-mcp-bridge` (uninstall bug is tracked separately as `B6`).
