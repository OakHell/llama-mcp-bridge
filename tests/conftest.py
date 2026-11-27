"""Shared pytest fixtures for llama-mcp-bridge."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def workspace_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create an isolated temporary workspace and point LLAMA_MCP_HOME to it."""
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "models").mkdir(exist_ok=True)
    monkeypatch.setenv("LLAMA_MCP_HOME", str(ws))
    return ws
