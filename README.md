# Llama MCP Bridge

A cross-platform (Linux, macOS, Windows, FreeBSD) Model Context Protocol (MCP) bridge connecting MCP clients to a local `llama-server` instance. It automatically profiles your GPU VRAM (or CPU fallback) and generates tuned `models.ini` presets—selecting optimal GGUF quantisations, KV-cache precision, and context windows for your exact memory constraints.

**Design Stance:** `llama-mcp-bridge` pairs Ollama-style zero-friction setup with `llama.cpp`'s native router-mode transparency. Every context window, KV-cache precision, and offload decision is derived from hardware VRAM/RAM and GGUF binary header geometry rather than guesswork. Specialist tools are strictly agency-free—no function calling, subprocesses, or network egress—and are composed from small, inspectable primitives.

## Architecture & Features

- **VRAM-Aware Auto-Tuning & 6-Architecture GGUF Inspection:** Profiles NVIDIA, AMD ROCm, Apple Silicon, Intel/Vulkan, or CPU fallback and parses GGUF v2/v3 binary headers ([gguf_header.py](src/llama_mcp_bridge/gguf_header.py)) across six attention layouts: MHA/GQA, per-layer GQA variance, alternating Sliding Window Attention (SWA), hybrid SSM recurrent state, DeepSeek MLA (`kv_lora_rank`), and KV-shared layers.
- **Dynamic `models.ini`-Driven Tool Filtering:** Exposes only configured specialists in `tools/list` alongside always-on `get_bridge_status` and `ask_preset` (which can invoke newly added presets immediately before a client reload). `json_schema` grammar constraints are supported on `ask_digest`, `ask_preset`, and custom `ask_<name>` tools.
- **Agency-Free Specialist Boundary:** Specialist models have zero tool/function calling, subprocess, or network access; all file and image access is brokered by the bridge within `.gitignore` cascades, `!` negations, `include > ignore` precedence, binary skipping, and smallest-first context budgets.
- **Output Hygiene & Reasoning Management:** Strips internal reasoning/monologue tags deterministically ([hygiene.py](src/llama_mcp_bridge/hygiene.py)) while preserving fenced code blocks, LaTeX, and structured content.
- **Lazy Process Supervision & Server-Side Diagnostics:** Spawns `llama-server` on demand, polls `/health`, and classifies OOM vs. cold-swap timeouts directly from captured `llama-server` `stderr` rather than generic HTTP error bodies.

## Hardware & VRAM Tiers

| Tier | Typical Hardware | KV Cache (`K`/`V`) | `coder` / `coder-long` Context | Parallelism |
| :--- | :--- | :--- | :--- | :--- |
| `6gb` | 6 GB GPUs / CPU fallback | `q4_0` / `q4_0` | 8K / 16K | 1 |
| `8gb` | 8 GB GPUs (e.g. RTX 3070 `sm_86`) | `q8_0` / `q8_0` (`q4_0` long) | 16K / 32K | 1 |
| `12gb` | 12 GB GPUs | `q8_0` / `q8_0` | 24K / 40K | 1 |
| `16gb` | 16 GB GPUs / Apple Silicon | `q8_0` / `q8_0` | 32K / 64K | 2 |
| `24gb+` | 24 GB+ GPUs / Apple Silicon | `q8_0` / `q8_0` | 64K / 128K | 4 |

> **CPU-Only Notice:** When no supported GPU (`NVIDIA`, `AMD ROCm`, `Apple Metal`, `Intel/Vulkan`) is detected, the installer falls back to the lightweight `cpu` (`6gb`) preset tier in system RAM. At least **16 GB of system RAM** is recommended, and vision or 32K-context tools will have noticeably slower latency than `ask_digest` and `ask_coder_fast`.

## Quick Start (Guided Setup Wizard)

```bash
uv tool install git+https://github.com/OakHell/llama-mcp-bridge
uv tool update-shell
llama-mcp-install
```

Running `llama-mcp-install` with no flags automatically launches the interactive `rich` TUI wizard (or pass `--tui` explicitly) to detect your hardware, audit BYO GPU prerequisites, optionally install `llama-server`, configure background services, and print the ready-to-copy `mcp_config.json` snippet and required GGUF model filenames.

## Agent Self-Bootstrap (`SKILL.md` & `--install-skill`)

Give your coding agent one skill so it can autonomously install, verify, wire, and troubleshoot `llama-mcp-bridge`:

```bash
llama-mcp-install --install-skill          # auto-detects ~/.claude, ~/.gemini/config, ~/.gemini, ~/.cursor
llama-mcp-install --install-skill claude   # or: antigravity, gemini, cursor, workspace, or <custom-dir>
```

