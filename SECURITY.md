# Security Policy

## Supported Versions

Security fixes are applied to the latest `main` release line:

| Version | Supported |
| --- | --- |
| `0.10.x` | Yes |
| `0.9.x` | Critical fixes only |
| `< 0.9.0` | No |

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Instead, report vulnerabilities privately via GitHub Security Advisories at:
<https://github.com/OakHell/llama-mcp-bridge/security/advisories/new>

Include:
1. Affected version (`llama-mcp-install --version`) and OS/platform.
2. Reproduction steps or proof-of-concept.
3. Potential impact (e.g. archive extraction, MCP client config writes, `llama-server` subprocess invocation).

We aim to acknowledge reports within 72 hours and provide a remediation timeline once reproduced.

## Security Boundaries

- **Local-only inference by default:** `llama-server` binds to `127.0.0.1` unless explicitly configured otherwise in `bridge.ini`.
- **Agency-free specialist tools:** Specialist MCP tools (`ask_digest`, `ask_coder`, etc.) do not expose shell execution or function-calling to the local model; file and image access is brokered by the Python bridge within `.gitignore` and character budgets.
- **Archive & download verification:** Prebuilt `llama.cpp` archive extraction validates path traversal, symlink targets, uncompressed byte limits (`2 GiB`), and entry counts (`5,000`), and GGUF downloads verify Hugging Face `X-Linked-ETag` SHA-256 checksums when provided.
