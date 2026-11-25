# Agent Operating Rules — `llama-mcp-bridge`

## 1. Environment & Tooling
- **Package & Test Runner:** Exclusively use `uv` (`uv run pytest`, `uv run ruff check .`, `uv sync`). Never instruct the user to create or activate a manual `venv` or use raw `pip`.
- **Python Target:** Python 3.11+ (`src/llama_mcp_bridge/` layout).
- **Language:** Use British/South African English spelling in documentation and comments where applicable (e.g., `initialise`, `serialise`, `behaviour`).

## 2. Hard Safety Boundaries
- **Read-Only Reference Directory (`/mnt/ai`):** Never modify, delete, or write files inside `/mnt/ai` or `~/.gemini/antigravity/mcp/`. Treat `/mnt/ai/local_bridge.py`, `/mnt/ai/hygiene.py`, and `/mnt/ai/models.ini` strictly as read-only reference specifications.
- **No Unsolicited Model Downloads:** Never download `.gguf` weights during tests or dry-runs. All unit tests must mock network calls and subprocess probes.

## 3. Local Specialist Delegation (`local-ai` MCP)
Delegate heavy inspection, boilerplate, auditing, and verification to the `local-ai` MCP tools rather than bloating cloud context:
- `ask_digest`: Structured JSON extraction from configs, logs, and schemas.
- `ask_coder_fast`: Rapid boilerplate (`pyproject.toml`, `bridge.example.ini`, `.github/workflows/ci.yml`, `pytest` fixtures).
- `ask_coder` / `ask_coder_long`: Modularizing `bridge.py`, `hardware.py`, `installer.py`, and `supervisor.py`.
- `ask_analyst`: Auditing cross-platform edge cases (Windows paths, macOS unified memory, Linux ROCm/CUDA, FreeBSD fallback).
- `ask_reasoner`: Verifying VRAM headroom math and `models.ini` tier budgets.

## 4. Verification Standard
- Every phase must end with `uv run pytest` passing with zero errors before moving to the next phase.
- Any changes to `hardware.py` must pass the golden 8 GB NVIDIA `sm_86` test asserting exact parity with `/mnt/ai/models.ini` tuning parameters.

## 5. Anti-Truncation & Completeness Rules
- **Copy-Then-Patch for Existing Code:** Never ask `local-ai` tools to regenerate files over 150 lines from scratch. Always copy the `/mnt/ai/` reference file first (`cp /mnt/ai/local_bridge.py src/llama_mcp_bridge/bridge.py`) and apply targeted, small edits.
- **Small Modular Files:** Keep new modules (`config.py`, `supervisor.py`, `hardware.py`, `installer.py`) under ~180 lines each so `ask_coder` / `ask_coder_long` never exhaust `max_tokens` mid-file.
- **Hard Phase Gate:** Before advancing to the next phase, run:
  1. `uv run python -m compileall src tests` (catches any truncated syntax immediately)
  2. `uv run pytest -v` (verifies all expected modules and tests exist and pass)
- **No Silent Stubs:** Never leave `TODO`, `pass`, or placeholder functions in `src/llama_mcp_bridge/`.

## 6. Single-Responsibility Tool Calls
- **One Module Per `local-ai` Call:** Never ask `ask_coder` or `ask_coder_long` to combine hardware probing, INI/config generation, and CLI logic in a single prompt. Split distinct concerns into separate files (`hardware.py` for probes vs. `presets.py` for `models.ini` generation) and generate one file per tool call.

## 7. Test Integrity & Anti-Cheating Rule
- **Never Weaken Tests to Force a Pass:** When a test fails, fix the production code in `src/`—never delete assertions, loosen expected values, or strip edge cases from `tests/` just to make `pytest` exit `0`.
