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
    assert workspace.resolve("/etc/hostname") == "/etc/hostname"
    # The root still anchors relative paths; it just is not a fence.
    assert workspace.resolve("sub/f.txt") == str(tmp_path / "sub" / "f.txt")


@pytest.mark.asyncio
async def test_mounted_workspace_still_bounds_its_own_address_space(tmp_path):
    """The one workspace that DOES bound, and does it itself.

    A composite root is a synthetic mount table, not a place on the machine,
    so a path it cannot name resolves to nothing rather than to a file. It
    must not borrow that enforcement from a base-class flag.
    """
    from omniagents.workspace.mounted_host import MountedHostWorkspace
    from omniagents.workspace.types import WorkspaceEscapeError

    (tmp_path / "src").mkdir()
    workspace = MountedHostWorkspace(
        str(tmp_path / "logical"),
        [{"kind": "local", "mountName": "src", "path": str(tmp_path / "src")}],
    )

    assert workspace.enforces_confinement is True
    assert workspace.resolve("src") == str(tmp_path / "src")
    with pytest.raises(WorkspaceEscapeError):
        workspace.resolve("/etc/hostname")
    # A symlink planted in the managed root names nothing in the table.
    (Path(workspace.root) / "sneaky").symlink_to("/etc", target_is_directory=True)
    with pytest.raises(WorkspaceEscapeError):
        workspace.resolve("sneaky/hostname")


@pytest.mark.parametrize("agent_host_id", ["", "  "])
def test_agent_host_identity_is_required(agent_host_id):
    with pytest.raises(ValueError, match="non-empty"):
        AgentHostResources.create(agent_host_id)
