import asyncio

import pytest

from omniagents.core.agents.service import AgentService
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope


def _empty_service() -> AgentService:
    service = AgentService.__new__(AgentService)
    service._server_functions = {}
    return service


def test_agent_service_stores_and_lists_server_function_execution_metadata():
    service = _empty_service()

    def handler():
        return None

    requirements = ExecutionRequirement.WORKSPACE_WRITE | ExecutionRequirement.PROCESS
    service.register_server_function(
        "workspace.mutate",
        handler,
        resource_scope=ResourceScope.WORKSPACE,
        execution_requirements=requirements,
        description="Mutate a workspace",
    )

    stored = service._server_functions["workspace.mutate"]
    assert stored["resource_scope"] is ResourceScope.WORKSPACE
    assert stored["execution_requirements"] == requirements

    listed = asyncio.run(service.list_server_functions())
    assert listed == [
        {
            "name": "workspace.mutate",
            "description": "Mutate a workspace",
            "params_schema": None,
            "result_schema": None,
            "strict": None,
            "resource_scope": "workspace",
            "execution_requirements": ["workspace_write", "process"],
        }
    ]


def test_agent_service_rejects_untyped_server_function_execution_metadata():
    service = _empty_service()

    def handler():
        return None

    with pytest.raises(TypeError, match="resource_scope"):
        service.register_server_function(
            "bad.scope",
            handler,
            resource_scope="workspace",
            execution_requirements=ExecutionRequirement.WORKSPACE_READ,
        )
    with pytest.raises(TypeError, match="execution_requirements"):
        service.register_server_function(
            "bad.requirements",
            handler,
            resource_scope=ResourceScope.WORKSPACE,
            execution_requirements=1,
        )