The bundled [skills/llama-mcp-setup/SKILL.md](skills/llama-mcp-setup/SKILL.md) encodes the deterministic bootstrap flow (`--yes` -> `--doctor` -> `--configure-client`) and black-box diagnostic commands (`--doctor` and `llama-mcp-ps`). Pass `--skill-link` to symlink instead of copying.

## Installation & Execution

### Option 1: Global Install from GitHub (End Users)

Installs into an isolated `uv` tool environment and exposes `llama-mcp-bridge` and `llama-mcp-install` globally on `PATH` (required for GUI MCP clients):

```bash
uv tool install git+https://github.com/OakHell/llama-mcp-bridge
uv tool update-shell
```

### Option 2: Global Editable Install from a Local Clone (Active Development)

Exposes the commands globally on `PATH` while reflecting live edits to `src/` immediately:

```bash
git clone https://github.com/OakHell/llama-mcp-bridge.git
cd llama-mcp-bridge
uv tool install --editable .
uv tool update-shell
```

### Option 3: Scoped Local Checkout without Global Install (`uv sync` / `uv run`)

Keeps everything inside the local `.venv/` without modifying `~/.local/bin` or `PATH`:

```bash
uv sync --dev
uv run llama-mcp-install --dry-run
uv run pytest -v
```

> **Zero-install dry run:** You can also run `uvx --from git+https://github.com/OakHell/llama-mcp-bridge llama-mcp-install --dry-run` without installing anything.
> **BSD Note (`FreeBSD / OpenBSD / NetBSD`):** Because PyPI does not publish binary wheels for `*BSD`, install `rust` and `cmake` via system packages (e.g. `pkg install rust cmake`) before `uv tool install`, and use `--mode source-hermetic` or `--mode system-bin` (upstream `llama.cpp` does not publish prebuilt BSD release archives).

### Non-Interactive / Scripted CLI Flags

| Flag | Description |
| :--- | :--- |
| `--mode prebuilt` | Downloads or configures prebuilt `llama.cpp` binaries (default). |
| `--mode source-hermetic` | Builds a self-contained static `llama-server` from source (`-DBUILD_SHARED_LIBS=OFF -DLLAMA_CURL=OFF`) with scrubbed build env vars and `uvx cmake` fallback. |
| `--mode source-system` | Builds `llama-server` dynamically (`-DBUILD_SHARED_LIBS=ON`) using system toolchains/env vars and copies built shared libraries into `<workspace>/bin/`. |
| `--mode system-bin` | Uses an existing `llama-server` binary on `PATH`. |
| `--dry-run` | Prints planned actions without writing files or executing subprocesses. |
| `-y`, `--yes` | Auto-confirms `llama-server` installation when undetected. |
| `--download-models [PRESET ...]` | Downloads curated GGUF starter-pack models for your detected tier, or only specific presets (e.g. `--download-models digest` for lightweight/CPU-only setups). |
| `--presets PRESET [PRESET ...]` | Limits `models.ini` generation to the specified specialist presets (default: all 9 presets). |
| `--verify-models` | Verifies Hugging Face URLs for `--tier` (or all tiers) and suggests live GGUF alternatives on 404. |
| `--tier` | Target VRAM tier (`6gb`, `8gb`, `12gb`, `16gb`, `24gb+`) for `--verify-models`. |
| `--residency` | Memory residency profile (`stingy`, `moderate`, `snappy`; default `moderate`). |
| `--models-dir` | Custom directory for GGUF model files (defaults to `<workspace>/models`). |
| `--ps` | Shows live server health, active residency, and loaded/idle presets (also `llama-mcp-ps`). |
| `--doctor` | Runs an end-to-end diagnostic check of configs, `llama-server`, GGUF files, and `/health`. |
| `--uninstall` | Removes managed `bin/`, build trees, and background service while preserving `.ini` configs and `.gguf` models (supports `--dry-run` and `--yes`). |
| `--configure-client` | Prints ready-to-paste `mcpServers` JSON and official setup docs for your coding harness (pass `--apply` to merge-write `"local-ai"` directly into the harness config). |
| `--tui` | Launches the interactive `rich` TUI setup wizard (also auto-launched on bare `llama-mcp-install` in a TTY). |
| `--install-service` | Generates an OS-specific user service (`systemd` on Linux, `launchd` on macOS). |

