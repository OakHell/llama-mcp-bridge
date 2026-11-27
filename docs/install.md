# Installation Paths

`llama-mcp-bridge` supports four installation workflows depending on your environment.

## 1. Interactive TUI Wizard (Recommended)

Running `llama-mcp-install` with no flags launches the interactive `rich` TUI wizard:

```bash
uv tool install git+https://github.com/OakHell/llama-mcp-bridge
llama-mcp-install
```

The wizard profiles your GPU/RAM, writes `bridge.ini` (`schema_version = 1`) and `models.ini`, optionally downloads curated GGUF models, and prints your MCP client snippet.

## 2. Non-Interactive CLI

For scripted or headless setups:

```bash
llama-mcp-install --mode prebuilt -y --residency moderate
llama-mcp-install --download-models
llama-mcp-install --doctor
```

Use `--dry-run` with any command to preview actions without modifying disk or network.

## 3. Agent Self-Bootstrap (`SKILL.md`)

Install the bundled setup skill so your coding agent can bootstrap and diagnose the bridge autonomously:

```bash
llama-mcp-install --install-skill auto
```

Supported targets: `auto`, `claude`, `antigravity`, `gemini`, `cursor`, `workspace`, or a custom directory. Pass `--skill-link` to symlink instead of copying.

## 4. Pinned / Offline Release Wheel

See [install-wheel.md](install-wheel.md) for installing from GitHub Release wheels or air-gapped environments, and [install-bsd.md](install-bsd.md) for FreeBSD/BSD instructions.
