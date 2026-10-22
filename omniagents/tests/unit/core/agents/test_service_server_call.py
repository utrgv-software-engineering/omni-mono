"""server_call: one invocation per call, and the thread's environment when it names none."""

import pytest

from omniagents.core.agents.service import AgentService
from omniagents.core.agents.specs import AgentSpec
from omniagents.execution import AgentHostResources, register_host_environment
from omniagents.rpc.protocol_catalog import ExecutionRequirement, ResourceScope


def _service(tmp_path, *, with_default: bool) -> AgentService:
    resources = AgentHostResources.create("test-server-call-host")
    service = AgentService(
        AgentSpec(
            name="server-call",
            welcome_text="",
            get_agent_instructions=lambda: "",
            build_agent=lambda *a, **k: None,
        ),
        resources,
    )
    if with_default:
        resources.thread_bindings.set_initial_default(
            register_host_environment(resources, str(tmp_path), owner_user_id=None)
        )
    return service


@pytest.mark.asyncio
async def test_a_type_error_inside_a_function_is_raised_once(tmp_path):
    service = _service(tmp_path, with_default=False)
    calls = []

    async def broken(service, session, args):
        calls.append(args)
        raise TypeError("inside the function")

    service.register_server_function(
        "broken",
        broken,
        resource_scope=ResourceScope.THREAD,
        execution_requirements=ExecutionRequirement.NONE,
    )
    with pytest.raises(TypeError, match="inside the function"):
        await service.server_call("broken", {"x": 1})
    assert calls == [{"x": 1}]


@pytest.mark.asyncio
@pytest.mark.parametrize("with_default", [True, False])
async def test_environment_functions_route_through_the_thread_binding(
    tmp_path, with_default
):
    service = _service(tmp_path, with_default=with_default)
    service.register_server_function(
        "needs_workspace",
        lambda args: {"ok": True},
        resource_scope=ResourceScope.THREAD,
        execution_requirements=ExecutionRequirement.WORKSPACE_READ,
    )
    params = {"function": "needs_workspace", "session_id": "s-1"}
    if with_default:
        context = await service.prepare_rpc_context(None, "server_call", params)
        binding = await service.thread_bindings.get("s-1")
        assert context.environment.environment_id == binding.environment_id
    else:
        with pytest.raises(Exception, match="requires an execution environment"):
            await service.prepare_rpc_context(None, "server_call", params)
