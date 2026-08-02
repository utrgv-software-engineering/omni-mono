from unittest.mock import AsyncMock, Mock

import pytest

from omniagents.execution import (
    CapabilityUnavailableError,
    EnvironmentCapability,
    EnvironmentChangedError,
    EnvironmentKind,
    EnvironmentNotFoundError,
    EnvironmentState,
    EnvironmentUnavailableError,
    ExecutionEnvironmentDescriptor,
    ExecutionEnvironmentManager,
    WorkspaceEnvironmentMismatchError,
    WorkspaceNotFoundError,
    WorkspaceRegistry,
    WorkspaceResource,
)
from omniagents.workspace.base import Workspace


def descriptor(
    environment_id: str,
    workspace_id: str,
    *,
    generation: int = 1,
    capabilities: frozenset[EnvironmentCapability] = frozenset(
        {EnvironmentCapability.WORKSPACE_READ}
    ),
) -> ExecutionEnvironmentDescriptor:
    return ExecutionEnvironmentDescriptor(
        environment_id=environment_id,
        workspace_id=workspace_id,
        kind=EnvironmentKind.DEVBOX,
        state=EnvironmentState.READY,
        generation=generation,
        capabilities=capabilities,
    )


def fake_workspace(content: str) -> Workspace:
    workspace = Mock(spec=Workspace)
    workspace.root = "/workspace"
    workspace.read_text = AsyncMock(return_value=content)
    return workspace


def test_workspace_registry_has_no_default_or_fallback():
    registry = WorkspaceRegistry()

    with pytest.raises(WorkspaceNotFoundError):
        registry.get("missing")

    registry.register(WorkspaceResource("workspace-1"))
    assert registry.get("workspace-1").workspace_id == "workspace-1"


@pytest.mark.asyncio
async def test_two_identical_backend_paths_remain_environment_isolated():
    workspaces = WorkspaceRegistry()
    workspaces.register(WorkspaceResource("workspace-a"))
    workspaces.register(WorkspaceResource("workspace-b"))
    environments = ExecutionEnvironmentManager(workspaces)
    backend_a = fake_workspace("from-a")
    backend_b = fake_workspace("from-b")
    await environments.register(descriptor("env-a", "workspace-a"), backend_a)
    await environments.register(descriptor("env-b", "workspace-b"), backend_b)

    lease_a = await environments.acquire("env-a")
    lease_b = await environments.acquire("env-b")

    assert lease_a.workspace.root == lease_b.workspace.root == "/workspace"
    assert await lease_a.workspace.read_text("shared.txt") == "from-a"
    assert await lease_b.workspace.read_text("shared.txt") == "from-b"
    assert lease_a.workspace_id == "workspace-a"
    assert lease_b.workspace_id == "workspace-b"


@pytest.mark.asyncio
async def test_acquisition_is_explicit_and_capability_checked():
    workspaces = WorkspaceRegistry()
    workspaces.register(WorkspaceResource("workspace-1"))
    environments = ExecutionEnvironmentManager(workspaces)
    await environments.register(
        descriptor("env-1", "workspace-1"), fake_workspace("content")
    )

    with pytest.raises(EnvironmentNotFoundError):
        await environments.acquire("missing")
    with pytest.raises(CapabilityUnavailableError) as exc_info:
        await environments.acquire(
            "env-1",
            required_capabilities=frozenset({EnvironmentCapability.PROCESS}),
        )
    assert exc_info.value.data["missing"] == ["process"]


@pytest.mark.asyncio
async def test_replacement_preserves_old_lease_and_advances_generation():
    workspaces = WorkspaceRegistry()
    workspaces.register(WorkspaceResource("workspace-1"))
    environments = ExecutionEnvironmentManager(workspaces)
    old_backend = fake_workspace("old")
    new_backend = fake_workspace("new")
    await environments.register(descriptor("env-1", "workspace-1"), old_backend)
    old_lease = await environments.acquire("env-1")

    await environments.transition("env-1", EnvironmentState.REPLACING)
    with pytest.raises(EnvironmentUnavailableError):
        await environments.acquire("env-1")
    replacement = await environments.replace("env-1", new_backend)

    assert replacement.generation == 2
    assert await old_lease.workspace.read_text("state") == "old"
    assert (
        await (await environments.acquire("env-1")).workspace.read_text("state")
        == "new"
    )
    with pytest.raises(EnvironmentChangedError):
        await environments.acquire("env-1", expected_generation=1)


@pytest.mark.asyncio
async def test_stopped_environment_cannot_be_leased():
    workspaces = WorkspaceRegistry()
    workspaces.register(WorkspaceResource("workspace-1"))
    environments = ExecutionEnvironmentManager(workspaces)
    await environments.register(
        descriptor("env-1", "workspace-1"), fake_workspace("content")
    )

    await environments.stop("env-1")

    with pytest.raises(EnvironmentUnavailableError) as exc_info:
        await environments.acquire("env-1")
    assert exc_info.value.data["state"] == "stopped"


@pytest.mark.asyncio
async def test_environment_workspace_mismatch_is_rejected():
    workspaces = WorkspaceRegistry()
    workspaces.register(WorkspaceResource("workspace-a"))
    workspaces.register(WorkspaceResource("workspace-b"))
    environments = ExecutionEnvironmentManager(workspaces)
    await environments.register(descriptor("env-a", "workspace-a"), fake_workspace("a"))

    with pytest.raises(WorkspaceEnvironmentMismatchError):
        await environments.validate_workspace("workspace-b", "env-a")
