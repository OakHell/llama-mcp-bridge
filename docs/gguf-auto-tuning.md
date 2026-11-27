# GGUF Header Auto-Tuning

When adding uncurated models via `llama-mcp-models add <name> --hf ...` or `--file ...`, `gguf_header.py` parses the GGUF v2/v3 binary header directly in Python (zero external dependencies) to estimate KV-cache memory and recommend safe `ctx-size`, `cache-type-k`/`cache-type-v`, and `n-gpu-layers`.

## Supported Attention Layouts

`parse_gguf_header` extracts tensor metadata and architecture-specific keys (`*.block_count`, `*.attention.head_count_kv`, `*.attention.key_length`, `*.attention.value_length`, `*.attention.sliding_window`, `*.ssm.*`, `*.kv_lora_rank`, `*.kv_shared_layers`) across six layouts:

1. **Standard MHA / GQA:** Uniform `head_count_kv` across all layers.
2. **Per-Layer GQA Variance:** Array-valued `head_count_kv` where different layers use different KV head counts.
3. **Sliding Window Attention (SWA-Alternating):** Models mixing full-context layers with bounded `sliding_window` layers (such as Gemma 3/4), capping KV cache growth on SWA layers.
4. **Hybrid SSM / Mamba State:** Accounts for fixed recurrent state overhead alongside attention KV cache.
5. **DeepSeek MLA:** Uses compressed latent `kv_lora_rank` + RoPE dimensions instead of full K/V head projections.
6. **KV-Shared Layers:** Excludes shared-KV layers from duplicate KV-cache allocation.

## Tuning Modes (`--tune-mode`)

- **`conservative` (default):** Targets lower KV-cache VRAM pressure (prefers `q8_0`/`q4_0` KV quantisation and safe context sizes within detected VRAM headroom).
- **`approximate`:** Allows larger `ctx-size` budgets when headroom permits.
- **Partial GPU Offload:** When `vram_gb` cannot fit the full model + KV cache, `recommend_gguf_tuning` calculates proportional `n-gpu-layers` (or `0` on CPU-only systems).
