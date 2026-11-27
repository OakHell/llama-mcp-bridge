# MCP Tools & Reasoning Management

## Specialist Tools

| Tool | Model Family | Reasoning Budget | Role |
| :--- | :--- | :--- | :--- |
| `ask_digest` | Liquid LFM3 | `0` | High-speed structured JSON/fact extraction (supports `json_schema`) |
| `ask_coder_fast` | Qwen 2.5 Coder | `0` | Rapid boilerplate and targeted edits |
| `ask_coder` | Qwen 3.5 | `2048` | Standard code generation and refactoring |
| `ask_coder_long` | Qwen 3.5 | `2048` | Multi-file codebase analysis (16K–128K) |
| `ask_vision` | MiniCPM-V 4.6 | `0` | Fast UI inspection, screenshots, and OCR |
| `ask_vision_deep` | Qwen 3.5 (VLM) | `1024` | Complex visual reasoning and diagrams |
| `ask_reasoner` | IBM Granite 4.2 | `2048` | Mathematical, algorithmic, and logical reasoning |
| `ask_analyst` | Mistral / Ministral 3 | `2048` | Deep technical auditing and edge-case analysis |
| `ask_generalist` | Google Gemma 4 | `1024` | General technical writing and synthesis |

All specialist tools support `stream=True` (opt-in SSE accumulation) and `timeout_s` overrides.

## Dispatcher Tools

| Tool | Role |
| :--- | :--- |
| `ask_preset` | Immediate invocation of any `[preset]` in `models.ini` (supports `json_schema`, `stream=True`, `timeout_s`). |
| `ask_batch` | Batch prompt execution bounded by the preset's `parallel` slot count; when `parallel = 1`, `asyncio.Semaphore(1)` serializes requests and returns results in submission order. |

## Diagnostics / Meta

| Tool | Role |
| :--- | :--- |
| `get_bridge_status` | Read-only JSON health, active VRAM tier, and warm model residency check. |

## Reasoning Management

All presets use `reasoning-format = deepseek` with a shared `reasoning-budget-message` cutoff string so thinking models emit a final answer before hitting token limits, paired with deterministic tag stripping in [`hygiene.py`](../src/llama_mcp_bridge/hygiene.py). See [reasoning-management.md](reasoning-management.md).