> **Hardware Prerequisites:** `llama-mcp-install` audits your GPU/OS tools (`nvidia-smi`, `nvcc`, `rocminfo`, `vulkaninfo`, `xcode-select`, `cmake`) and warns if any BYO drivers or SDKs are missing before offering to install `llama-server`.
> **Source Build Caveats:** `--mode source-hermetic` produces an isolated static binary (`-DBUILD_SHARED_LIBS=OFF -DLLAMA_CURL=OFF`), but still requires a host C/C++ compiler (`cl.exe` on Windows, `xcode-select` on macOS) and GPU SDKs (`nvcc`/`hipcc`) for GPU backends. On macOS, `llama-mcp-install` runs ad-hoc `codesign --force --deep -s -` automatically; if Gatekeeper blocks execution, re-run `codesign --force --deep -s - <workspace>/bin/llama-server`. Prefer `--mode prebuilt` or `--mode source-system` for dynamic GPU/system library linking.

## Model Management (`llama-mcp-models`)

Manage, add, or remove specialist presets anytime after installation without re-running the full installer:

- **Interactive Checkbox Selector:** Run `llama-mcp-models` in a terminal to toggle presets with `Up/Down` (or `j/k`), `Space` to toggle `[x]` / `[ ]`, `a` for all/none, and `Enter` to apply (with a numbered fallback when stdin is not a TTY).
- **List Presets:** `llama-mcp-models list` displays all 9 presets, their `models.ini` status, tier GGUF filenames, disk presence, and sizes.
- **Add Presets:** `llama-mcp-models add digest coder` (or `--add`) merges the requested presets into `models.ini` without duplicating existing `[section]` headers and downloads their GGUFs.
- **Remove Presets Safely:** `llama-mcp-models remove vision` (or `--remove`) prompts for confirmation (`-y` / `--yes` skips the prompt; `--dry-run` previews changes), removes the preset sections from `models.ini`, and only deletes `.gguf` / `mmproj` files on disk if no remaining preset in `models.ini` references them.
- **Custom Models (`--hf` / `--file` & `--tune-mode`):** `llama-mcp-models add my_model --hf <owner>/<repo>[:<quant-or-filename>]` (with automatic `mmproj` detection, `X-Linked-ETag` SHA-256 checksum verification, and resumable `.part` downloads) or `--file <path/to/model.gguf>` (`--mmproj` optional) inspects GGUF binary headers to auto-tune `n-gpu-layers` and `ctx-size` (`--tune-mode conservative|approximate`, default `conservative`), prints a heuristic tuning & client-restart notice, and exposes the preset via `ask_preset` or dynamically registered `ask_<preset>` tools.
- **Self-Healing URL Verification:** `llama-mcp-install --verify-models` checks Hugging Face starter-pack URLs concurrently and suggests live GGUF alternatives on 404.

## Configuration (`LLAMA_MCP_HOME`, `bridge.ini` & `models.ini`)

`llama-mcp-bridge` separates two explicit contracts:
- **`models.ini`** is `llama-server`'s native router-mode preset file—the full `llama-server` flag surface is delegated verbatim.
- **`bridge.ini`** is the bridge's orchestration contract (`host`, `port`, `auto_spawn`, `residency`, `fallback_on_oom`, `health_timeout_s`, `request_timeout_s`, `models_dir`, `models_ini`, `llama_server_bin`).

`LLAMA_MCP_HOME` is a first-class environment variable for targeting any workspace. Resolution precedence:

1. Explicit CLI flag (`--dir` / `--home`).
2. `LLAMA_MCP_HOME` environment variable (e.g. `LLAMA_MCP_HOME=/mnt/ai`).
3. Registered global workspace pointer (`~/.config/llama-mcp-bridge/home` on POSIX, `%APPDATA%\llama-mcp-bridge\home` on Windows) set via `llama-mcp-install --register` (clear with `--unregister`).
4. **POSIX (Linux, macOS, FreeBSD):** `~/.llama-mcp-bridge` / **Windows:** `%LOCALAPPDATA%\llama-mcp-bridge`

- **Residency Profiles (`residency`):** `stingy` (`15s` idle, `1` model), `moderate` (`120s` idle, `1`–`2` models; default), `snappy` (`600s` idle, `2`–`3` models on `12gb+`).
- **OOM & Timeout Recovery (`fallback_on_oom`):** Classifies VRAM OOM vs. cold-swap timeouts from `llama-server` `stderr`; set `fallback_on_oom = true` in `bridge.ini` to run a structured degradation loop (retrying text requests on `digest` and prepending a fallback notice; image requests note that images were dropped).
- **Config-as-Contract & Tinkerability:** Hand-edited `[preset]` sections in `models.ini` are preserved across re-installs and `--install-service`; `llama-mcp-models remove` uses reference-counted GGUF deletion; `--uninstall` preserves `.ini` and `.gguf` files; `--dry-run` and `--doctor` validate without mutating.

