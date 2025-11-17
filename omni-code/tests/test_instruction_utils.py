from oagents.instruction_utils import format_instructions


def test_format_instructions_without_agents(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result = format_instructions("Base text")
    assert "Base text" in result
    assert "[Workspace]" in result
    assert "# Repository AGENTS" not in result


def test_format_instructions_with_agents(monkeypatch, tmp_path):
    root_agents = tmp_path / "AGENTS.md"
    root_agents.write_text("root instructions", encoding="utf-8")
    nested_dir = tmp_path / "nested"
    nested_dir.mkdir()
    nested_agents = nested_dir / "AGENTS.md"
    nested_agents.write_text("nested instructions", encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    result = format_instructions("Voice base")

    assert "Voice base" in result
    assert "# Repository AGENTS" in result
    assert f"[AGENTS.md at {tmp_path}]" in result
    assert f"[AGENTS.md at {nested_dir}]" in result
