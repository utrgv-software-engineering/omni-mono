from pathlib import Path

import pytest

from omniagents.execution import (
    AgentHostResources,
    EnvironmentKind,
    register_host_environment,
)


def test_agent_host_resources_share_one_explicit_composition_root():
    resources = AgentHostResources.create("agent-host-1")

    assert resources.agent_host_id == "agent-host-1"
    assert resources.router._workspace_registry is resources.workspaces
    assert resources.router._environment_manager is resources.environments


@pytest.mark.asyncio
async def test_host_execution_exists_only_after_explicit_registration(tmp_path):
    resources = AgentHostResources.create("agent-host-1")

    descriptor = register_host_environment(
        resources,
        str(tmp_path),
        owner_user_id="user-1",
        workspace_id="workspace-1",
        environment_id="environment-1",
    )

    lease = await resources.environments.acquire("environment-1")
    assert descriptor.kind is EnvironmentKind.HOST
    assert lease.workspace.root == str(tmp_path)
    assert resources.workspaces.get("workspace-1").owner_user_id == "user-1"


@pytest.mark.asyncio
async def test_registered_host_environment_is_unconfined(tmp_path):
    """The eager composition root must agree with the lazy one.

    ``register_host_environment`` (ink / web / studio / worker worktrees) and
    ``ServeEnvironmentProvisioner``'s host branch (omni serve) both mean "the
    no-sandbox target", and both must produce a workspace whose file tools
    reach as far as its shell does. They diverged once — the provisioner was
    unfenced while this root silently inherited a fence — and nothing but this
    test compares them.
    """
    resources = AgentHostResources.create("agent-host-1")
    register_host_environment(
        resources,
        str(tmp_path),
        owner_user_id=None,
        workspace_id="workspace-1",
        environment_id="environment-1",
    )

    lease = await resources.environments.acquire("environment-1")
    workspace = lease.workspace

    assert workspace.enforces_confinement is False
    # A path outside the workspace, spelled for this host ("C:\\etc\\..." on Windows).
    outside = str(Path(tmp_path.anchor) / "etc" / "hostname")
    assert workspace.resolve(outside) == outside
    # The root still anchors relative paths; it just is not a fence.
    assert workspace.resolve("sub/f.txt") == str(tmp_path / "sub" / "f.txt")


@pytest.mark.asyncio
async def test_mounted_host_paths_are_unrestricted_but_rpc_paths_stay_bounded(tmp_path):
    from omniagents.workspace.mounted_host import MountedHostWorkspace
    from omniagents.workspace.types import WorkspaceEscapeError

    (tmp_path / "src").mkdir()
    outside = tmp_path / "skills"
    outside.mkdir()
    (outside / "reference.md").write_text("skill reference")
    workspace = MountedHostWorkspace(
        str(tmp_path / "logical"),
        [{"kind": "local", "mountName": "src", "path": str(tmp_path / "src")}],
    )
    assert workspace.enforces_confinement is False
    assert workspace.resolve("src") == str(tmp_path / "src")
    for path in [str(outside / "reference.md"), "../skills/reference.md"]:
        assert await workspace.read_text(path) == "skill reference"
        with pytest.raises(WorkspaceEscapeError):
            workspace.resolve_confined(path)
    (Path(workspace.root) / "external").symlink_to(outside, target_is_directory=True)
    assert await workspace.read_text("external/reference.md") == "skill reference"
    with pytest.raises(WorkspaceEscapeError):
        workspace.resolve_confined("external/reference.md")
    assert workspace.resolve_confined("src") == str(tmp_path / "src")


@pytest.mark.parametrize("agent_host_id", ["", "  "])
def test_agent_host_identity_is_required(agent_host_id):
    with pytest.raises(ValueError, match="non-empty"):
        AgentHostResources.create(agent_host_id)
