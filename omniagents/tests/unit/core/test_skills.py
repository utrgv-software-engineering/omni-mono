from pathlib import Path

from omniagents.core.skills import (
    build_available_skills_block,
    discover_skill_dirs,
    find_skill_md,
    parse_frontmatter,
    read_properties,
    validate,
)


def _write_skill(
    skill_dir: Path, *, name: str, description: str, filename: str = "SKILL.md"
) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / filename).write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def test_find_skill_md_prefers_uppercase(tmp_path: Path):
    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir()
    (skill_dir / "skill.md").write_text(
        "---\nname: demo-skill\ndescription: demo\n---", encoding="utf-8"
    )
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: demo\n---", encoding="utf-8"
    )

    assert find_skill_md(skill_dir) == skill_dir / "SKILL.md"


def test_parse_frontmatter_returns_metadata_and_body():
    metadata, body = parse_frontmatter(
        "---\nname: demo-skill\ndescription: demo\n---\n\nBody"
    )

    assert metadata["name"] == "demo-skill"
    assert metadata["description"] == "demo"
    assert body == "Body"


def test_read_properties_extracts_expected_fields(tmp_path: Path):
    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: demo\nmetadata:\n  owner: team\n---\n",
        encoding="utf-8",
    )

    props = read_properties(skill_dir)

    assert props.name == "demo-skill"
    assert props.description == "demo"
    assert props.metadata == {"owner": "team"}


def test_validate_rejects_mismatched_directory_name(tmp_path: Path):
    skill_dir = tmp_path / "not-the-skill-name"
    _write_skill(skill_dir, name="demo-skill", description="demo")

    errors = validate(skill_dir)

    assert errors
    assert (
        "Directory name 'not-the-skill-name' must match skill name 'demo-skill'"
        in errors
    )


def test_discover_skill_dirs_finds_root_and_children(tmp_path: Path):
    root_skill = tmp_path / "root-skill"
    child_root = tmp_path / "skills"
    child_skill = child_root / "child-skill"
    _write_skill(root_skill, name="root-skill", description="root")
    _write_skill(child_skill, name="child-skill", description="child")

    discovered = discover_skill_dirs([root_skill, child_root])

    assert root_skill in discovered
    assert child_skill in discovered


def test_build_available_skills_block_prefers_earlier_duplicate_names(tmp_path: Path):
    local_root = tmp_path / "local"
    global_root = tmp_path / "global"
    _write_skill(
        local_root / "demo-skill", name="demo-skill", description="local description"
    )
    _write_skill(
        global_root / "demo-skill", name="demo-skill", description="global description"
    )
    _write_skill(
        global_root / "other-skill", name="other-skill", description="other description"
    )

    block = build_available_skills_block([local_root, global_root])

    assert block.count("<name>\ndemo-skill\n</name>") == 1
    assert "local description" in block
    assert "global description" not in block
    assert "other-skill" in block
