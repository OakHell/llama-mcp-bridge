# Model Management (`llama-mcp-models`)

The curated starter pack uses **7 GGUF models acting as 9 specialist presets**: on `8gb`–`16gb`, `coder`, `coder-long`, and `vision-deep` share `Qwen3.5-9B-UD-Q4_K_XL.gguf` (with `mmproj-BF16.gguf` attached for `vision-deep`), while `24gb+` swaps `coder-fast`, `coder`, and `coder-long` to `Qwen2.5-Coder-14B`.

## Listing & Inspecting Presets

```bash
llama-mcp-models list
llama-mcp-install --model-info coder
```

## Adding Custom Hugging Face or Local GGUF Models

```bash
llama-mcp-models add my-model --hf owner/repo:Q4_K_M --tune-mode conservative
llama-mcp-models add local-model --file /path/to/model.gguf --tool-name ask_local
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

`--prune` removes unreferenced `.gguf` files (case-insensitive, including `.GGUF`) and stale `.part` / `.gguf.tmp` partials (`include_partials=True`) in `models_dir`.

## Downloads & Verifying Curated URLs

```bash
llama-mcp-install --verify-models --no-cache
```

- **Disk-Space Preflight & Resume:** Downloads check free disk space against HEAD `Content-Length` (falling back to registry `size_gb`), validate HTTP `416` `Content-Range` headers before finalizing `.part` files, and retry transient chunk-read errors with exponential backoff.
- **SHA-256 & Size-Drift Checks:** On SHA-256 mismatch, the `.part` file is preserved as `<filename>.part.corrupt` for inspection. `--verify-models` flags >20% size drift from registry expectations.
