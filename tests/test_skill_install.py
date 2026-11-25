from pathlib import Path
from unittest.mock import patch

from llama_mcp_bridge.installer import _install_skill, _resolve_bundled_skill_path


def test_skill_md_exists_with_valid_frontmatter_and_commands():
    repo_skill = Path(__file__).resolve().parents[1] / "skills" / "llama-mcp-setup" / "SKILL.md"
    assert repo_skill.is_file()
    text = repo_skill.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "name: llama-mcp-setup" in text
    assert "description:" in text
    for required in ("--yes", "--doctor", "llama-mcp-ps", "llama-mcp-models add"):
        assert required in text


def test_bundled_skill_resource_lookup():
    path = _resolve_bundled_skill_path()
    assert path.is_file()
    assert path.name == "SKILL.md"


def test_install_skill_targets_and_auto_detection(tmp_path: Path):
    home = tmp_path / "home"
    work = tmp_path / "work"
    home.mkdir()
    work.mkdir()

    # Specific targets
    assert _install_skill("claude", home_dir=home, cwd=work)[0].is_file()
    assert (home / ".claude" / "skills" / "llama-mcp-setup" / "SKILL.md").is_file()

    assert _install_skill("cursor", home_dir=home, cwd=work)[0].is_file()
    assert (home / ".cursor" / "skills" / "llama-mcp-setup" / "SKILL.md").is_file()

    assert _install_skill("gemini", home_dir=home, cwd=work)[0].is_file()
    assert (home / ".gemini" / "skills" / "llama-mcp-setup" / "SKILL.md").is_file()

    assert _install_skill("antigravity", home_dir=home, cwd=work)[0].is_file()
    assert (home / ".gemini" / "config" / "skills" / "llama-mcp-setup" / "SKILL.md").is_file()

    assert _install_skill("workspace", home_dir=home, cwd=work)[0].is_file()
    assert (work / ".agents" / "skills" / "llama-mcp-setup" / "SKILL.md").is_file()

    # Auto-detection with mocked home containing .claude and .cursor only
    home2 = tmp_path / "home2"
    (home2 / ".claude").mkdir(parents=True)
    (home2 / ".cursor").mkdir(parents=True)
    installed = _install_skill("auto", home_dir=home2, cwd=work)
    assert len(installed) == 2
    assert (home2 / ".claude" / "skills" / "llama-mcp-setup" / "SKILL.md").is_file()
    assert (home2 / ".cursor" / "skills" / "llama-mcp-setup" / "SKILL.md").is_file()

    # Auto fallback to workspace when no harness dirs exist
    home3 = tmp_path / "home3"
    home3.mkdir()
    work3 = tmp_path / "work3"
    work3.mkdir()
    fallback = _install_skill("auto", home_dir=home3, cwd=work3)
    assert fallback == [work3 / ".agents" / "skills" / "llama-mcp-setup" / "SKILL.md"]
    assert fallback[0].is_file()


def test_install_skill_cli_flag(tmp_path: Path):
    work = tmp_path / "cli_work"
    work.mkdir()
    with (
        patch("llama_mcp_bridge.installer.Path.cwd", return_value=work),
        patch("sys.argv", ["llama-mcp-install", "--install-skill", "workspace"]),
    ):
        from llama_mcp_bridge.installer import main as installer_main

        installer_main()

    assert (work / ".agents" / "skills" / "llama-mcp-setup" / "SKILL.md").is_file()
