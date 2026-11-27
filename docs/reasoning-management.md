# Reasoning Management

Local reasoning models emit internal monologues (`<think>...</think>`, `Thinking Process:`, or tagless reasoning preambles) that can exhaust `max_tokens` or pollute MCP tool responses. `llama-mcp-bridge` manages reasoning across two layers: **server-side budgets** in `models.ini` and **deterministic post-processing** in `hygiene.py`.

## 1. Server-Side Budgets (`presets.py`)

`generate_models_ini` sets `reasoning-format = deepseek` and a shared `reasoning-budget-message` in `[*]`, then assigns per-role `reasoning-budget` values:

| Preset | `reasoning-budget` | Behaviour |
| --- | --- | --- |
| `digest`, `coder-fast`, `vision` | `0` | Reasoning disabled for fast deterministic output |
| `vision-deep`, `generalist` | `1024` | Bounded thinking budget |
| `coder`, `coder-long`, `reasoner`, `analyst` | `2048` | Extended reasoning budget |

When a model hits its `reasoning-budget`, `llama-server` injects `reasoning-budget-message` so the model transitions to emitting its final answer before `max_tokens` runs out.

## 2. Deterministic Hygiene (`hygiene.py`)

`clean_mcp_output` strips reasoning traces from returned text while protecting structured user-facing content:

- **Input Safety (`InputSafetyGuard`):** Preserves `Error:` / HTTP / traceback diagnostics and non-text types untouched.
- **Block Protection (`BlockProtector`):** Masks fenced code blocks, inline code, and LaTeX (`$$...$$`, `\[...\]`) before boundary scanning so reasoning markers inside code or math are never stripped.
- **Boundary Detection (`BoundaryDetector`):** Handles explicit XML tags (`<think>`, `<thought>`, `<reasoning>`), structural dividers (`---`), and tagless monologues, returning the clean final answer.
