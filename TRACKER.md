# Issue & Feature Tracker — `llama-mcp-bridge`

> **Release Ledger:** Items marked `[x]` shipped in `v1.0.0` / `v1.0.1` (see `SHIPPED.md` and `CHANGELOG.md`). Open `[ ]` items below represent the unified code, test, and documentation backlog.

---

## Open — P0 (Critical Security, User-Path Breakage & README First-Screen)
- [x] **B13 — 24 GB+ `--presets coder` / `--presets coder-long` download vs. `models.ini` mismatch (`presets.py`):**
- [x] **S6 — `include` patterns bypass `DEFAULT_IGNORES` (`discovery.py`):**
- [x] **S7 — Symlink target `chmod` traversal in `resolve_tenant_workspace` (`enterprise/tenant.py`):**
- [x] **D31 — Rewrite README opening pitch (`README.md`, D-01):** Replace the three-clause technical opening with a two-sentence elevator pitch naming the target user ("Ollama users who've outgrown Ollama's opinionated defaults without wanting to hand-edit `llama-server` flags").
- [x] **D32 — Add first-person origin story section (`README.md`, D-02):** Add the "Why I built this" narrative (~250 words covering Ollama token savings, conservative reasoning-block stripping, `.gitignore`-brokered file attachment, and GGUF-header-derived tuning) right after the pitch, replacing the abstract "Design Stance" prose.
- [x] **D33 — Hoist Quick Start install commands to the first screen (`README.md`, D-03):** Move the 3-command Quick Start immediately after the pitch + origin story so visitors don't scroll past ~1,200 words of orientation before seeing how to install.
- [x] **D34 — Add condensed "Who this is for / not for" section (`README.md`, D-04):** Move "Tradeoffs & Non-Goals" from position 15 to position 4–5, condensed to 3–4 crisp bullets each.
- [x] **D35 — Document the pinned release-wheel / offline install path (`README.md`, `docs/install-wheel.md`, D-05):** Add the reproducible release-wheel install path as a fourth parallel installation option and link `docs/install-wheel.md` from Quick Start.

---

## Open — P1 (High-Impact Reliability, Concurrency, Downloads & README De-Bloating)

- [x] **B23 — Fresh `bridge.ini` generation omits `llama_server_bin` and commented defaults (`installer.py`):** Always write `llama_server_bin` when a binary is resolved (including `system-bin` and `--tui`), stamp `schema_version = 1`, and include commented defaults aligned with `bridge.example.ini`.
- [ ] **M29 — Persist resolved `llama_server_bin` and commented defaults in generated `bridge.ini` (`installer.py`):** Fresh `bridge.ini` files omit `llama_server_bin` when `--mode system-bin` is used and omit commented defaults for `host`, `port`, `auto_spawn`, `models_max`, `sleep_idle_seconds`, and `fallback_on_oom`. Write `llama_server_bin` whenever a binary path is detected and include concise commented defaults aligned with `bridge.example.ini`.
- [ ] **B14 — Disk preflight uses stale registry `size_gb` (`model_download.py`):** `_download_spec_list` computes required disk space from static `spec["size_gb"]` (skipping the check entirely when `size_gb == 0` on custom `--hf`/`--file` models) instead of using `size_bytes` returned by `probe_model_metadata`. Prefer HEAD `Content-Length` `size_bytes`, falling back to `size_gb` only when HEAD size is unavailable.
- [ ] **B15 — HTTP 416 treated unconditionally as download complete (`model_download.py`):** `_download_spec_list` treats any HTTP `416 Range Not Satisfiable` on an existing `.part` file as completion without checking `Content-Range: bytes */<total>`, which can finalize an oversized `.part` if an upstream file shrank and lacks a SHA-256 ETag. Verify `offset == total` from `Content-Range` before finalizing.
- [ ] **B16 — No per-chunk retry/resume inside GGUF download loop (`model_download.py`):** A single transient socket read failure in `resp.read(1024 * 1024)` aborts multi-gigabyte downloads instead of retrying from the current `.part` byte offset with backoff.
- [ ] **B17 — Unhandled HTTP/JSON exceptions in `resolve_hf_gguf_repo` (`presets.py`):** Unlike sibling helpers (`probe_model_metadata`, `search_hf_gguf_models`, `verify_model_url`), `resolve_hf_gguf_repo` does not catch `URLError`, `HTTPError`, `JSONDecodeError`, or `TimeoutError`, leaking raw tracebacks on `llama-mcp-models add --hf`.
- [ ] **B18 — `ensure_running()` holds `self._lock` across 30s health polling (`supervisor.py`):** Holding `self._lock` during `_poll_until_healthy()` blocks concurrent `stop()`, `is_healthy()`, `get_health_status()`, and `atexit` cleanup for up to 30 seconds. Release `self._lock` after spawning the subprocess and poll outside the lock.
- [ ] **B19 — Broad `"metal"` substring in `_OOM_SIGNATURES` (`supervisor.py`):** Bare `"metal"` matches non-OOM Metal shader compilation or device-init errors on macOS, misclassifying them as OOM. Narrow to explicit allocation failures (e.g., `"metal: out of memory"`).
- [ ] **B20 — Unsynchronized `_MODELS_INI_STATE` and per-call disk reads (`bridge.py`):** Guard snapshot-compare-swap in `check_and_reload_models_ini` with `_TOOL_REG_LOCK` and cache by `(st_mtime_ns, st_size)` before reading `models.ini` bytes on every `query_llama` call.
- [ ] **B21 — `check_readiness` does not catch `health_check` exceptions (`enterprise/probes.py`):** Wrap `health_check()` in `try/except` so supervisor or config errors return `(503, {"status": "error", "reason": ...})` instead of an unhandled exception.
- [ ] **B22 — Windows `msvcrt.locking` on zero-byte lock files (`enterprise/tenant.py`):** `msvcrt.locking(fd, msvcrt.LK_LOCK, 1)` can raise `PermissionError` / `Errno 13` on an empty newly created file and times out after ~10s unlike blocking POSIX `flock`. Ensure at least 1 byte exists before locking and document/handle retry semantics.
- [x] **D36 — Extract 25-row CLI flag table to `docs/cli.md` (`README.md`, `docs/cli.md`, D-06):** Move the ~600-word CLI reference table to `docs/cli.md`, leaving a 3-line pointer to `llama-mcp-install --help` and `docs/cli.md`.
- [x] **D37 — Extract full MCP tool tables & reasoning stack to `docs/tools.md` (`README.md`, `docs/tools.md`, D-07):** Move the Specialist, Dispatcher, and Diagnostics tables plus Reasoning Management to `docs/tools.md`; keep a compact 9-row specialist summary table (`name` + one-line role) in `README.md`.
- [x] **D38 — Extract 15-bullet "Advanced Runtime & Multi-Workspace" section to `docs/advanced.md` (`README.md`, `docs/advanced.md`, D-08):** Move auth headers, named workspaces, warmup, and signature verification reference details to `docs/advanced.md`.
- [x] **D39 — Restructure Installation into four parallel paths (`README.md`, `docs/install.md`, `docs/install-agent.md`, D-09):** Present TUI wizard, agent self-bootstrap (`SKILL.md`), non-interactive CLI, and release wheel as parallel installation paths rather than sequential steps, moving full multi-option install steps into `docs/install.md` and `docs/install-agent.md`.
- [x] **D40 — Deduplicate Enterprise warning (`README.md`, `docs/enterprise.md`, D-10):** Keep a single two-sentence provisional warning in `README.md` and move E3/E7/E11 operational details to `docs/enterprise.md`.
- [x] **D41 — Add top-of-README status badges (`README.md`, D-11):** Add Python `>=3.11`, GitHub Actions CI status, latest release tag, and license badges under the title.
- [x] **D42 — Hoist Troubleshooting FAQ and link from Quick Start (`README.md`, `docs/troubleshooting.md`, D-12):** Move condensed 5-item Troubleshooting FAQ higher in `README.md` (with a direct link from Quick Start) and expand full troubleshooting in `docs/troubleshooting.md`.
- [ ] **D43 — Record and embed a 30-second terminal demo (`README.md`, D-13):** Capture a clean `asciinema` recording of `llama-mcp-install` running on a fresh 8 GB setup and link/embed it near Quick Start.

