"""Regression tests for file discovery precedence and structural DEFAULT_IGNORES (T27, S6)."""

from __future__ import annotations

from pathlib import Path

from llama_mcp_bridge.discovery import _expand_target_files


def test_include_overrides_gitignore_and_user_ignore_but_never_default_ignores(
    tmp_path: Path,
) -> None:
    """S6: include=['*.md'] must still exclude .venv, node_modules, .git, and __pycache__."""
    (tmp_path / ".gitignore").write_text("ignored.md\n", encoding="utf-8")
    (tmp_path / "ignored.md").write_text("gitignored", encoding="utf-8")
    (tmp_path / "user_ignored.md").write_text("user ignored", encoding="utf-8")
    (tmp_path / "normal.md").write_text("normal", encoding="utf-8")

    for structural in (".venv", "node_modules", ".git", "__pycache__"):
        d = tmp_path / structural
        d.mkdir(parents=True, exist_ok=True)
        (d / "secret.md").write_text("should never be included", encoding="utf-8")

    files, err = _expand_target_files(
        [str(tmp_path)],
        ignore=["user_ignored.md"],
        include=["*.md"],
        use_gitignore=True,
    )
    assert err is None
    names = {f.relative_to(tmp_path).as_posix() for f in files}
    assert "normal.md" in names
    assert "ignored.md" in names
    assert "user_ignored.md" in names
    assert ".venv/secret.md" not in names
    assert "node_modules/secret.md" not in names
    assert ".git/secret.md" not in names
    assert "__pycache__/secret.md" not in names
