# Llama MCP Bridge

A cross-platform Model Context Protocol (MCP) bridge connecting MCP clients to a local `llama-server` instance.

## Architecture & Features

- **Specialist MCP Tools:** 9 dedicated tools (`ask_digest`, `ask_coder_fast`, `ask_coder`, `ask_coder_long`, `ask_vision`, `ask_vision_deep`, `ask_reasoner`, `ask_analyst`, `ask_generalist`) routed to tuned local GGUF presets.
- **Output Hygiene Engine:** Strips internal reasoning/monologue tags while preserving fenced code blocks, LaTeX, and structured content.
- **Gitignore-Aware File Discovery:** Respects `.gitignore` cascades, `!` negations, `include > ignore` precedence, binary skipping, and smallest-first context budgets.
- **Lazy Process Supervision:** Automatically spawns `llama-server` on demand and polls `/health` before dispatching requests.
- **Cross-Platform Hardware Profiling:** Detects NVIDIA, AMD ROCm, Apple Silicon, Intel/Vulkan, and CPU fallbacks across Linux, macOS, Windows, and FreeBSD, generating tuned `models.ini` presets.

## Installation & Execution

### Via `uv tool install` (recommended)

```bash
uv tool install llama-mcp-bridge
uv tool update-shell
```

Running `uv tool update-shell` ensures `llama-mcp-bridge` and `llama-mcp-install` are on your `PATH`.

### Via `uv run` (local checkout)

```bash
uv sync
uv run llama-mcp-install --dry-run
uv run llama-mcp-bridge
```

### Installer Modes (`--mode`)

| Flag | Description |
| :--- | :--- |
| `--mode prebuilt` | Downloads or configures prebuilt `llama.cpp` binaries (default). |
| `--mode source-hermetic` | Builds `llama.cpp` from source in a hermetic environment. |
| `--mode source-system` | Builds `llama.cpp` from source using system toolchains. |
| `--mode system-bin` | Uses an existing `llama-server` binary on `PATH`. |
| `--dry-run` | Prints planned actions without writing files or executing subprocesses. |
| `--install-service` | Generates an OS-specific user service (`systemd` on Linux, `launchd` on macOS). |

## Configuration (`LLAMA_MCP_HOME` & `bridge.ini`)

`llama-mcp-bridge` resolves its workspace root in the following order:

1. `LLAMA_MCP_HOME` environment variable (e.g. `LLAMA_MCP_HOME=/mnt/ai`).
2. **POSIX (Linux, macOS, FreeBSD):** `~/.llama-mcp-bridge`
3. **Windows:** `%LOCALAPPDATA%\llama-mcp-bridge`

Relative paths in `bridge.ini` (`models_dir`, `models_ini`, `llama_server_bin`) resolve relative to `workspace`. See [bridge.example.ini](bridge.example.ini) for all options (`host`, `port`, `auto_spawn`, `health_timeout_s`, `request_timeout_s`, `models_ini`).

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
| `ask_coder_long` | Multi-file codebase analysis (32K context) |
| `ask_vision` | Fast UI inspection, screenshots, and OCR |
| `ask_vision_deep` | Complex visual reasoning and diagrams |
| `ask_reasoner` | Mathematical, algorithmic, and logical reasoning |
| `ask_analyst` | Deep technical auditing and edge-case analysis |
| `ask_generalist` | General technical writing and synthesis |

## Running Alongside `systemd` on Linux

```bash
llama-mcp-install --mode system-bin --install-service
systemctl --user daemon-reload
systemctl --user enable --now llama-server.service
```

## Development

```bash
uv run pytest -v
uv run ruff check .
```
