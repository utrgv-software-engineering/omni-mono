from omni_agents.omni.context import format_agents_md_block


def test_format_agents_md_block_without_agents(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PWD", str(tmp_path))
    result = format_agents_md_block()
    assert "[Workspace]" in result
    assert "# Repository AGENTS" not in result


def test_format_agents_md_block_with_agents(monkeypatch, tmp_path):
    root_agents = tmp_path / "AGENTS.md"
    root_agents.write_text("root instructions", encoding="utf-8")
    nested_dir = tmp_path / "nested"
    nested_dir.mkdir()
    nested_agents = nested_dir / "AGENTS.md"
    nested_agents.write_text("nested instructions", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PWD", str(tmp_path))

    result = format_agents_md_block()

    assert "# Repository AGENTS" in result
    assert f"[AGENTS.md at {tmp_path}]" in result
    assert f"[AGENTS.md at {nested_dir}]" in result
