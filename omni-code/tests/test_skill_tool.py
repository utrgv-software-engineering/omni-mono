from omniagents.builtin.tools.skill_tool import _resolve_skill_path
from omniagents.core.runtime import serve_state as aws
from omniagents.core.skills import source_skill_roots

# Importing omni_code bootstraps the product identity (slug omni_code),
# from which the framework derives the skill layout (workspace
# .omni_code/skills + source mounts + global config dir) — no provider
# install; this is the same wiring the running agent gets.
import omni_code  # noqa: F401


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
