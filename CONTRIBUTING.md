# Contributing to `llama-mcp-bridge`

Thank you for considering contributing to `llama-mcp-bridge`!

## Development Setup

This project uses `uv` exclusively for environment management, linting, type checking, and testing (Python 3.11+):

```bash
git clone https://github.com/OakHell/llama-mcp-bridge.git
cd llama-mcp-bridge
uv sync --dev
```

## Verification Checks

Run the full local verification suite before opening a pull request:

```bash
uv run ruff check .
uv run mypy src
uv run pytest -v
```

You can also use the `Makefile` shortcuts (`make check` or `make test`, `make lint`, `make typecheck`) or install pre-commit hooks via `pre-commit install`.

## Coding & Testing Guidelines

- **Type annotations:** Keep `uv run mypy src` clean across all modules in `src/llama_mcp_bridge/`.
- **Modular layout:** Place new installer subcommands or helpers in their focused submodule (`service_install.py`, `model_download.py`, `client_config.py`, `skill_install.py`, `discovery.py`) and re-export from `installer.py` / `bridge.py` where needed for backwards compatibility.
- **Mock external I/O:** Unit tests must mock network calls (Hugging Face / GitHub APIs) and subprocess probes (`nvidia-smi`, `llama-server`, `systemctl`, `schtasks`) so the suite runs offline across Linux, macOS, Windows, and FreeBSD.
- **Documentation:** Update `README.md`, `TRACKER.md`, or `docs/` when adding CLI flags, configuration keys, or hardware tier changes.

## Pull Request Process

1. Create a feature branch from `main` (e.g. `feat/my-change` or `fix/issue-123`).
2. Ensure `uv run ruff check .`, `uv run mypy src`, and `uv run pytest -v` pass with zero errors.
3. Fill out the pull request template describing the change and verification performed.

## Enterprise Extra (`src/llama_mcp_bridge/enterprise/`)
- `audit.py` (`E3`), `probes.py` (`E7`), `tenant.py` (`E11`): opt-in `[enterprise]` modules, never imported unless `--enterprise` / `enterprise_mode = true`.

## Makefile Targets
- `make test`: Run `uv run pytest -v`
- `make lint`: Run `uv run ruff check .`
- `make typecheck`: Run `uv run mypy src`
- `make doctor`: Run `uv run llama-mcp-install --doctor`
- `make verify-all`: Run `uv run llama-mcp-install --verify-all`
- `make check`: Run `lint`, `typecheck`, and `test`