---

## Open — P2 (Medium Edge Cases, Security Hardening & Docs Accuracy)
- [ ] **S8 — Unescaped `</file>` delimiters in `_build_prompt_with_files` (`discovery.py`):** File contents containing literal `</file>` break the `<file path="...">...</file>` prompt framing, and paths containing `<`/`>` are unescaped. Escape `<`/`>` in paths and escape or neutralize closing `</file>` tags inside file content.
- [ ] **S9 — Raw `files_accessed` paths logged in `AuditLogger.record` (`enterprise/audit.py`):** `input_text` is hashed to prevent payload leakage, but `files_accessed` stores full filesystem paths verbatim. Hash file paths (or store basenames only) to prevent directory-structure leakage.
- [ ] **M21 — Paths containing glob metacharacters (`[`, `?`, `*`) fail literal lookup (`discovery.py`):** `_expand_target_files` runs `glob_mod.has_magic()` before checking `Path(expanded_str).exists()`, causing literal paths like `/home/user/[draft]/notes.md` to fail with `"Glob pattern matched no files"`. Check literal existence first.
- [ ] **M22 — Byte-read vs. character-count budget mismatch in `_build_prompt_with_files` (`discovery.py`):** Reading `remaining + 1` bytes per file while subtracting `len(content)` Unicode characters allows multibyte UTF-8 text to exceed `max_total_chars` (and token budgets on smaller context models). Align read/slice budgeting in characters (and verify callers size `max_total_chars` from preset `ctx-size`).
- [ ] **M23 — `models.ini` mutations strip comments and blank lines (`presets.py`):** `merge_models_ini`, `merge_custom_preset_ini`, and `remove_models_from_ini` re-serialize via `ConfigParser`, discarding user comments (`#`, `;`). Either preserve untouched sections/comments via line-preserving edits or document the behavior explicitly in `docs/config.md`.
- [ ] **M24 — `merge_custom_preset_ini` allows reserved section name `"DEFAULT"` (`presets.py`):** Reject `"DEFAULT"` (case-insensitive) alongside `""` and `"*"` so `llama-mcp-models add DEFAULT` cannot silently inject `ConfigParser` section defaults.
- [ ] **M25 — `prune_unreferenced_models` ignores `.part`, `.gguf.tmp`, and uppercase `.GGUF` (`presets.py`):** Extend cleanup matching (or add `--include-partials`) so crashed downloads and case-insensitive filenames are pruned cleanly.
- [ ] **M26 — Non-atomic cache write in `verify_tier_models` (`presets.py`):** Write JSON cache updates to a sibling `.tmp` file and atomically replace via `Path.replace()` to avoid torn reads under concurrent runs.
- [x] **M27 — `llama-mcp-install` omits `schema_version = 1` on fresh `bridge.ini` generation (`installer.py`, `bridge.example.ini`):**
- [ ] **M28 — `_stderr_tail` never populated in `LlamaSupervisor` (`supervisor.py`):** Because `Popen` redirects `stderr=subprocess.STDOUT` to the log file, `proc.stderr` is `None` and `_start_stderr_reader()` returns immediately, leaving `get_stderr_tail()` to read the merged log file. Either capture `stderr` via pipe + tee to log, or align `README.md` / `docs/failure-classification.md` wording to `"server-log tail"`.
- [x] **D44 — Footnote `24gb+` model family swap in Hardware Tiers table (`README.md`, D-14):** Add a footnote explaining that `24gb+` swaps `coder`, `coder-fast`, and `coder-long` to `Qwen 2.5-Coder-14B` rather than keeping the smaller coder models.
- [x] **D45 — Verify & document `24gb+` `coder-long` 131K VRAM headroom (`README.md`, `presets.py`, D-15, N-18):** Benchmark `[coder-long]` at `131072` ctx with `q4_0` V-cache on 24 GB VRAM (`--model-info` + `--profile`); reduce to `98304` or annotate headroom caveats if OOM occurs.
- [x] **D46 — Hoist `uvx` zero-install dry-run into Quick Start (`README.md`, D-16):** Move `uvx --from git+... llama-mcp-install --dry-run` out of a buried blockquote into Quick Start as the "try without installing" one-liner.
- [x] **D47 — Promote "Agency-Free Specialist Boundary" to top-level Security Model section (`README.md`, D-17, D-18):** Elevate the zero-agency / `.gitignore`-brokered access guarantee into a dedicated two-paragraph **Security Model** section and link directly to `SECURITY.md`.
- [x] **D48 — Align version & stability metadata across `README.md`, `STABILITY.md`, and `pyproject.toml` (`README.md`, `pyproject.toml`, D-19, D-20):** Collapse the two-paragraph stability callout into a concise pointer to `STABILITY.md`, update `pyproject.toml` classifier from `Development Status :: 4 - Beta` to `Development Status :: 5 - Production/Stable`, and align version strings (`v1.0.1`+).
- [x] **D49 — Expand `ARCHITECTURE.md` and `docs/` deep dives or soften links (`ARCHITECTURE.md`, `docs/*.md`, D-21, D-22):** Expand `ARCHITECTURE.md` (module map, spawn lifecycle, hot-reload sequence, OOM fallback flow) and either flesh out `failure-classification.md`, `gguf-auto-tuning.md`, and `reasoning-management.md` or fold them into `docs/tools.md` / `docs/config.md` so no link points to a thin stub.
- [x] **D50 — Populate or retire `ROADMAP.md` and link `TRACKER.md` + `SHIPPED.md` from `README.md` (`README.md`, `ROADMAP.md`, D-23, D-24):** Either populate `ROADMAP.md` with concrete milestone themes or replace it with a pointer to `TRACKER.md`, and surface `TRACKER.md` and `SHIPPED.md` in the `README.md` Documentation footer.
- [x] **D51 — Document CPU fallback tier (`6gb`) and RAM recommendation (`README.md`, D-25):** Explicitly state that `llama-mcp-install` falls back to the `6gb`/CPU tier when no supported GPU is detected and recommends ≥16 GB system RAM.
- [x] **D52 — Extract Configuration reference (`LLAMA_MCP_HOME`, `bridge.ini`, `models.ini`) to `docs/config.md` (`README.md`, `docs/config.md`):** Move the ~500-word configuration reference from `README.md` into `docs/config.md` and keep a concise 100-word overview + link in `README.md`.
- [x] **D53 — Extract Model Management reference (6 subsections) to `docs/models.md` (`README.md`, `docs/models.md`):** Move full add/remove/import/prune/custom model management sections to `docs/models.md`, keeping common one-liner commands in `README.md`.
- [x] **D54 — Extract Service Installation reference (4 bullets) to `docs/services.md` (`README.md`, `docs/services.md`):** Move `systemd`, `launchd`, Windows `schtasks`, `crontab`, and BSD `rc.d` details to `docs/services.md`.

