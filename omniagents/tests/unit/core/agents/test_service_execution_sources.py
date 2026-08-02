from __future__ import annotations

import pytest

from omniagents.core.agents.service import AgentService
from omniagents.execution import (
    AgentHostResources,
    EnvironmentKind,
    EnvironmentState,
    ExecutionEnvironmentDescriptor,
    WorkspaceResource,
)
from omniagents.workspace.host import HostWorkspace


@pytest.mark.asyncio
async def test_execution_sources_follow_environment_workspace_identity(tmp_path):
    resources = AgentHostResources.create("source-policy-service-test-host")
    workspace_a = HostWorkspace(root=str(tmp_path / "a"))
    workspace_b = HostWorkspace(root=str(tmp_path / "b"))
    source_a = {"kind": "local", "mountName": "a", "writable": False}
    source_b = {"kind": "local", "mountName": "b", "writable": True}
    resources.workspaces.register(WorkspaceResource("workspace-a", sources=(source_a,)))
    resources.workspaces.register(WorkspaceResource("workspace-b", sources=(source_b,)))
    for environment_id, workspace_id, workspace in (
        ("environment-a", "workspace-a", workspace_a),
        ("environment-a-shared", "workspace-a", workspace_a),
        ("environment-b", "workspace-b", workspace_b),
    ):
        resources.environments.register_initial(
            ExecutionEnvironmentDescriptor(
                environment_id=environment_id,
                workspace_id=workspace_id,
                kind=EnvironmentKind.HOST,
                state=EnvironmentState.READY,
                generation=1,
            ),
            workspace,
        )

    service = object.__new__(AgentService)
    service.workspace_registry = resources.workspaces
    service.environment_manager = resources.environments

    assert await service._execution_sources("environment-a") == (source_a,)
    assert await service._execution_sources("environment-a-shared") == (source_a,)
    assert await service._execution_sources("environment-b") == (source_b,)
