from omni_agents.omni.context import build_coding_context


def test_build_coding_context_includes_available_skills(monkeypatch, tmp_path):
    skills_root = tmp_path / ".omni_code" / "skills"
    skill_dir = skills_root / "pdf-reader"
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\nname: pdf-reader\ndescription: Reads PDF files\n---\n\nBody\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)

    context = build_coding_context({"workspace_root": str(tmp_path)})
    block = context["available_skills_block"]

    assert "<available_skills>" in block
    assert "pdf-reader" in block
    assert "Reads PDF files" in block
    assert str(skill_md.resolve()) in block


def test_build_coding_context_prefers_pwd_for_skill_discovery(monkeypatch, tmp_path):
    skills_root = tmp_path / ".omni_code" / "skills"
    skill_dir = skills_root / "pdf-reader"
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\nname: pdf-reader\ndescription: Reads PDF files\n---\n\nBody\n",
        encoding="utf-8",
    )

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)
    monkeypatch.setenv("PWD", str(tmp_path))

    context = build_coding_context({})
    block = context["available_skills_block"]

    assert "pdf-reader" in block
