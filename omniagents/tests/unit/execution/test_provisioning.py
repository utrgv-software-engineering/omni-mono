import asyncio
from unittest.mock import Mock

import pytest

from omniagents.execution import (
    AgentHostResources,
    AutomaticEnvironment,
    EnvironmentCapability,
    EnvironmentKind,
    EnvironmentProvisioningFailedError,
    EnvironmentProvisioningUnavailableError,
    ProvisionedEnvironment,
    ThreadRuntimeBinding,
    WorkspaceResource,
)
from omniagents.rpc.protocol_catalog import OPERATION_BY_NAME
from omniagents.workspace.base import Workspace


class RecordingProvisioner:
    def __init__(self, *, gate: asyncio.Event | None = None, failures: int = 0):
        self.gate = gate
        self.failures = failures
        self.requests = []
        self.entered = asyncio.Event()

    async def provision(self, request):
        self.requests.append(request)
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.failures:
            self.failures -= 1
            raise RuntimeError("backend unavailable")
        workspace = Mock(spec=Workspace)
        workspace.root = "/workspace"
        return ProvisionedEnvironment(
            workspace=workspace,
            kind=EnvironmentKind.DEVBOX,
            capabilities=frozenset(
                {
                    EnvironmentCapability.WORKSPACE_READ,
                    EnvironmentCapability.WORKSPACE_WRITE,
                    EnvironmentCapability.PROCESS,
                }
            ),
        )


def resources_with(provisioner=None):
    resources = AgentHostResources.create(
        "provisioning-host", environment_provisioner=provisioner
    )
    resources.workspaces.register(WorkspaceResource("workspace-1"))
    return resources


@pytest.mark.asyncio
async def test_prepare_is_side_effect_free_and_materialize_provisions_afterward():
    provisioner = RecordingProvisioner()
    resources = resources_with(provisioner)
    await resources.thread_bindings.bind(
        "thread-1",
        ThreadRuntimeBinding(
            workspace_id="workspace-1",
            environment_selection=AutomaticEnvironment("devbox"),
        ),
    )

    context = await resources.router.prepare_protocol(
        OPERATION_BY_NAME["start_run"],
        {
            "prompt": "hello",
            "session_id": "thread-1",
            "environment_selection": {"mode": "inherit"},
        },
    )

    assert provisioner.requests == []
    assert context.workspace.workspace_id == "workspace-1"
    assert context.environment_lease is None
    assert context.automatic_environment == AutomaticEnvironment("devbox")

    materialized = await resources.router.materialize(context)

    assert len(provisioner.requests) == 1
    assert materialized.environment_lease is not None
    assert materialized.environment_lease.workspace_id == "workspace-1"
    assert materialized.automatic_environment is None


@pytest.mark.asyncio
async def test_concurrent_automatic_resolution_single_flights_one_environment():
    gate = asyncio.Event()
    provisioner = RecordingProvisioner(gate=gate)
    resources = resources_with(provisioner)

    first = asyncio.create_task(
        resources.environments.resolve_automatic("workspace-1", "devbox")
    )
    second = asyncio.create_task(
        resources.environments.resolve_automatic("workspace-1", "devbox")
    )
    await asyncio.wait_for(provisioner.entered.wait(), timeout=1)
    assert len(provisioner.requests) == 1

    gate.set()
    first_descriptor, second_descriptor = await asyncio.gather(first, second)

    assert first_descriptor == second_descriptor
    assert len(await resources.environments.list()) == 1


@pytest.mark.asyncio
async def test_same_profile_isolated_by_workspace_identity():
    provisioner = RecordingProvisioner()
    resources = resources_with(provisioner)
    resources.workspaces.register(WorkspaceResource("workspace-2"))

    first, second = await asyncio.gather(
        resources.environments.resolve_automatic("workspace-1", "devbox"),
        resources.environments.resolve_automatic("workspace-2", "devbox"),
    )

    assert first.environment_id != second.environment_id
    assert first.workspace_id == "workspace-1"
    assert second.workspace_id == "workspace-2"
    assert len(provisioner.requests) == 2


@pytest.mark.asyncio
async def test_failed_provisioning_is_typed_and_can_be_retried():
    provisioner = RecordingProvisioner(failures=1)
    resources = resources_with(provisioner)

    with pytest.raises(EnvironmentProvisioningFailedError) as exc_info:
        await resources.environments.resolve_automatic("workspace-1", "devbox")
    assert exc_info.value.data["reason"] == "backend unavailable"

    descriptor = await resources.environments.resolve_automatic("workspace-1", "devbox")
    assert descriptor.workspace_id == "workspace-1"
    assert len(provisioner.requests) == 2


@pytest.mark.asyncio
async def test_stopped_automatic_environment_is_reprovisioned():
    provisioner = RecordingProvisioner()
    resources = resources_with(provisioner)
    first = await resources.environments.resolve_automatic("workspace-1", "devbox")

    await resources.environments.stop(first.environment_id)
    second = await resources.environments.resolve_automatic("workspace-1", "devbox")

    assert second.environment_id != first.environment_id
    assert len(provisioner.requests) == 2


@pytest.mark.asyncio
async def test_automatic_resolution_fails_closed_without_host_provisioner():
    resources = resources_with()

    with pytest.raises(EnvironmentProvisioningUnavailableError) as exc_info:
        await resources.environments.resolve_automatic("workspace-1", "devbox")

    assert exc_info.value.data == {
        "kind": "environment_provisioning_unavailable",
        "workspace_id": "workspace-1",
        "profile_id": "devbox",
    }