## MCP Client Configuration (`mcp_config.json`)

```json
{
  "mcpServers": {
    "local-ai": {
      "command": "llama-mcp-bridge",
      "env": {
        "LLAMA_MCP_HOME": "/mnt/ai"
      }
    }
  }
}
```

### Specialist Tools

Spanning distinct model families decorrelates role failures across extraction, coding, vision, and reasoning:

| Tool | Model Family | Reasoning Budget | Role |
| :--- | :--- | :--- | :--- |
| `ask_digest` | Liquid LFM3 | `0` | High-speed structured JSON/fact extraction |
| `ask_coder_fast` | Qwen 2.5 Coder | `0` | Rapid boilerplate and targeted edits |
| `ask_coder` | Qwen 3.5 | `2048` | Standard code generation and refactoring |
| `ask_coder_long` | Qwen 3.5 | `2048` | Multi-file codebase analysis (tier-scaled context, 16K–128K) |
| `ask_vision` | MiniCPM-V 4.6 | `0` | Fast UI inspection, screenshots, and OCR |
| `ask_vision_deep` | Qwen 3.5 (VLM) | `1024` | Complex visual reasoning and diagrams |
| `ask_reasoner` | IBM Granite 4.2 | `2048` | Mathematical, algorithmic, and logical reasoning |
| `ask_analyst` | Mistral / Ministral 3 | `2048` | Deep technical auditing and edge-case analysis |
| `ask_generalist` | Google Gemma 4 | `1024` | General technical writing and synthesis |
| `ask_preset` | Any configured preset | Per `models.ini` | Immediate invocation of any `[preset]` (including before client reload) |
| `get_bridge_status` | — | — | Read-only JSON health, active VRAM tier, and warm model residency check |

### Reasoning Management

All presets use `reasoning-format = deepseek` with a shared `reasoning-budget-message` cutoff string so thinking models emit a final answer before hitting token limits, paired with deterministic tag stripping in [hygiene.py](src/llama_mcp_bridge/hygiene.py).

## Tradeoffs & Non-Goals

- **Non-Streaming Responses:** MCP tool calls wait for complete responses rather than streaming partial tokens.
- **Client Reload for `tools/list` Discovery:** Newly added custom `ask_<name>` tools appear in `tools/list` after reloading the MCP client, though `ask_preset` can invoke them immediately.
- **Single `llama-server` Process:** All presets share one router-mode `llama-server` instance per workspace.
- **Curated Starter Pack & Backends:** Curated starter models are Hugging Face GGUFs; AMD prebuilt selection scores ROCm/HIP and Vulkan assets; CPU-only `6gb` tier setups trade higher latency for broad compatibility.

## Service Installation (`--install-service`)

- **Linux (`systemd`):** Writes `~/.config/systemd/user/llama-server.service` when `systemd` is active (`systemctl --user enable --now llama-server.service`).
- **Linux (non-`systemd` / Alpine / Void / Gentoo):** Installs a deduplicated `@reboot` user crontab entry (`# llama-mcp-bridge`) and prints an OpenRC reference snippet.
- **macOS (`launchd`):** Writes `com.llama-server.plist` in `LLAMA_MCP_HOME`.
- **Windows:** Writes `<workspace>/start-llama-server.bat` and registers a `LlamaServer` logon task via `schtasks`.
- **BSD (FreeBSD / OpenBSD / NetBSD):** Installs a deduplicated `@reboot` user crontab entry (`# llama-mcp-bridge`) and prints a FreeBSD `rc.d` (`daemon(8)`) template.

## Development

Requires **Python `>=3.11`**. GitHub Actions CI runs on `ubuntu-latest`, `macos-latest`, `windows-latest`, and a real FreeBSD 15 VM (`vmactions/freebsd-vm`) executing `uv sync --dev`, `uv run ruff check .`, `uv run pytest -v`, and `uv run llama-mcp-install --dry-run`.

```bash
uv run pytest -v
uv run ruff check .
```

## Roadmap

See [ROADMAP.md](ROADMAP.md) for upcoming milestones (`v0.9.0`–`v0.12.0`) and [SHIPPED.md](SHIPPED.md) for all shipped releases (`v0.1.0`–`v0.9.0`).

## License

MIT — see [LICENSE](LICENSE).
