from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from agents.run_config import SandboxRunConfig

from omniagents.core.agents import service as service_module
from omniagents.core.agents.service import AgentService
from omniagents.core.agents.specs import AgentSpec
from omniagents.execution import (
    AgentHostResources,
    AutomaticEnvironment,
    EnvironmentCapability,
    EnvironmentKind,
    EnvironmentLease,
    EnvironmentState,
    ExecutionEnvironmentDescriptor,
    ProvisionedEnvironment,
    SandboxExecutionRuntime,
    ThreadRuntimeBinding,
    WorkspaceResource,
    current_request_context,
)
from omniagents.workspace.base import Workspace


def _lease(environment_id: str, session: object, run_as: str) -> EnvironmentLease:
    descriptor = ExecutionEnvironmentDescriptor(
        environment_id=environment_id,
        workspace_id=f"workspace-{environment_id}",
        kind=EnvironmentKind.CONTAINER,
        state=EnvironmentState.READY,
        generation=1,
    )
    workspace = Mock(spec=Workspace)
    workspace.root = "/workspace"
    return EnvironmentLease(
        descriptor,
        workspace,
        sandbox_runtime=SandboxExecutionRuntime(session, run_as=run_as),
    )


def _spec(build_agent) -> AgentSpec:
    return AgentSpec(
        name="agent",
        welcome_text="welcome",
        get_agent_instructions=lambda: "instructions",
        build_agent=build_agent,
    )


def test_run_scoped_spec_uses_lease_without_mutating_agent_definition():
    definition_session = object()
    spec = _spec(None)
    spec.sandbox_use_sandbox_agent = True
    spec.sandbox_run_as = "definition-user"
    spec.sandbox_run_config = SandboxRunConfig(
        session=definition_session,
        options={"preserved": True},
    )
    service = AgentService(spec, AgentHostResources.create("runtime-spec-host"))
    environment_session = object()

    run_spec = service._spec_for_environment(
        _lease("environment-a", environment_session, "environment-user")
    )
    targetless_spec = service._spec_for_environment(None)

    assert run_spec is not spec
    assert run_spec.sandbox_use_sandbox_agent is True
    assert run_spec.sandbox_run_as == "environment-user"
    assert run_spec.sandbox_run_config.session is environment_session
    assert run_spec.sandbox_run_config.options == {"preserved": True}
    assert targetless_spec.sandbox_use_sandbox_agent is False
    assert targetless_spec.sandbox_run_config is None
    assert spec.sandbox_use_sandbox_agent is True
    assert spec.sandbox_run_as == "definition-user"
    assert spec.sandbox_run_config.session is definition_session


@pytest.mark.asyncio
async def test_concurrent_agent_builds_keep_environment_sessions_isolated():
    captured_specs = []
    both_entered = asyncio.Event()
    release = asyncio.Event()

    async def build_agent(settings, primary_server, stub_app):
        captured_specs.append(stub_app.spec)
        if len(captured_specs) == 2:
            both_entered.set()
        await release.wait()
        return SimpleNamespace(tools=[], mcp_servers=[])

    spec = _spec(build_agent)
    service = AgentService(spec, AgentHostResources.create("concurrent-runtime-host"))
    service._clone_agent = lambda agent: agent
    session_a = service._session_manager.get_or_create("thread-a")
    session_b = service._session_manager.get_or_create("thread-b")
    runtime_session_a = object()
    runtime_session_b = object()
    run_spec_a = service._spec_for_environment(
        _lease("environment-a", runtime_session_a, "user-a")
    )
    run_spec_b = service._spec_for_environment(
        _lease("environment-b", runtime_session_b, "user-b")
    )

    task_a = asyncio.create_task(
        service._build_agent_for_session(session_a, None, spec=run_spec_a)
    )
    task_b = asyncio.create_task(
        service._build_agent_for_session(session_b, None, spec=run_spec_b)
    )
    await asyncio.wait_for(both_entered.wait(), timeout=1)

    assert {item.sandbox_run_config.session for item in captured_specs} == {
        runtime_session_a,
        runtime_session_b,
    }
    assert {item.sandbox_run_as for item in captured_specs} == {"user-a", "user-b"}
    assert spec.sandbox_run_config is None
    assert spec.sandbox_use_sandbox_agent is False

    release.set()
    await asyncio.gather(task_a, task_b)


