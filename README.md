# Llama MCP Bridge

A cross-platform (Linux, macOS, Windows, FreeBSD) Model Context Protocol (MCP) bridge connecting MCP clients to a local `llama-server` instance. It automatically profiles your GPU VRAM (or CPU fallback) and generates tuned `models.ini` presets—selecting optimal GGUF quantisations, KV-cache precision, and context windows for your exact memory constraints.

## Architecture & Features

- **VRAM-Aware Auto-Tuning:** Profiles NVIDIA, AMD ROCm, Apple Silicon, Intel/Vulkan, or CPU fallback and generates `models.ini` with tier-matched GGUF quantisations, KV-cache precision (`q4_0`–`q8_0`), and context sizes.
- **Specialist MCP Tools:** 9 dedicated tools (`ask_digest`, `ask_coder_fast`, `ask_coder`, `ask_coder_long`, `ask_vision`, `ask_vision_deep`, `ask_reasoner`, `ask_analyst`, `ask_generalist`) routed to tuned local GGUF presets.
- **Output Hygiene Engine:** Strips internal reasoning/monologue tags while preserving fenced code blocks, LaTeX, and structured content.
- **Gitignore-Aware File Discovery:** Respects `.gitignore` cascades, `!` negations, `include > ignore` precedence, binary skipping, and smallest-first context budgets.
- **Lazy Process Supervision:** Automatically spawns `llama-server` on demand and polls `/health` before dispatching requests.

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
| `--configure-client` | Prints ready-to-paste `mcpServers` JSON and official setup docs for your coding harness without modifying external files. |
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
- **Custom Models (Upcoming `v0.6.0` / `v0.7.0`):** Curated tier presets are provided out of the box; `v0.6.0` adds `--hf <owner>/<repo>[:<quant-or-filename>]` and `--file <path/to/model.gguf>` to `llama-mcp-models add` with a generic baseline `models.ini` tuning notice and MCP client restart reminder (`tools/list`), and `v0.7.0` adds GGUF binary header KV-cache auto-sizing via `--tune-mode conservative|approximate`.

## Configuration (`LLAMA_MCP_HOME` & `bridge.ini`)

`llama-mcp-bridge` resolves its workspace root in the following order:

1. `LLAMA_MCP_HOME` environment variable (e.g. `LLAMA_MCP_HOME=/mnt/ai`).
2. **POSIX (Linux, macOS, FreeBSD):** `~/.llama-mcp-bridge`
3. **Windows:** `%LOCALAPPDATA%\llama-mcp-bridge`

> **Upcoming (`v0.6.0`):** Global workspace enrolment (`llama-mcp-install --register` / active home pointer) will persist custom installation paths (such as `/mnt/ai`) across all `llama-mcp-*` commands and MCP clients without needing to pass `LLAMA_MCP_HOME` every time.

Relative paths in `bridge.ini` (`models_dir`, `models_ini`, `llama_server_bin`) resolve relative to `workspace`. See [bridge.example.ini](bridge.example.ini) for all options (`host`, `port`, `auto_spawn`, `residency`, `health_timeout_s`, `request_timeout_s`, `models_ini`).

- **Residency Profiles (`residency`):** `stingy` (`15s` idle, `1` model), `moderate` (`120s` idle, `1`–`2` models; default), `snappy` (`600s` idle, `2`–`3` models on `12gb+`).
- **OOM & Timeout Recovery (`fallback_on_oom`):** Classifies VRAM OOM vs. cold-swap timeouts with actionable recovery steps; set `fallback_on_oom = true` in `bridge.ini` to auto-retry text requests on `digest`.

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

| Tool | Role |
| :--- | :--- |
| `ask_digest` | High-speed structured JSON/fact extraction |
| `ask_coder_fast` | Rapid boilerplate and targeted edits |
| `ask_coder` | Standard code generation and refactoring |
| `ask_coder_long` | Multi-file codebase analysis (tier-scaled context, 16K–128K) |
| `ask_vision` | Fast UI inspection, screenshots, and OCR |
| `ask_vision_deep` | Complex visual reasoning and diagrams |
| `ask_reasoner` | Mathematical, algorithmic, and logical reasoning |
| `ask_analyst` | Deep technical auditing and edge-case analysis |
| `ask_generalist` | General technical writing and synthesis |
| `get_bridge_status` | Read-only JSON health, active VRAM tier, and warm model residency check |

## Service Installation (`--install-service`)

- **Linux (`systemd`):** Writes `~/.config/systemd/user/llama-server.service` when `systemd` is active (`systemctl --user enable --now llama-server.service`).
- **Linux (non-`systemd` / Alpine / Void / Gentoo):** Installs a deduplicated `@reboot` user crontab entry (`# llama-mcp-bridge`) and prints an OpenRC reference snippet.
- **macOS (`launchd`):** Writes `com.llama-server.plist` in `LLAMA_MCP_HOME`.
- **Windows:** Writes `<workspace>/start-llama-server.bat` and registers a `LlamaServer` logon task via `schtasks`.
- **BSD (FreeBSD / OpenBSD / NetBSD):** Installs a deduplicated `@reboot` user crontab entry (`# llama-mcp-bridge`) and prints a FreeBSD `rc.d` (`daemon(8)`) template.

## Development

```bash
uv run pytest -v
uv run ruff check .
```

## Roadmap

See [ROADMAP.md](ROADMAP.md) for the full release history (`v0.1.0`–`v0.5.x`) and upcoming milestones: `v0.6.0` (custom `--hf` / local `--file` GGUFs, baseline tuning notice, MCP client restart reminder, and global workspace enrolment via `--register`), `v0.7.0` (GGUF binary header KV-cache heuristics via `--tune-mode conservative|approximate`), and `v0.8.0` (bundled `skills/llama-mcp-setup/SKILL.md` + `llama-mcp-install --install-skill`).

## License

MIT — see [LICENSE](LICENSE).