---

## Open — P3 (Polish, Packaging, Style & Hygiene)
- [ ] **N13:** Rename corrupted `.part` files to `.part.corrupt` (or keep behind a flag) on SHA-256 mismatch in `_download_spec_list` (`model_download.py`) for post-mortem diagnosis.
- [ ] **N14:** Export `audit`, `probes`, and `tenant` submodules in `src/llama_mcp_bridge/enterprise/__init__.py` so `llama_mcp_bridge.enterprise.audit` and `from llama_mcp_bridge.enterprise import *` match `__all__`.
- [ ] **N15:** Source Hugging Face `User-Agent` version dynamically from `importlib.metadata.version("llama-mcp-bridge")` instead of hardcoded `llama-mcp-bridge/0.1.0`.
- [ ] **N16:** Clarify or remove the empty `enterprise = []` optional extra in `pyproject.toml` and document that `--enterprise` is a runtime flag (`README.md`, `docs/enterprise.md`).
- [ ] **N17:** Verify `.gitignore` excludes `__pycache__/` and confirm `git ls-files | grep __pycache__` is empty.
- [ ] **N18:** Verify `llama.cpp` runtime handling of `reasoning-budget-message = ""` for `coder-fast` and `6 GB` CPU prefill latency for `digest` at `65536` ctx (`presets.py`).
- [ ] **N19:** Add a `--doctor --verify-models` size-drift warning when HEAD `X-Linked-Size` diverges by >20% from registry `size_gb` (`presets.py`).
- [x] **D55 — Tighten prose style and reduce em-dash/abstraction density (`README.md`, D-26, D-27):** Edit section intros for direct, concrete phrasing (~1,200-word landing page budget).
- [x] **D56 — Clarify TUI wizard auto-launch behaviour in Quick Start (`README.md`, D-28):** State explicitly whether `llama-mcp-install` with no flags launches the interactive TUI wizard, matching the section heading.
- [x] **D57 — Extract BSD installation notes to `docs/install-bsd.md` (`README.md`, `docs/install-bsd.md`, D-29):** Move platform-specific BSD install details out of `README.md` into `docs/install-bsd.md` with a one-line pointer in Installation.
- [x] **D58 — Replace GitHub-only `[!WARNING]` admonition with portable Markdown blockquote (`README.md`, D-30):** Use standard `> **Warning:** ...` syntax so PyPI and non-GitHub renderers display warnings cleanly.
- [x] **D59 — Surface `CONTRIBUTING.md` link and `Python >= 3.11` requirement near top of `README.md` (`README.md`, D-31, D-32):** Add a top-level links bar (`Docs` · `Architecture` · `Contributing` · `Security`) and state `Python >= 3.11` right above Quick Start.