@pytest.mark.asyncio
async def test_start_run_passes_environment_runtime_to_builder_and_runner(monkeypatch):
    built_specs = []
    streamed_configs = []
    streamed_contexts = []

    async def build_agent(settings, primary_server, stub_app):
        built_specs.append(stub_app.spec)
        return SimpleNamespace(tools=[], mcp_servers=[])

    async def stream_agent(*args, **kwargs):
        streamed_configs.append(kwargs["sandbox_run_config"])
        streamed_contexts.append(current_request_context())
        return {}

    monkeypatch.setattr(service_module, "stream_agent", stream_agent)
    resources = AgentHostResources.create("start-run-runtime-host")
    workspace = Mock(spec=Workspace)
    workspace.root = "/workspace"
    resources.workspaces.register(WorkspaceResource("workspace-a"))
    runtime_session = object()
    resources.environments.register_initial(
        ExecutionEnvironmentDescriptor(
            environment_id="environment-a",
            workspace_id="workspace-a",
            kind=EnvironmentKind.CONTAINER,
            state=EnvironmentState.READY,
            generation=1,
        ),
        workspace,
        sandbox_runtime=SandboxExecutionRuntime(
            runtime_session,
            run_as="environment-user",
        ),
    )
    spec = _spec(build_agent)
    service = AgentService(spec, resources)

    result = await service.start_run(
        "prompt",
        {"mode": "explicit", "environment_id": "environment-a"},
    )
    session = service._session_manager.get_or_create(result["session_id"])
    await asyncio.wait_for(session.current_task, timeout=1)

    assert built_specs[0].sandbox_run_config.session is runtime_session
    assert built_specs[0].sandbox_run_as == "environment-user"
    assert streamed_configs[0] is built_specs[0].sandbox_run_config
    assert streamed_contexts[0].environment_lease.environment_id == "environment-a"
    assert spec.sandbox_run_config is None
    assert spec.sandbox_use_sandbox_agent is False


@pytest.mark.asyncio
async def test_start_run_materializes_inherited_automatic_environment(monkeypatch):
    runtime_session = object()
    provision_requests = []
    streamed_contexts = []

    class Provisioner:
        async def provision(self, request):
            provision_requests.append(request)
            workspace = Mock(spec=Workspace)
            workspace.root = "/workspace"
            return ProvisionedEnvironment(
                workspace=workspace,
                kind=EnvironmentKind.CONTAINER,
                capabilities=frozenset({EnvironmentCapability.WORKSPACE_READ}),
                sandbox_runtime=SandboxExecutionRuntime(runtime_session),
            )

    async def build_agent(settings, primary_server, stub_app):
        return SimpleNamespace(tools=[], mcp_servers=[])

    async def stream_agent(*args, **kwargs):
        streamed_contexts.append(current_request_context())
        return {}

    monkeypatch.setattr(service_module, "stream_agent", stream_agent)
    resources = AgentHostResources.create(
        "automatic-start-run-host", environment_provisioner=Provisioner()
    )
    resources.workspaces.register(WorkspaceResource("workspace-a"))
    await resources.thread_bindings.bind(
        "thread-a",
        ThreadRuntimeBinding(
            workspace_id="workspace-a",
            environment_selection=AutomaticEnvironment("devbox"),
        ),
    )
    service = AgentService(_spec(build_agent), resources)

    result = await service.start_run(
        "prompt",
        {"mode": "inherit"},
        session_id="thread-a",
    )
    session = service._session_manager.get_or_create(result["session_id"])
    await asyncio.wait_for(session.current_task, timeout=1)

    assert len(provision_requests) == 1
    assert provision_requests[0].workspace.workspace_id == "workspace-a"
    assert streamed_contexts[0].environment_lease.sandbox_runtime.session is (
        runtime_session
    )
