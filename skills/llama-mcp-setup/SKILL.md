---
name: llama-mcp-setup
description: Bootstraps, configures, and troubleshoots llama-mcp-bridge for local LLM inference, llama.cpp server management, GGUF model downloads, MCP server setup, and offloading tasks to local specialist models.
---

# `llama-mcp-setup` — Autonomous Local Model Bootstrap

Follow this deterministic workflow when setting up, wiring, or diagnosing `llama-mcp-bridge` and local GGUF specialist models.

## 1. Preflight & Non-Login Shell PATH Setup

Non-login shells often omit `~/.local/bin` (POSIX) or `%USERPROFILE%\.local\bin` (Windows). Prepend it before checking commands:

- **POSIX (`sh` / `bash` / `zsh`):**
  ```bash
  export PATH="$HOME/.local/bin:$PATH"
  command -v llama-mcp-install || uv tool install git+https://github.com/OakHell/llama-mcp-bridge
  ```
- **Windows (PowerShell):**
  ```powershell
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
  if (-not (Get-Command llama-mcp-install -ErrorAction SilentlyContinue)) { uv tool install git+https://github.com/OakHell/llama-mcp-bridge }
  ```
- Run `llama-mcp-install --doctor` to check whether a workspace is already configured and healthy.

## 2. Autonomous Bootstrap

- **Default workspace** (`~/.llama-mcp-bridge` on POSIX, `%LOCALAPPDATA%\llama-mcp-bridge` on Windows):
  ```bash
  llama-mcp-install --yes --download-models --install-service
  ```
- **Custom directory / secondary drive** (registers globally so `LLAMA_MCP_HOME` is not needed):
  ```bash
  # POSIX example:
  llama-mcp-install --dir /mnt/ai --register --yes --download-models --install-service
  # Windows example:
  llama-mcp-install --dir D:\ai --register --yes --download-models --install-service
  ```
- **Single-preset lightweight install** (e.g. CPU-only or minimal footprint):
  ```bash
  llama-mcp-install --presets digest --download-models digest --yes --install-service
  ```

## 3. Verify & Wire MCP Client

1. Run `llama-mcp-install --doctor` (`Server /health: PASS (Standby)` is healthy when `auto_spawn = true`).
2. Print the `"local-ai"` MCP JSON snippet without modifying external files by default:
   ```bash
   llama-mcp-install --configure-client <claude-code|cursor|cline|windsurf|zed|antigravity|other>
   ```
   Only pass `--apply` if the user explicitly authorizes modifying their harness config (`~/.gemini/config/mcp_config.json`, `~/.claude.json`, `~/.cursor/mcp.json`, etc.).
3. Instruct the user to **restart or reload their MCP client** so `tools/list` refreshes with newly configured `ask_<preset>` tools (note: `ask_preset` can call newly added presets immediately without a restart).

## 4. Model Management & Black-Box Troubleshooting

### Adding / Tuning Models
- Add curated presets: `llama-mcp-models add <preset>`
- Add any Hugging Face or local GGUF model with header-based KV-cache auto-tuning and optional `--tool-name`:
  ```bash
  llama-mcp-models add my_coder --hf <owner>/<repo>:<quant> --tune-mode conservative --tool-name qwen_coder
  llama-mcp-models add my_model --file /path/to/model.gguf --tune-mode approximate
  ```
- Remind the user to restart active MCP clients whenever `[preset]` sections are added or removed so `tools/list` updates.

### Black-Box Diagnostic Decision Tree
Always run black-box CLI diagnostics before inspecting source code or logs:

1. **`llama-mcp-install --doctor`** — Checks `bridge.ini` & `models.ini`, `llama-server` binary permissions, GGUF files, and `/health` (`Standby` when `auto_spawn = true`).
2. **`llama-mcp-ps`** — Shows `ONLINE` / `STANDBY (auto-spawn)` / `OFFLINE`, active `residency`, VRAM tier, and `LOADED` / `IDLE` presets.
3. **`llama-mcp-install --verify-models`** — Checks Hugging Face starter-pack URLs and suggests live GGUF alternatives on 404.
3. **Common Failures:**
   - *Missing GGUF files*: Run `llama-mcp-models add <preset>` or `llama-mcp-install --download-models`.
   - *`Preset '<name>' is not configured in models.ini`*: Run `llama-mcp-models add <name>`.
   - *VRAM OOM or cold-swap timeout*: Set `residency = stingy` or `fallback_on_oom = true` in `bridge.ini`, or re-run `llama-mcp-models add` with `--tune-mode conservative`.
