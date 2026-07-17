from omniagents.builtin.tools.skill_tool import _resolve_skill_path
from omniagents.core.skills import set_skill_roots_provider

from omni_code import agent_workspace_state as aws
from omni_code.skill_sources import source_skill_roots

# Importing the context module installs omni-code's skill-roots provider
# (workspace .omni_code/skills + source mounts + global config dir) —
# the same wiring the running agent gets.
from omni_code.agents.omni.context import _skill_roots_for_workspace


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
    set_skill_roots_provider(_skill_roots_for_workspace)
    try:
        assert _resolve_skill_path("software-tickets", str(primary)) == skill_md
    finally:
        set_skill_roots_provider(_skill_roots_for_workspace)
        aws.set_sources([])
        aws.set_manifest_root(None)
        aws.set_sandbox_session(None)


def test_source_skill_roots_include_host_paths_without_sandbox_session(tmp_path):
    workspace = tmp_path / "workspace"
    primary = workspace / "omni-desktop"
    plugins = workspace / "omni-plugins-official"
    primary.mkdir(parents=True)
    plugins.mkdir(parents=True)
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
    aws.set_manifest_root(str(primary))
    aws.set_sandbox_session(None)
    try:
        roots = source_skill_roots(str(primary))
    finally:
        aws.set_sources([])
        aws.set_manifest_root(None)

    assert primary / "omni-desktop" / "skills" in roots
    assert plugins / "skills" in roots


def test_source_skill_roots_use_sandbox_mounts_with_live_sandbox_session(tmp_path):
    workspace = tmp_path / "workspace"
    primary = workspace / "omni-desktop"
    plugins = workspace / "omni-plugins-official"
    primary.mkdir(parents=True)
    plugins.mkdir(parents=True)
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
    aws.set_manifest_root("/workspace")
    aws.set_sandbox_session(object())
    try:
        roots = source_skill_roots("/workspace/omni-desktop")
    finally:
        aws.set_sources([])
        aws.set_manifest_root(None)
        aws.set_sandbox_session(None)

    assert [str(root) for root in roots] == [
        "/workspace/omni-desktop/skills",
        "/workspace/omni-plugins-official/skills",
    ]
