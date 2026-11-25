from pathlib import Path

from llama_mcp_bridge.bridge import (
    _build_prompt_with_files,
    _discover_ancestor_gitignores,
    _expand_target_files,
    _is_gitignored,
)


def test_gitignore_cascade(tmp_path: Path):
    (tmp_path / ".gitignore").write_text("node_modules/\n*.log\n")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / ".gitignore").write_text("temp/\n*.tmp\n")

    (tmp_path / "node_modules").mkdir()
    (tmp_path / "nested" / "temp").mkdir()
    (tmp_path / "app.log").write_text("log")
    (tmp_path / "nested" / "tempfile.tmp").write_text("tmp")
    (tmp_path / "nested" / "file.txt").write_text("ok")

    specs = _discover_ancestor_gitignores(tmp_path / "nested" / "file.txt")
    assert len(specs) >= 2


def test_gitignore_negations(tmp_path: Path):
    (tmp_path / ".gitignore").write_text("*.log\n!important.log\n")
    (tmp_path / "app.log").write_text("ignore me")
    (tmp_path / "important.log").write_text("keep me")

    specs = _discover_ancestor_gitignores(tmp_path)
    assert _is_gitignored(
        tmp_path / "app.log",
        rel_path="app.log",
        is_dir=False,
        scoped_specs=specs,
        global_specs=[],
    )
    assert not _is_gitignored(
        tmp_path / "important.log",
        rel_path="important.log",
        is_dir=False,
        scoped_specs=specs,
        global_specs=[],
    )


def test_include_ignore_precedence(tmp_path: Path):
    (tmp_path / "skip.log").write_text("skip")
    (tmp_path / "keep.txt").write_text("keep")

    files, _ = _expand_target_files(
        [str(tmp_path)], ignore=["*.log", "*.txt"], include=["keep.txt"]
    )
    assert files == [tmp_path / "keep.txt"]


def test_budget_sorting(tmp_path: Path):
    (tmp_path / "small.txt").write_text("a" * 100)
    (tmp_path / "large.txt").write_text("a" * 1000)

    prompt, err = _build_prompt_with_files("Check", [str(tmp_path)], max_total_chars=500)
    assert err is None
    assert "small.txt" in prompt
    assert prompt.index("small.txt") < prompt.index("large.txt")
    assert "TRUNCATED" in prompt


def test_binary_file_skipping(tmp_path: Path):
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00")
    (tmp_path / "code.py").write_text("print('hello')\n")

    prompt, err = _build_prompt_with_files("Check", [str(tmp_path)])
    assert err is None
    assert "code.py" in prompt
    assert "skipped_binary" in prompt
