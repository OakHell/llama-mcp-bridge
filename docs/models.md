# Model Management (`llama-mcp-models`)

## Listing & Inspecting Presets

```bash
llama-mcp-models list
llama-mcp-install --model-info coder
```

## Adding Custom Hugging Face or Local GGUF Models

```bash
llama-mcp-models add --hf owner/repo:Q4_K_M --preset my-model --tune-mode conservative
llama-mcp-models add --file /path/to/model.gguf --preset local-model --tool-name ask_local
```

`--tune-mode conservative|approximate` inspects the GGUF v2/v3 header (`gguf_header.py`) to size KV caches and context windows for your detected VRAM/RAM. See [gguf-auto-tuning.md](gguf-auto-tuning.md).

## Zero-Copy Ollama Import

Import already-downloaded Ollama GGUF blobs via symlinks without duplicating weights:

```bash
llama-mcp-install --import-ollama
```

Pass `--ollama-dir /path/to/ollama/models` to override the default `~/.ollama/models`.

## Removing & Pruning

```bash
llama-mcp-models remove my-model
llama-mcp-models --prune
```

`--prune` removes unreferenced `.gguf` files in `models_dir` that are no longer referenced by `models.ini`.

## Verifying Curated URLs

```bash
llama-mcp-install --verify-models --no-cache
```