---

## Open — Testing & Tooling Gaps
- [x] **T26 — Add `tests/test_presets.py` with tier × preset invariant:**
- [x] **T27 — Expand `tests/test_discovery.py` (`discovery.py`):**
- [ ] **T28 — Add `tests/test_model_download.py` & `tests/test_enterprise.py`:** Cover HEAD `size_bytes` preflight (`B14`), HTTP 416 `Content-Range` validation (`B15`), chunk retry/resume (`B16`), tenant symlink rejection (`S7`), Windows empty-file `msvcrt.locking` (`B22`), and `check_readiness` error handling (`B21`).
- [ ] **T29 — Audit `service_install.py` coverage & refactor test suite structure:** Verify `test_cross_platform.py` covers all 5 service targets (`systemd`, `launchd`, `schtasks`, `crontab`, BSD `rc.d`), split `test_cross_platform.py` by platform/subsystem, group version-tagged tests (`test_v010`…`test_v090`) by feature, and centralize shared fixtures in `tests/conftest.py`.
- [x] **T30 — Verify `Makefile` parity with CI and add `make verify-all` (`Makefile`):** Ensure `make test`, `make lint`, and `make typecheck` mirror `.github/workflows/` and add `make verify-all` (`--doctor` + `--selftest`).

---

## Completed — P0 — Security & Correctness (`v1.0.0` / `v1.0.1`)
- [x] **S1 — Archive extraction path-traversal defense (`installer.py`):** `_extract_archive` uses `zipfile.extractall` and `tarfile.extractall`, which will write outside `dest_dir` if an archive contains `../` entries or absolute paths. Validate every archive member's resolved target path stays strictly inside `dest_dir` before extraction.
- [x] **S2 — Archive extraction size and entry-count bounds (`installer.py`):** Enforce configurable ceilings on total extracted bytes (e.g. `2 GiB`) and total file count (e.g. `5,000` entries) inside `_extract_archive` to prevent zip bombs or corrupted release archives from exhausting disk/inodes.
- [x] **S3 — Windows WMI `AdapterRAM` >4 GB overflow (`installer.py`):** `_probe_windows_wmi` reads `Win32_VideoController.AdapterRAM` (`uint32`), which overflows/wraps at 4 GB and misclassifies 8 GB–24 GB AMD/Intel GPUs as `6gb`. Query 64-bit `HardwareInformation.qwMemorySize` from the Windows display adapter registry (`HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\000*`) when `nvidia-smi` is absent, falling back to `AdapterRAM` only if unavailable.
- [x] **S4 — Make `--apply` opt-in in `SKILL.md` (`skills/llama-mcp-setup/SKILL.md`):** Step 3 currently instructs agents to run `--configure-client <harness> --apply` by default. Restate Step 3 so the agent prints the `mcpServers` JSON snippet (`llama-mcp-install --configure-client <harness>`) by default and only runs `--apply` when the user explicitly authorizes modifying their harness config.
- [x] **S5 — Require absolute binary path for `@reboot` crontab (`installer.py`):** `_install_reboot_crontab` currently falls back to bare `"llama-server"` if `cfg.llama_server_bin` is unset. Cron runs with a minimal `PATH`, so a bare binary name can resolve to the wrong binary or fail silently. Refuse to install the `@reboot` cron entry unless `server_bin` resolves to an existing absolute path.

## Completed — P1 — User-Affecting Bugs (`v1.0.0` / `v1.0.1`)
- [x] **B0 — Pass `--host`, `--port`, `--models-preset`, `--models-dir`, and tier-resolved residency flags on auto-spawn (`supervisor.py`, `config.py` — shipped in `v1.0.1`):** `LlamaSupervisor._start_server()` now forwards `--host`, `--port`, `--models-preset`, `--models-dir`, and tier+residency `--models-max` / `--sleep-idle-seconds` (with `bridge.ini` overrides) to `llama-server`.
- [x] **B1 — Parenthesize `_select_llama_release_asset` operator precedence (`installer.py`):** Explicitly parenthesize `(profile.gpu_vendor == "nvidia" and "cuda" in name) or (profile.gpu_vendor in ("amd", "intel") and "vulkan" in name)` so future scoring additions cannot silently break precedence.
- [x] **B2 — Add AMD ROCm scoring branch (`installer.py`):** Add `rocm` / `hip` token scoring in `_select_llama_release_asset` for `profile.gpu_vendor == "amd"` alongside `vulkan` so AMD users get ROCm prebuilt assets when `llama.cpp` ships them.
- [x] **B3 — Include BSD in GPU detection (`installer.py`):** Update `detect_hardware` from `elif os_name in ("linux", "windows"):` to include `"freebsd"` (and BSD variants where `nvidia-smi` is available) instead of falling through unconditionally to the CPU tier.
- [x] **B4 — Replace `platform.release()` in `_detect_distro_family` (`installer.py`):** `platform.release()` returns the kernel version, not the OS distro. Replace with `platform.freedesktop_os_release()` (falling back to `/etc/os-release`).
- [x] **B6 — Scope `_uninstall_bridge` crontab removal (`installer.py`):** Filter crontab lines strictly by `# llama-mcp-bridge` marker rather than removing any line containing `"llama-server"`, avoiding deletion of unrelated user cron jobs.
- [x] **B7 — Handle moved `models_dir` in `models.ini` (`installer.py`):** When `--models-dir` is passed on an existing workspace (or via `--repair`), rewrite `[preset]` `model` and `mmproj` paths in `models.ini` to match the new `models_dir` so `--doctor` does not fail on stale paths.
- [x] **B8 — Fix macOS Xcode CLT check (`installer.py`):** Replace `shutil.which("xcode-select")` (which always succeeds at `/usr/bin/xcode-select`) in `_check_hardware_prerequisites` with `xcode-select -p` checking exit status `0`.
- [x] **B9 — Safe fallback when `models.ini` is missing (`bridge.py`):** In `_register_configured_tools`, when `models.ini` does not exist, log a warning and register only `get_bridge_status` and `ask_preset` instead of registering all 9 unconfigured built-in tools.
- [x] **B10 — Guard FastMCP internal tool-clearing (`bridge.py`):** Guard `getattr(srv, "local_provider", None)._components` clearing in `_register_configured_tools` with an explicit check and comment so FastMCP internal API changes fail loudly rather than silently duplicating tools.
- [x] **B11 — Explicit image-drop notice on vision OOM fallback (`bridge.py`):** When an image request falls back to text-only `digest` on OOM, update the notice string to explicitly state `"(text-only; images dropped)"`.
- [x] **B12 — Clear error when `cmake` and `uvx` are both missing (`installer.py`):** Raise `RuntimeError("cmake not found; install cmake or uvx, or use --mode prebuilt")` before `subprocess.run` instead of letting `["cmake"]` fail with an opaque `FileNotFoundError`.

