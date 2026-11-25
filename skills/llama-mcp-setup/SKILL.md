---
name: llama-mcp-setup
description: Bootstraps, configures, and troubleshoots llama-mcp-bridge for local LLM inference, llama.cpp server management, GGUF model downloads, MCP server setup, and offloading tasks to local specialist models.
---

# `llama-mcp-setup` — Autonomous Local Model Bootstrap

Follow this deterministic workflow when setting up, wiring, or diagnosing `llama-mcp-bridge` and local GGUF specialist models.

## 1. Preflight & Workspace Check

1. Check if `llama-mcp-install` is on `PATH`:
   - If missing, install globally:
     `uv tool install git+https://github.com/OakHell/llama-mcp-bridge` (or `uv tool install llama-mcp-bridge`).
2. Check whether a workspace is already configured and healthy:
   - Run `llama-mcp-install --doctor`.
   - If all checks pass (`Config files`, `llama-server`, `GGUF files`), skip to Step 3.

## 2. Autonomous Bootstrap

- Default location (`~/.llama-mcp-bridge` on POSIX, `%LOCALAPPDATA%\llama-mcp-bridge` on Windows):
  ```bash
  llama-mcp-install --yes --download-models
  ```
- Secondary drive or custom directory (registers globally so `LLAMA_MCP_HOME` is not needed):
  ```bash
  llama-mcp-install --dir /path/to/workspace --register --yes --download-models
  ```
- Optional native background service (systemd / launchd / Task Scheduler): append `--install-service`.

## 3. Verify & Wire MCP Client

1. Run `llama-mcp-install --doctor` to confirm `llama-server` and referenced GGUF files pass.
2. Print the exact `mcpServers` JSON for the user's harness without mutating external configs:
   ```bash
   llama-mcp-install --configure-client <claude-code|cursor|cline|windsurf|zed|antigravity|other>
   ```
3. Instruct the user to **restart or reload their MCP client** so `tools/list` refreshes with `ask_digest`, `ask_coder_fast`, `ask_coder`, `ask_coder_long`, `ask_vision`, `ask_vision_deep`, `ask_reasoner`, `ask_analyst`, `ask_generalist`, and any custom `ask_<preset>` tools.

## 4. Model Management & Black-Box Troubleshooting

### Adding / Tuning Models
- Add curated presets: `llama-mcp-models add <preset>`
- Add any Hugging Face or local GGUF model with header-based KV-cache auto-tuning:
  ```bash
  llama-mcp-models add <preset> --hf <owner>/<repo>:<quant> --tune-mode conservative
  llama-mcp-models add <preset> --file /path/to/model.gguf --tune-mode approximate
  ```
- Remind the user to restart active MCP clients whenever custom `[preset]` sections are added or removed.

### Black-Box Diagnostic Decision Tree
Always run black-box CLI diagnostics before inspecting source code or logs:

1. **`llama-mcp-install --doctor`** — Checks `bridge.ini` & `models.ini` validity, `llama-server` binary permissions, GGUF presence, and `/health`.
2. **`llama-mcp-ps`** — Shows live `ONLINE`/`OFFLINE` state, active `residency`, VRAM tier, and `LOADED`/`IDLE` presets.
3. **Common Failures:**
   - *Missing GGUF files*: Run `llama-mcp-models add <preset>` or `llama-mcp-install --download-models`.
   - *`Preset '<name>' is not configured in models.ini`*: Run `llama-mcp-models add <name>`.
   - *VRAM OOM or cold-swap timeout*: Set `residency = stingy` or `fallback_on_oom = true` in `bridge.ini`, or re-run `llama-mcp-models add` with `--tune-mode conservative`.
