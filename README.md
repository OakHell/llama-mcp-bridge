# Llama MCP Bridge

Hardware-aware local LLM MCP bridge powered by `llama.cpp` router mode.

[![Python >=3.11](https://img.shields.io/badge/python-%3E%3D3.11-blue.svg)](https://www.python.org/downloads/)
[![CI](https://github.com/OakHell/llama-mcp-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/OakHell/llama-mcp-bridge/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/OakHell/llama-mcp-bridge?color=green)](https://github.com/OakHell/llama-mcp-bridge/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[Docs](docs/index.md) · [Architecture](ARCHITECTURE.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Changelog](CHANGELOG.md)

**Requires Python >= 3.11.**

## What this is

`llama-mcp-bridge` is a cross-platform (Linux, macOS, Windows, FreeBSD) Model Context Protocol (MCP) server that connects MCP clients to a single router-mode `llama-server` process. It is built for Ollama users who've outgrown Ollama's opinionated defaults without wanting to hand-edit `llama-server` flags.

## Why I built this

I started out running local models behind Ollama, and it worked—until I wanted to know *why* a model was spilling into system RAM, or why a coding prompt was eating 30% of its context window on `<think>` tags. Ollama hides those knobs behind opaque modelfiles. Switching to `llama.cpp`'s native router mode and wrapping it in `fastmcp` gave me exact control over KV-cache quantisation, GPU layer offloading, and per-preset context budgets—at the cost of a wall of CLI flags.

So I built `llama-mcp-bridge` to automate the tedious parts while keeping every decision inspectable in plain INI files:

- **Token savings & output hygiene ([`hygiene.py`](src/llama_mcp_bridge/hygiene.py)):** Strips internal reasoning/monologue tags deterministically while preserving fenced code blocks, LaTeX, and structured JSON.
- **`.gitignore`-brokered file context ([`discovery.py`](src/llama_mcp_bridge/discovery.py)):** Expands files, directories, and globs on the host side within `.gitignore` cascades, `include`/`ignore` rules, binary skipping, and smallest-first character budgets.
- **GGUF-header-derived tuning ([`gguf_header.py`](src/llama_mcp_bridge/gguf_header.py)):** Inspects GGUF v2/v3 binary headers directly (MHA/GQA, SWA, hybrid SSM, DeepSeek MLA, KV-shared layers) to size KV caches from actual model geometry.
- **Honest labeling of extras:** Core local inference is `v1.0.2`-stable; opt-in `[enterprise]` features (`--enterprise`) and background service wrappers are clearly marked as provisional until field-validated.

## Who this is for / not for

**For:**
- Developers who want local specialist models (`digest`, `coder`, `vision`, `reasoner`) wired into Claude Code, Cursor, Cline, Windsurf, Zed, or Antigravity.
- Workstation users on 6 GB–24 GB+ GPUs (or CPU-only fallback) who want deterministic, inspectable `models.ini` tuning.
- Teams wanting zero-copy Ollama GGUF blob imports (`--import-ollama`) without re-downloading weights.

**Not for:**
- Multi-node distributed inference or high-throughput SaaS serving (use vLLM or TGI).
- Agentic tool-calling inside the local model itself (specialists are strictly agency-free).
- Zero-config cloud API proxying.

## Quick Start

Preview what the installer will do without touching disk or network:

```bash
uvx --from git+https://github.com/OakHell/llama-mcp-bridge llama-mcp-install --dry-run
```

Install and launch the setup wizard:

```bash
uv tool install git+https://github.com/OakHell/llama-mcp-bridge
uv tool update-shell
llama-mcp-install          # interactive TUI setup (choose "download models" inside)
# or non-interactive:
llama-mcp-install -y --download-models
```

For agent self-bootstrap, run `llama-mcp-install --install-skill` (see [docs/install.md](docs/install.md) and [SKILL.md](skills/llama-mcp-setup/SKILL.md)). See [docs/install-wheel.md](docs/install-wheel.md) for pinned/offline release wheels.

Verify your setup and wire it into your MCP client:

```bash
llama-mcp-install --doctor      # Standby is normal when auto_spawn = true
llama-mcp-install --selftest    # Live end-to-end ask_digest('ping') check
```

Add to your MCP client's `mcpServers` config (then reload/restart the client; see [docs/config.md](docs/config.md) for `LLAMA_MCP_HOME` details):

```json
{
  "mcpServers": {
    "local-ai": {
      "command": "llama-mcp-bridge",
      "env": {
        "LLAMA_MCP_HOME": "/path/to/workspace"
      }
    }
  }
}
```

Hit an issue? Jump to [Troubleshooting](docs/troubleshooting.md).

## Specialist Tools

7 GGUF models act as 9 specialist presets: on `8gb`–`16gb`, `coder`, `coder-long`, and `vision-deep` share `Qwen3.5-9B-UD-Q4_K_XL.gguf` (with `mmproj-BF16.gguf` attached for `vision-deep`), while `24gb+` swaps `coder-fast`, `coder`, and `coder-long` to `Qwen2.5-Coder-14B`.

| Tool | Role | Model Family |
| :--- | :--- | :--- |
| `ask_digest` | High-speed structured JSON/fact extraction | Liquid LFM3 |
| `ask_coder_fast` | Rapid boilerplate and targeted edits | Qwen 2.5 Coder |
| `ask_coder` | Standard code generation and refactoring | Qwen 3.5 |
| `ask_coder_long` | Multi-file codebase analysis (16K–128K) | Qwen 3.5 |
| `ask_vision` | Fast UI inspection, screenshots, and OCR | MiniCPM-V 4.6 |
| `ask_vision_deep` | Complex visual reasoning and diagrams | Qwen 3.5 (VLM) |
| `ask_reasoner` | Mathematical, algorithmic, and logical reasoning | IBM Granite 4.2 |
| `ask_analyst` | Deep technical auditing and edge-case analysis | Mistral / Ministral 3 |
| `ask_generalist` | General technical writing and synthesis | Google Gemma 4 |

See [docs/tools.md](docs/tools.md) for dispatcher tools (`ask_preset`, `ask_batch`), `get_bridge_status`, `json_schema` grammar constraints, and the Reasoning Management stack.

## Hardware Tiers

| Tier | Typical Hardware | KV Cache (`K`/`V`) | `coder` / `coder-long` Context | Parallelism |
| :--- | :--- | :--- | :--- | :--- |
| `6gb` | 6 GB GPUs / CPU fallback (`>=16 GB` RAM recommended) | `q4_0` / `q4_0` | 8K / 16K | 1 |
| `8gb` | 8 GB GPUs (e.g. RTX 3070) | `q8_0` / `q8_0` (`q4_0` long) | 16K / 32K | 1 |
| `12gb` | 12 GB GPUs (e.g. RTX 3060/4070) | `q8_0` / `q8_0` | 24K / 40K | 1 |
| `16gb` | 16 GB GPUs (e.g. RTX 4080 / M2 Pro 32GB) | `q8_0` / `q8_0` | 32K / 64K | 2 |
| `24gb+` | 24 GB+ GPUs (e.g. RTX 4090 / M2 Max 64GB) | `q8_0` / `q8_0` | 64K / 128K | 4 |

> **Note:** On `24gb+`, `coder`, `coder-fast`, and `coder-long` swap to `Qwen 2.5-Coder-14B` (`qwen2.5-coder-14b-instruct-q4_k_m.gguf`); `131072` context uses `q4_0` KV cache and can be lowered via `llama-mcp-models` if tight on 24 GB VRAM.

## Common Operations

```bash
llama-mcp-models add my-model --hf owner/repo:Q4_K_M
llama-mcp-models remove my-model
llama-mcp-install --import-ollama   # --ollama-dir to override ~/.ollama/models
llama-mcp-install --doctor
llama-mcp-ps
llama-mcp-install --check-update
```

See [docs/models.md](docs/models.md) and [docs/cli.md](docs/cli.md) for the full command reference.

## Configuration

The bridge reads `bridge.ini` (`[bridge]` with `schema_version = 1`) and `models.ini` from `LLAMA_MCP_HOME` (defaulting to `~/.llama-mcp-bridge` on POSIX or `%LOCALAPPDATA%\llama-mcp-bridge` on Windows). See [docs/config.md](docs/config.md) for all keys, residency profiles, and cross-platform path examples.

## Security Model

All specialist models run strictly **agency-free**: they have zero function-calling, subprocess, or network-egress capabilities. File and image context is brokered by the bridge within `.gitignore` cascades, structural `DEFAULT_IGNORES` (`.git`, `.venv`, `node_modules`, `__pycache__`), and character budgets.

`llama-server` binds to `127.0.0.1` by default, archive extraction enforces Zip Slip / Tar traversal and size bounds, and release downloads support Sigstore/Cosign verification. See [SECURITY.md](SECURITY.md) for the full threat model and supported versions.

## Enterprise & Stability

Core `llama-mcp-bridge` (`v1.0.2`) is covered by Semantic Versioning in [STABILITY.md](STABILITY.md). Opt-in enterprise capabilities (`--enterprise`, `E3` rotating JSONL audit logs, `E7` `/livez` + `/readyz` + `systemd` watchdog probes, `E11` multi-tenant isolation) are documented in [docs/enterprise.md](docs/enterprise.md).

> **Warning:** `--enterprise` (`E3`, `E7`, `E11`) is verified by automated unit and chaos tests only and has **not yet been field-validated in a live multi-tenant production environment**.

## Troubleshooting FAQ

1. **`--doctor` shows `Standby`:** Normal when `auto_spawn = true`; `llama-server` spawns lazily on the first tool call.
2. **New `ask_<name>` tool missing in client:** Restart/reload your MCP client, or call `ask_preset(preset="<name>", ...)` immediately.
3. **VRAM OOM or cold-swap timeout:** Set `residency = stingy` or `fallback_on_oom = true` in `bridge.ini`, or lower `ctx-size` in `models.ini`.
4. **Windows `llama-server.exe` missing DLLs (`cudart64_*.dll`, `cublas64_*.dll`, `cublasLt64_*.dll`):** Re-run `llama-mcp-install --mode prebuilt -y` to co-locate companion CUDA runtime DLLs.
5. **macOS Gatekeeper / `codesign` errors:** Run `codesign --force --deep -s - <workspace>/bin/llama-server` (or re-run `llama-mcp-install --mode prebuilt -y`).

See [docs/troubleshooting.md](docs/troubleshooting.md) for detailed diagnostics.

## Documentation & License

- [docs/cli.md](docs/cli.md) · [docs/config.md](docs/config.md) · [docs/tools.md](docs/tools.md) · [docs/models.md](docs/models.md)
- [docs/install.md](docs/install.md) · [docs/install-wheel.md](docs/install-wheel.md) · [docs/install-bsd.md](docs/install-bsd.md)
- [docs/advanced.md](docs/advanced.md) · [docs/services.md](docs/services.md) · [docs/enterprise.md](docs/enterprise.md) · [docs/troubleshooting.md](docs/troubleshooting.md)
- [ARCHITECTURE.md](ARCHITECTURE.md) · [STABILITY.md](STABILITY.md) · [ROADMAP.md](ROADMAP.md) · [TRACKER.md](TRACKER.md) · [SHIPPED.md](SHIPPED.md) · [SKILL.md](skills/llama-mcp-setup/SKILL.md) · [CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md)

MIT License — see [LICENSE](LICENSE).
