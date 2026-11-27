# Advanced Runtime & Multi-Workspace Options

## Bearer Auth (`api_key` / `LLAMA_MCP_API_KEY`)

Set `api_key = <secret>` in `bridge.ini` or export `LLAMA_MCP_API_KEY=<secret>`. The bridge passes `--api-key` to `llama-server` and attaches `Authorization: Bearer <secret>` headers to all HTTP probes and inference calls.

## Named Multi-Workspaces

Register named profiles and target them by name:

```bash
llama-mcp-install --dir /path/to/work --register-name work
llama-mcp-bridge --workspace-name work
```

Or set `LLAMA_MCP_WORKSPACE=work`.

## Warmup Preloading (`--warmup`)

Preload configured presets asynchronously on startup:

```bash
llama-mcp-bridge --warmup digest,coder
```

Or set `warmup_presets = digest,coder` in `bridge.ini`.

## Benchmarking (`--profile`)

Benchmark configured presets against a live `llama-server`:

```bash
llama-mcp-install --profile --json
```

## Release Signature Verification (`signing.py`)

Downloaded `llama.cpp` release archives are verified against `.bundle`/`.sig`/`.asc` assets via `cosign` or `gpg` when available (pass `--no-verify-signature` to skip).