## Completed — P2 — Medium Edge Cases (`v1.0.0`)
- [x] **M1 — Expand `_cleanup_harness_mcp_configs` to all 7 harnesses (`installer.py`):** Mirror `_resolve_harness_config_paths` so `cline`, `windsurf`, and `zed` are cleaned up on `--uninstall` alongside `antigravity`, `claude-code`, and `cursor`.
- [x] **M2 — Delete empty `mcpServers` object on cleanup (`installer.py`):** Remove the parent `"mcpServers"` key from harness JSON configs when removing `"local-ai"` leaves `"mcpServers": {}` empty.
- [x] **M3 — Consolidate duplicate image data-URI encoders (`bridge.py`):** Merge `_encode_image_data_uri` and `_encode_image_to_data_uri` into a single helper.
- [x] **M4 — Inherit tier defaults in `merge_custom_preset_ini` (`presets.py`):** Populate `ctx-size` and `n-gpu-layers` from the detected hardware tier defaults when adding custom presets instead of hardcoding `8192` / omitting `n-gpu-layers`.
- [x] **M5 — Tier-aware quant heuristic in `resolve_hf_gguf_repo` (`presets.py`):** Select default GGUF quantisation preference using the detected hardware tier rather than always picking `Q4_K_M`.
- [x] **M6 — Document basename stripping in `remove_models_from_ini` (`presets.py`):** Add a docstring/comment clarifying that `.name` restricts file deletion strictly to `models_dir` basenames.
- [x] **M7 — Validate archive symlink targets in `_extract_archive` (`installer.py`):** Reject or bound symlinks inside downloaded archives whose resolved targets point outside `dest_dir` before `_flatten_extracted_dir` moves them.
- [x] **M8 — Detect VS Code Insiders and VSCodium for `cline` (`installer.py`):** Extend `_resolve_harness_config_paths("cline")` to detect `Code - Insiders` and `VSCodium` globalStorage directories in addition to `Code`.
- [x] **M9 — Document/harden `_resolve_bundled_skill_path` fallback (`installer.py`):** Prefer `importlib.resources` and document the `parents[2]` repo-root fallback assumption (or log a debug notice when used).
- [x] **M10 — Validate `llama-server --version` in `_run_doctor` (`installer.py`):** Run `subprocess.run([srv_bin, "--version"], timeout=5)` in `_run_doctor` to verify the binary actually executes rather than only checking file permissions.
- [x] **M11 — Cross-check `models.ini` presets against registered MCP tools in `_run_doctor` (`installer.py`):** Verify every `[preset]` in `models.ini` has a corresponding registered `ask_<preset>` tool.
- [x] **M12 — Check free disk space in `_run_doctor` (`installer.py`):** Check available disk space in `models_dir` before recommending `--download-models`.
- [x] **M13 — Standardize HTTP timeout between `probe_model_metadata` and `verify_model_url` (`presets.py`):** Align `timeout=5` and `timeout=10` to a single consistent constant.
- [x] **M14 — Expose `--max-workers` on `verify_tier_models` (`installer.py`):** Make the `ThreadPoolExecutor(max_workers=8)` worker count configurable via `--max-workers` to avoid Hugging Face rate limits.
- [x] **M15 — URL-encode filenames in Hugging Face URLs (`presets.py`):** Use `urllib.parse.quote` on filenames when constructing `https://huggingface.co/{repo}/resolve/main/{filename}` URLs.
- [x] **M16 — Cache `detect_hardware()` in error classification (`bridge.py`):** Cache the `SystemProfile` instead of spawning hardware probe subprocesses on every inference error.
- [x] **M17 — Mtime-cache `load_config()` (`bridge.py`):** Cache parsed `bridge.ini` / `models.ini` keyed by file `st_mtime` instead of re-reading from disk on every tool call.
- [x] **M18 — Restore `LLAMA_MCP_HOME` env state after TUI wizard (`installer.py`):** Avoid mutating `os.environ["LLAMA_MCP_HOME"]` permanently in-process after `_run_tui_wizard` exits.
- [x] **M19 — Preserve key case in `_persist_server_bin` (`installer.py`):** Use `ConfigParser(default_section=None, interpolation=None)` with `optionxform = str` so mixed-case keys in `bridge.ini` round-trip intact.
- [x] **M20 — Print change summary in `_run_tui_wizard` (`installer.py`):** Log what changed vs. what was preserved when re-running the TUI wizard on an existing workspace.

