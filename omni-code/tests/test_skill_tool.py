from tools.skill_tool import _resolve_skill_path
from omni_code import agent_workspace_state as aws


def _write_skill(skill_dir, name, description):
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nBody\n",
        encoding="utf-8",
    )
    return skill_md


def test_resolve_skill_path_finds_skill_in_sibling_source_mount(tmp_path):
    workspace = tmp_path / "workspace"
    primary = workspace / "omni-desktop"
    plugins = workspace / "omni-plugins-official"
    primary.mkdir(parents=True)
    skill_md = _write_skill(
        plugins / "skills" / "software-tickets",
        "software-tickets",
        "Ticket workflow",
    )
    aws.set_sources(
        [
            {"kind": "local-git", "mountName": "omni-desktop", "path": str(primary)},
            {
                "kind": "local-git",
                "mountName": "omni-plugins-official",
                "path": str(plugins),
            },
        ]
    )
    aws.set_manifest_root(str(workspace))
    try:
        assert _resolve_skill_path("software-tickets", str(primary)) == skill_md
    finally:
        aws.set_sources([])
        aws.set_manifest_root(None)
