# Pinned & Offline Release Wheel Installation

Tagged releases on GitHub include a Python wheel (`.whl`), source tarball, CycloneDX 1.5 SBOM (`sbom.cdx.json`), and Sigstore bundle signatures.

## Install a Pinned Release

```bash
uv tool install "llama-mcp-bridge @ https://github.com/OakHell/llama-mcp-bridge/releases/download/v1.0.1/llama_mcp_bridge-1.0.1-py3-none-any.whl"
```

## Offline / Air-Gapped Installation

1. Download the `.whl` and its dependencies on a connected machine (`pip download` or `uv export`).
2. Transfer the wheelhouse to the target host and install:

```bash
uv tool install --offline /path/to/llama_mcp_bridge-1.0.1-py3-none-any.whl
```

3. Use `--mode system-bin` if `llama-server` is already installed on `PATH`, or place the prebuilt `llama-server` binary in `<workspace>/bin/`.