## Completed — P3 — Polish & Nits (`v1.0.0`)
- [x] **N1:** Remove redundant `os.X_OK` check alongside `sys.platform == "win32"` in `_run_doctor` (`installer.py`).
- [x] **N2:** Remove unused `server_url` field from `BridgeConfig` (`bridge.py`) or wire it up.
- [x] **N3:** Remove unused `distro_family` field from `SystemProfile` (`installer.py`) or wire it up.
- [x] **N4:** Derive `int_fields` and `bool_fields` in `load_config` (`bridge.py`) from `BridgeConfig` dataclass field annotations instead of hardcoding field names.
- [x] **N5:** Clean up redundant local `base64` / `mimetypes` imports in `bridge.py` when consolidating image helpers (`M3`).
- [x] **N6:** Add a note to the Windows PowerShell preflight in `skills/llama-mcp-setup/SKILL.md` for environments where `uv tool update-shell` has not yet been run.
- [x] **N7:** Add `--json` machine-parseable output flag to `llama-mcp-install --doctor`.
- [x] **N8:** Add `--json` machine-parseable output flag to `llama-mcp-ps` / `llama-mcp-install --ps`.
- [x] **N9:** Surface the active workspace path and whether `LLAMA_MCP_HOME` is active in `--ps` and `--doctor` output.
- [x] **N10:** Add `[project.urls]` (`Homepage`, `Repository`, `Issues`) to `pyproject.toml`.
- [x] **N11:** Add PyPI `classifiers` to `pyproject.toml`.
- [x] **N12:** Remove dead `_MODEL_LOCK` and unused `import threading` in `bridge.py`.

## Completed — Features (`v1.0.0`)
- [x] **F1 — Optional streaming support:** Add opt-in streaming in `query_llama` and MCP tool handlers (`"stream": True`).
- [x] **F2 — Hot-reload `models.ini` via `notifications/tools/list_changed`:** Emit MCP tool-list change notifications when `models.ini` updates without requiring a client restart.
- [x] **F3 — `--verify-all` CLI flag:** Combine `--verify-models` and `--doctor` into a single deployment check.
- [x] **F4 — 1-hour TTL cache for Hugging Face URL verification:** Cache `verify_tier_models` results locally for 1 hour to reduce repeated HF API calls.
- [x] **F5 — `--import-ollama` manifest parser & blob symlinker (`ollama_import.py`):** Walk `~/.ollama/models/manifests/**`, parse JSON layers (`application/vnd.ollama.image.model` and `.projector`), **symlink** (never copy) matching `blobs/sha256-<hash>` files into `<workspace>/models/<model>-<tag>.gguf`, run `parse_gguf_header` on each symlinked blob to auto-tune `models.ini`, reject split multi-blob models clearly, and catch `Invalid GGUF magic` on safetensors-backed Ollama models with a warning.
- [x] **F6 — `ask_batch` parallel prompt tool:** Batch N prompts against the same preset when `parallel > 1`.
- [x] **F7 — MCP `notifications/progress` for long inference:** Emit progress notifications during calls exceeding 30 seconds.
- [x] **F8 — `--export-config` and `--diff-config`:** Print effective merged `[*]` + `[preset]` config and diff current `models.ini` against freshly generated tier defaults.
- [x] **F9 — `--doctor --verify-sha`:** Re-verify downloaded GGUF files on disk against Hugging Face `X-Linked-ETag` SHA-256 headers.
- [x] **F10 — `llama-mcp-models --prune`:** Remove unreferenced `.gguf` and `mmproj` files in `models_dir` not claimed by any `[preset]` in `models.ini`.
- [x] **F11 — `--profile` benchmarking mode:** Run a standard prompt suite across configured presets and report tokens/sec, latency, and VRAM usage.
- [x] **F12 — Optional `--api-key` auth for `llama-server`:** Wire `llama-server --api-key` through `bridge.ini` and `Authorization: Bearer` headers.
- [x] **F13 — Prometheus `/metrics` sidecar endpoint:** Expose request counts, latency histograms, model load events, and OOM counts.
- [x] **F14 — Per-preset access control (`allowed_presets`):** Allow restricting callable presets per `bridge.ini` profile.
- [x] **F15 — Named multi-workspace support (`--register-name`):** Support multiple named workspace pointers alongside the default global workspace.
- [x] **F16 — `--selftest` end-to-end inference probe:** Run a live `ask_digest("ping")` smoke test against `llama-server` and verify a non-empty reply.
- [x] **F17 — `--warmup` preset preloading:** Optionally preload configured presets on bridge/service startup.
- [x] **F18 — `llama-mcp-config check <path>` CLI:** Validate `bridge.ini` and `models.ini` syntax/paths standalone.
- [x] **F19 — `--model-info <preset>` CLI flag:** Display effective merged `llama-server` flags (`[*]` + `[preset]`) for a single preset.
- [x] **F20 — GPG / sigstore signature verification for downloaded `llama-server` binaries.**
- [x] **F21 — `--check-update` via Git tags:** Query `git ls-remote --tags https://github.com/OakHell/llama-mcp-bridge`, parse SemVer tags, and compare against installed `pyproject.toml` version.
- [x] **F22 — Optional per-call `timeout_s` override** on MCP tool invocations.

