import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from omniagents.execution import (
    CapabilityUnavailableError,
    EnvironmentCapability,
    EnvironmentChangedError,
    EnvironmentKind,
    EnvironmentNotFoundError,
    EnvironmentProvisioningUnavailableError,
    EnvironmentShutdownError,
    EnvironmentState,
    EnvironmentUnavailableError,
    ExecutionEnvironmentDescriptor,
    ExecutionEnvironmentManager,
    ProvisionedEnvironment,
    SandboxExecutionRuntime,
    TerminalExecutionDefaults,
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
        kind=EnvironmentKind.CONTAINER,
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
async def test_set_capability_updates_descriptor_without_new_generation():
    workspaces = WorkspaceRegistry()
    workspaces.register(WorkspaceResource("workspace-1"))
    environments = ExecutionEnvironmentManager(workspaces)
    await environments.register(
        descriptor(
            "env-1",
            "workspace-1",
            capabilities=frozenset(
                {EnvironmentCapability.WORKSPACE_READ, EnvironmentCapability.NETWORK}
            ),
        ),
        fake_workspace("content"),
    )
    lease_before = await environments.acquire("env-1")

    updated = await environments.set_capability(
        "env-1", EnvironmentCapability.NETWORK, present=False
    )

    # Same generation — the toggle records what the CURRENT backend can do.
    assert updated.generation == 1
    assert EnvironmentCapability.NETWORK not in updated.capabilities
    # describe() (what the approval policy reads per-request) sees the change;
    # the pre-existing lease keeps its immutable snapshot.
    described = await environments.describe("env-1")
    assert EnvironmentCapability.NETWORK not in described.capabilities
    assert EnvironmentCapability.NETWORK in lease_before.descriptor.capabilities

    restored = await environments.set_capability(
        "env-1", EnvironmentCapability.NETWORK, present=True
    )
    assert EnvironmentCapability.NETWORK in restored.capabilities

    with pytest.raises(EnvironmentNotFoundError):
        await environments.set_capability(
            "missing", EnvironmentCapability.NETWORK, present=True
        )


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
async def test_replacement_publishes_new_runtime_without_retargeting_old_lease():
    workspaces = WorkspaceRegistry()
    workspaces.register(WorkspaceResource("workspace-1"))
    environments = ExecutionEnvironmentManager(workspaces)
    old_session = object()
    new_session = object()
    await environments.register(
        descriptor("env-1", "workspace-1"),
        fake_workspace("old"),
        sandbox_runtime=SandboxExecutionRuntime(old_session, run_as="old-user"),
        terminal_defaults=TerminalExecutionDefaults(shell="old-shell"),
    )
    old_lease = await environments.acquire("env-1")

    await environments.transition("env-1", EnvironmentState.REPLACING)
    await environments.replace(
        "env-1",
        fake_workspace("new"),
        sandbox_runtime=SandboxExecutionRuntime(new_session, run_as="new-user"),
        terminal_defaults=TerminalExecutionDefaults(shell="new-shell"),
    )
    new_lease = await environments.acquire("env-1")

    assert old_lease.sandbox_runtime.session is old_session
    assert old_lease.sandbox_runtime.run_as == "old-user"
    assert new_lease.sandbox_runtime.session is new_session
    assert new_lease.sandbox_runtime.run_as == "new-user"
    assert old_lease.terminal_defaults.shell == "old-shell"
    assert new_lease.terminal_defaults.shell == "new-shell"


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
async def test_shutdown_closes_every_ready_environment():
    workspaces = WorkspaceRegistry()
    environments = ExecutionEnvironmentManager(workspaces)
    controllers = []
    for suffix in ("a", "b"):
        workspace_id = f"workspace-{suffix}"
        environment_id = f"env-{suffix}"
        workspaces.register(WorkspaceResource(workspace_id))
        controller = Mock(close=AsyncMock())
        controllers.append(controller)
        await environments.register(
            descriptor(environment_id, workspace_id),
            fake_workspace(suffix),
            lifecycle_controller=controller,
        )

    stopped = await environments.shutdown()

    assert {item.environment_id for item in stopped} == {"env-a", "env-b"}
    assert all(item.state is EnvironmentState.STOPPED for item in stopped)
    for controller in controllers:
        controller.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_attempts_remaining_environments_after_close_failure():
    workspaces = WorkspaceRegistry()
    environments = ExecutionEnvironmentManager(workspaces)
    for suffix in ("broken", "healthy"):
        workspace_id = f"workspace-{suffix}"
        workspaces.register(WorkspaceResource(workspace_id))
        close = AsyncMock(
            side_effect=RuntimeError("cannot close") if suffix == "broken" else None
        )
        await environments.register(
            descriptor(f"env-{suffix}", workspace_id),
            fake_workspace(suffix),
            lifecycle_controller=Mock(close=close),
        )

    with pytest.raises(EnvironmentShutdownError) as exc_info:
        await environments.shutdown()

    assert set(exc_info.value.failures) == {"env-broken"}
    assert (await environments.describe("env-broken")).state is EnvironmentState.FAILED
    assert (
        await environments.describe("env-healthy")
    ).state is EnvironmentState.STOPPED


@pytest.mark.asyncio
async def test_shutdown_waits_for_inflight_provisioning_then_stops_result():
    workspaces = WorkspaceRegistry()
    workspaces.register(WorkspaceResource("workspace-1"))
    provision_started = asyncio.Event()
    release_provision = asyncio.Event()

    async def provision(request):  # noqa: ARG001
        provision_started.set()
        await release_provision.wait()
        return ProvisionedEnvironment(
            workspace=fake_workspace("provisioned"),
            kind=EnvironmentKind.CONTAINER,
            capabilities=frozenset({EnvironmentCapability.WORKSPACE_READ}),
        )

    environments = ExecutionEnvironmentManager(
        workspaces, provisioner=SimpleNamespace(provision=provision)
    )
    provision_task = asyncio.create_task(
        environments.resolve_automatic("workspace-1", "devbox")
    )
    await provision_started.wait()

    shutdown_task = asyncio.create_task(environments.shutdown())
    await asyncio.sleep(0)
    assert not shutdown_task.done()

    release_provision.set()
    provisioned = await provision_task
    stopped = await shutdown_task

    assert [item.environment_id for item in stopped] == [provisioned.environment_id]
    assert stopped[0].state is EnvironmentState.STOPPED


@pytest.mark.asyncio
async def test_shutdown_rejects_new_automatic_environments():
    workspaces = WorkspaceRegistry()
    workspaces.register(WorkspaceResource("workspace-1"))
    provisioner = SimpleNamespace(provision=AsyncMock())
    environments = ExecutionEnvironmentManager(workspaces, provisioner=provisioner)

    assert await environments.shutdown() == ()

    with pytest.raises(EnvironmentProvisioningUnavailableError):
        await environments.resolve_automatic("workspace-1", "devbox")
    provisioner.provision.assert_not_awaited()


@pytest.mark.asyncio
async def test_environment_workspace_mismatch_is_rejected():
    workspaces = WorkspaceRegistry()
    workspaces.register(WorkspaceResource("workspace-a"))
    workspaces.register(WorkspaceResource("workspace-b"))
    environments = ExecutionEnvironmentManager(workspaces)
    await environments.register(descriptor("env-a", "workspace-a"), fake_workspace("a"))

    with pytest.raises(WorkspaceEnvironmentMismatchError):
        await environments.validate_workspace("workspace-b", "env-a")


@pytest.mark.asyncio
async def test_retire_removes_environment_but_not_inflight_leases():
    """retire() drops the environment from routing; a lease already held
    is an immutable snapshot and keeps working (worker-worktree
    lifecycle: integrate, retire, in-flight run drains)."""
    workspaces = WorkspaceRegistry()
    workspaces.register(WorkspaceResource("workspace-1"))
    environments = ExecutionEnvironmentManager(workspaces)
    await environments.register(descriptor("env-1", "workspace-1"), fake_workspace("x"))

    lease = await environments.acquire("env-1")
    retired = await environments.retire("env-1")
    assert retired.environment_id == "env-1"
    assert lease.workspace is not None  # snapshot unaffected

    from omniagents.execution import EnvironmentNotFoundError

    with pytest.raises(EnvironmentNotFoundError):
        await environments.acquire("env-1")
    with pytest.raises(EnvironmentNotFoundError):
        await environments.retire("env-1")
