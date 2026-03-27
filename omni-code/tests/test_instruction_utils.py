from omni_agents.omni.context import format_agents_md_block


def test_format_agents_md_block_without_agents(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PWD", str(tmp_path))
    result = format_agents_md_block()
    assert "[Workspace]" in result
    assert "<agents_md_files>" not in result


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

    assert "<agents_md_files>" in result
    assert f"<location>{root_agents}</location>" in result
    assert f"<location>{nested_agents}</location>" in result
    assert f"<scope>{tmp_path}</scope>" in result
    assert f"<scope>{nested_dir}</scope>" in result
    # Content should NOT be in the block
    assert "root instructions" not in result
    assert "nested instructions" not in result