## Completed — Chores (`v1.0.0`)
- [x] **C1:** Consolidate `_encode_image_data_uri` and `_encode_image_to_data_uri` (`bridge.py`).
- [x] **C2:** Add `src/llama_mcp_bridge/py.typed` PEP 561 marker.
- [x] **C3:** Add `mypy` (or `pyright`) static type-checking job to `.github/workflows/`.
- [x] **C4:** Add `--version` flag to `llama-mcp-install`, `llama-mcp-models`, and `llama-mcp-bridge`.
- [x] **C5:** Add `--log-level` CLI flag and structured Python `logging` setup.
- [x] **C6:** Make `llama-server.log` path configurable in `bridge.ini` (`server_log`).
- [x] **C7:** Add `--dry-run` support to `--verify-models`.
- [x] **C8:** Standardize on `Path.home()` instead of mixing `os.path.expanduser` across modules.
- [x] **C9:** Protect `_register_configured_tools` with a re-entrant lock for thread safety.
- [x] **C10:** Define explicit `__all__` exports in every module under `src/llama_mcp_bridge/`.
- [x] **C11:** Separate shipped history (`SHIPPED.md`) from forward-looking `ROADMAP.md` without bloating `README.md`.
- [x] **C12:** Add `.pre-commit-config.yaml` configured for `ruff` and `mypy`.
- [x] **C13:** Add `Taskfile.yml` or `Makefile` for common `test`, `lint`, `typecheck`, and `doctor` tasks.
- [x] **C14:** Add `[project.urls]` and `classifiers` to `pyproject.toml`.
- [x] **C15:** Add `mypy` and `types-requests` to `[dependency-groups] dev` in `pyproject.toml`.
- [x] **C16:** Split `installer.py` (~1,500 lines) into focused submodules (`service_install.py`, `model_download.py`, `client_config.py`, `skill_install.py`).
- [x] **C17:** Split file discovery (`_expand_target_files`, gitignore walking) out of `bridge.py` into `discovery.py`.
- [x] **C18:** Consolidate `_PRESET_NAMES` in `installer.py` with `_VALID_PRESETS` in `presets.py`.
- [x] **C19:** Derive `_VALID_PRESETS` directly from `base_config` keys in `generate_models_ini` (`presets.py`).
- [x] **C20:** Add `ARCHITECTURE.md` (or module call-graph diagram in `README.md`).
- [x] **C21:** Add `docs/` deep-dive guides (reasoning-budget stack, GGUF header auto-tuning, stderr failure classifier).

## Completed — Testing Gaps (`v1.0.0` / `v1.0.1`)
- [x] **T0:** Unit tests in `tests/test_supervisor_spawn_args.py` verifying `--host`, `--port`, `--models-preset`, `--models-dir`, and tier+residency `--models-max` / `--sleep-idle-seconds` (`v1.0.1`).
- [x] **T1:** Unit test `_select_llama_release_asset` with AMD ROCm release assets.
- [x] **T2:** Unit test `detect_hardware` on BSD (`sys.platform = "freebsd14"`).
- [x] **T3:** Unit test `_probe_windows_wmi` with >4 GB VRAM (64-bit registry path vs. 32-bit `AdapterRAM` overflow).
- [x] **T4:** Unit test `_extract_archive` path-traversal rejection (Zip Slip and Tar `../` traversal).
- [x] **T5:** Unit test `_extract_archive` zip-bomb / max-bytes and max-entry-count enforcement.
- [x] **T6:** Unit test `_extract_archive` symlink target escape rejection.
- [x] **T7:** Unit test `remove_models_from_ini` with traversal-like preset model paths (`model = /etc/passwd`).
- [x] **T8:** Concurrency test for simultaneous `_register_configured_tools` calls.
- [x] **T9:** Unit test `_configure_client --apply` when target JSON file contains a top-level list instead of a dict.
- [x] **T10:** Unit test `probe_model_metadata` timeout handling.
- [x] **T11:** Unit test `_find_llama_server` when configured `llama_server_bin` path does not exist.
- [x] **T12:** Unit test relative `workspace` path resolution in `bridge.ini` (`workspace = ./local`).
- [x] **T13:** Unit test `_discover_ancestor_gitignores` with directory symlink loops.
- [x] **T14:** Unit test `_walk_dir_pruned` with directory symlink loops.
- [x] **T15:** Unit test `query_llama` when `models.ini` is deleted at runtime.
- [x] **T16:** Unit test `_persist_server_bin` round-trip preservation of existing `bridge.ini` sections and key case.
- [x] **T17:** Property-based tests for `_expand_target_files` precedence (`include > gitignore > ignore > defaults`).
- [x] **T18:** Property-based tests for `_build_prompt_with_files` character budget and smallest-first sorting.
- [x] **T19:** Expand fuzz/chaos tests for `clean_mcp_output` with nested code blocks, LaTeX, and malformed reasoning tags.
- [x] **T20:** Unit test `_run_doctor` with `auto_spawn = false` and server offline (expects `WARN`, not `FAIL` or `Standby`).
- [x] **T21:** Unit test `_uninstall_bridge` across `launchd`, Windows `schtasks`, `systemd`, and `crontab`.
- [x] **T22:** Integration test running `--configure-client --apply` followed by MCP `initialize` and `tools/list`.
- [x] **T23:** Optional end-to-end test against a real `llama-server` binary.
- [x] **T24:** Add real FreeBSD VM CI job (`vmactions/freebsd-vm` on `ubuntu-latest` with `cache-after-prepare: true`) running full `pytest` suite and `llama-mcp-install` dry-runs.
- [x] **T25:** Optional end-to-end `--selftest` on FreeBSD VM against pkg-installed `llama.cpp` (`system-bin` mode).

