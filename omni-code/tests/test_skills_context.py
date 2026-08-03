from types import SimpleNamespace

from omniagents.execution import EnvironmentKind

from omni_code.agents.omni.context import build_coding_context


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


def test_build_coding_context_includes_skills_from_sibling_source_mount(
    monkeypatch, tmp_path
):
    workspace = tmp_path / "workspace"
    primary = workspace / "omni-desktop"
    plugins = workspace / "omni-plugins-official"
    primary.mkdir(parents=True)
    skill_dir = plugins / "skills" / "software-tickets"
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\nname: software-tickets\ndescription: Ticket workflow\n---\n\nBody\n",
        encoding="utf-8",
    )
    sources = [
        {"kind": "local-git", "mountName": "omni-desktop", "path": str(primary)},
        {
            "kind": "local-git",
            "mountName": "omni-plugins-official",
            "path": str(plugins),
        },
    ]
    monkeypatch.setattr(
        "omniagents.execution.current_request_context",
        lambda: SimpleNamespace(
            workspace=SimpleNamespace(sources=sources),
            environment_lease=SimpleNamespace(
                descriptor=SimpleNamespace(kind=EnvironmentKind.HOST),
                workspace=SimpleNamespace(root=str(workspace)),
            ),
        ),
    )
    context = build_coding_context({"workspace_root": str(primary)})

    block = context["available_skills_block"]
    assert "software-tickets" in block
    assert "Ticket workflow" in block
    assert str(skill_md.resolve()) in block