## Completed — Documentation Gaps (`v1.0.0`)
- [x] **D1:** Add a 3–4 sentence **Design Stance** paragraph in `README.md` right after the intro (Ollama ease + `llama.cpp` transparency; hardware/geometry-derived tuning; agency-free specialist boundary; small composable primitives).
- [x] **D2:** Add an explicit **Tradeoffs & Non-Goals** section in `README.md` (no streaming, reload required for new `ask_<name>` discovery in `tools/list`, HF-only curated set, AMD prebuilt Vulkan default, single `llama-server` process, CPU-only `6gb` tier latency).
- [x] **D3:** State in `README.md` that `models.ini` is `llama-server`'s native router-mode preset file (full `llama-server` flag surface delegated verbatim).
- [x] **D4:** Distinguish `models.ini` (`llama.cpp`'s preset contract) from `bridge.ini` (the bridge's orchestration contract) in `README.md`.
- [x] **D5:** Document the **Reasoning Management** stack in `README.md` (`reasoning-format = deepseek`, per-role `reasoning-budget` table verified from `presets.py`, `reasoning-budget-message` + deterministic stripping in `hygiene.py`).
- [x] **D6:** Document in `README.md` that `gguf_header.py` parses six attention layouts (MHA/GQA, per-layer GQA variance, SWA-alternating, hybrid SSM state overhead, DeepSeek MLA, and KV-shared layers).
- [x] **D7:** State in `README.md` that specialist tools are **agency-free** (no function calling, no subprocesses, no network egress; file/image access is brokered by the bridge within `.gitignore` and context budgets).
- [x] **D8:** State in `README.md` that OOM vs. timeout classification is **server-side** from captured `llama-server` `stderr`, not HTTP error bodies.
- [x] **D9:** Describe `fallback_on_oom` in `README.md` as a structured degradation loop (retry text prompt on `digest` + prepend fallback notice).
- [x] **D10:** Document the **Config-as-Contract / Tinkerability** guarantees in `README.md` (hand-edited sections preserved across re-installs and `--install-service`, reference-counted GGUF removal, `--uninstall` preserves `.ini`/`.gguf`, `--dry-run`, `--doctor`).
- [x] **D11:** Document the 3-OS GitHub Actions CI matrix (`ubuntu-latest`, `macos-latest`, `windows-latest`) in `README.md` Development.
- [x] **D12:** State the Python `>=3.11` requirement in `README.md`.
- [x] **D13:** Document `--verify-models` self-healing Hugging Face search suggestions on 404 in `README.md`.
- [x] **D14:** Mention Hugging Face `X-Linked-ETag` SHA-256 checksum verification in `README.md` Model Management.
- [x] **D15:** Document in `README.md` that `ask_preset` and custom `ask_<name>` tools also support `json_schema` grammar constraints.
- [x] **D16:** Add a **Model Family** column to the Specialist Tools table in `README.md` (noting cross-family diversity decorrelates role failures).
- [x] **D17:** Clarify in `README.md` that client reload is only for `tools/list` discoverability, while `ask_preset` can call newly added presets immediately.
- [x] **D18:** Expand custom model auto-tuning description in `README.md` (`resolve_hf_gguf_repo` `mmproj` selection + GGUF header geometry tuning via `--tune-mode`).
- [x] **D19:** Surface `LLAMA_MCP_HOME` as a first-class environment variable option in `README.md` Configuration.
- [x] **D20:** Add a **Contributing** section in `README.md` linking to `CONTRIBUTING.md`.
- [x] **D21:** Add a concise **Troubleshooting / FAQ** section in `README.md`.
- [x] **D22:** Warn in `SKILL.md` that `--apply` modifies the user's harness config and requires explicit user authorization (`S4`).
- [x] **D23:** Mention `--verify-models` in `SKILL.md` black-box troubleshooting.
- [x] **D24:** Clarify in `SKILL.md` that client restart refreshes `tools/list` discoverability while `ask_preset` works immediately.
- [x] **D25:** Create `SHIPPED.md` for shipped history (and `CHANGELOG.md` when breaking SemVer changes occur).
- [x] **D26:** Create `SECURITY.md` with vulnerability reporting scope and process.
- [x] **D27:** Create `CONTRIBUTING.md` (dev environment, `ruff`, `pytest`, PR guidelines).
- [x] **D28:** Create `CODE_OF_CONDUCT.md`.
- [x] **D29:** Create `.github/ISSUE_TEMPLATE/` (bug report, feature request) and `.github/PULL_REQUEST_TEMPLATE.md`.
- [x] **D30:** Create `ARCHITECTURE.md` with module call graph and data-flow diagram.

## Completed — Enterprise Positioning (`v1.0.0`)
- [x] **E1:** Structured JSON logging mode (`--log-format json`).
- [x] **E2:** Prometheus `/metrics` sidecar endpoint (`--metrics`).
- [x] **E3:** Rotating JSONL audit log of tool invocations.
- [x] **E4:** Config schema versioning and `--migrate-config`.
- [x] **E5:** CycloneDX SBOM generation (`uv export`) per release tag.
- [x] **E6:** Sigstore / cosign release artifact signing.
- [x] **E7:** Readiness vs. liveness healthcheck probes (`systemd` `WatchdogSec` / Kubernetes).
- [x] **E8:** Formal SemVer CLI/tool stability policy.
- [x] **E9:** Exponential backoff and circuit breaker for transient GitHub/Hugging Face `5xx` errors.
- [x] **E10:** Explicit resource-exhaustion handling (`EMFILE`, disk-full `ENOSPC`, thread limits).
- [x] **E11:** Multi-user / multi-tenant shared workspace isolation.
- [x] **E12:** Fault-injection chaos suite (network partition, disk full, port conflict, mid-download kill).

## Retracted / Not-a-Bug Ledger (Reference Only)
- **B5 (Retracted):** `_MODEL_LOCK` in `bridge.py` is never acquired in `query_llama` (dead variable tracked as `N12`); `parallel = N` is not serialized by the bridge.
- **C22 (Retracted):** Project runs under `uv`, which already enforces `requires-python = ">=3.11"`.
- **Windows `os.access(..., os.X_OK)` (Not a bug):** Works on Windows; redundant check tracked as `N1`.
- **`remove_models_from_ini` path traversal (Not a bug):** `.name` strips directories; documentation tracked as `M6`.
- **`_install_reboot_crontab` line filter (Not a bug):** Properly scoped to `# llama-mcp-bridge` (uninstall bug is tracked separately as `B6`).
- **T-001 Orphaned 9B at `24gb+` (Corrected):** Full `--download-models` at `24gb+` still uses the 9B model for `[vision-deep]`; only selective `--presets coder` / `--presets coder-long` breaks (tracked as `B13`).