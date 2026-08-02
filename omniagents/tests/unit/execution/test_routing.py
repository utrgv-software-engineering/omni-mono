from unittest.mock import Mock

import pytest

from omniagents.execution import (
    AgentHostResources,
    CapabilityUnavailableError,
    EnvironmentCapability,
    EnvironmentKind,
    EnvironmentRequiredError,
    EnvironmentState,
    ExistingEnvironment,
    ExecutionEnvironmentDescriptor,
    ExecutionEnvironmentManager,
    OperationExecutionDescriptor,
    OperationRouter,
    ThreadRuntimeBindingRegistry,
    ThreadRuntimeBinding,
    WorkspaceEnvironmentMismatchError,
    WorkspaceRegistry,
    WorkspaceResource,
    register_host_environment,
)
from omniagents.rpc.protocol_catalog import (
    OPERATION_BY_NAME,
    ExecutionRequirement,
    ResourceScope,
)
from omniagents.workspace.base import Workspace


async def make_router(
    capabilities: frozenset[EnvironmentCapability],
) -> OperationRouter:
    workspaces = WorkspaceRegistry()
    workspaces.register(WorkspaceResource("workspace-1", owner_user_id="user-1"))
    environments = ExecutionEnvironmentManager(workspaces)
    backend = Mock(spec=Workspace)
    backend.root = "/workspace"
    await environments.register(
        ExecutionEnvironmentDescriptor(
            environment_id="env-1",
            workspace_id="workspace-1",
            kind=EnvironmentKind.DEVBOX,
            state=EnvironmentState.READY,
            generation=3,
            capabilities=capabilities,
        ),
        backend,
    )
    bindings = ThreadRuntimeBindingRegistry(workspaces, environments)
    return OperationRouter(workspaces, environments, bindings, agent_host_id="host-1")


@pytest.mark.asyncio
async def test_control_plane_operation_acquires_no_environment():
    router = await make_router(frozenset())

    context = await router.prepare_protocol(
        OPERATION_BY_NAME["get_agent_info"], {}, principal="principal"
    )

    assert context.principal == "principal"
    assert context.agent_host_id == "host-1"
    assert context.environment is None
    assert context.environment_lease is None


@pytest.mark.asyncio
async def test_run_plan_explicit_selection_acquires_generation_checked_lease():
    router = await make_router(frozenset())

    context = await router.prepare_protocol(
        OPERATION_BY_NAME["start_run"],
        {
            "prompt": "hello",
            "environment_selection": {
                "mode": "explicit",
                "environment_id": "env-1",
                "environment_generation": 3,
            },
        },
    )

    assert context.environment_lease is not None
    assert context.environment_lease.environment_id == "env-1"
    assert context.environment_lease.generation == 3


@pytest.mark.asyncio
async def test_run_plan_none_selection_is_targetless():
    router = await make_router(frozenset())

    context = await router.prepare_protocol(
        OPERATION_BY_NAME["start_run"],
        {"prompt": "hello", "environment_selection": {"mode": "none"}},
    )

    assert context.workspace is None
    assert context.environment_lease is None


@pytest.mark.asyncio
async def test_run_plan_inherits_thread_runtime_binding(tmp_path):
    resources = AgentHostResources.create("host-1")
    descriptor = register_host_environment(
        resources,
        str(tmp_path),
        owner_user_id=None,
        workspace_id="workspace-1",
        environment_id="environment-1",
    )
    await resources.thread_bindings.bind(
        "thread-1",
        ThreadRuntimeBinding(
            workspace_id=descriptor.workspace_id,
            environment_selection=ExistingEnvironment(descriptor.environment_id),
        ),
    )

    context = await resources.router.prepare_protocol(
        OPERATION_BY_NAME["start_run"],
        {
            "prompt": "continue",
            "session_id": "thread-1",
            "environment_selection": {"mode": "inherit"},
        },
    )

    assert context.environment_lease is not None
    assert context.environment_lease.environment_id == "environment-1"


@pytest.mark.asyncio
async def test_first_inherit_atomically_initializes_targetless_binding():
    resources = AgentHostResources.create("host-1")

    context = await resources.router.prepare_protocol(
        OPERATION_BY_NAME["start_run"],
        {
            "prompt": "queued first turn",
            "session_id": "thread-new",
            "environment_selection": {"mode": "inherit"},
        },
    )

    assert context.environment_lease is None
    assert await resources.thread_bindings.get("thread-new") == ThreadRuntimeBinding()


@pytest.mark.asyncio
async def test_workspace_operation_requires_explicit_environment_id():
    router = await make_router(frozenset({EnvironmentCapability.WORKSPACE_READ}))

    with pytest.raises(EnvironmentRequiredError):
        await router.prepare_protocol(OPERATION_BY_NAME["fs_list"], {})


@pytest.mark.asyncio
async def test_workspace_operation_resolves_immutable_lease():
    router = await make_router(frozenset({EnvironmentCapability.WORKSPACE_READ}))

    context = await router.prepare_protocol(
        OPERATION_BY_NAME["fs_list"],
        {"environment_id": "env-1", "environment_generation": 3},
    )

    assert context.environment_lease is not None
    assert context.environment_lease.environment_id == "env-1"
    assert context.environment_lease.generation == 3
    assert context.workspace is not None
    assert context.workspace.workspace_id == "workspace-1"
    assert context.resource_owner_user_id == "user-1"


@pytest.mark.asyncio
async def test_git_push_requires_all_declared_capabilities():
    router = await make_router(
        frozenset(
            {
                EnvironmentCapability.WORKSPACE_WRITE,
                EnvironmentCapability.PROCESS,
            }
        )
    )

    with pytest.raises(CapabilityUnavailableError) as exc_info:
        await router.prepare_protocol(
            OPERATION_BY_NAME["git_push"], {"environment_id": "env-1"}
        )
    assert exc_info.value.data["missing"] == ["network"]


@pytest.mark.asyncio
async def test_explicit_workspace_must_match_environment():
    router = await make_router(frozenset({EnvironmentCapability.WORKSPACE_READ}))

    with pytest.raises(WorkspaceEnvironmentMismatchError):
        await router.prepare_protocol(
            OPERATION_BY_NAME["fs_list"],
            {"environment_id": "env-1", "workspace_id": "workspace-other"},
        )


@pytest.mark.asyncio
async def test_lifecycle_operation_resolves_ready_immutable_lease():
    router = await make_router(frozenset({EnvironmentCapability.ENVIRONMENT_LIFECYCLE}))
    operation = OperationExecutionDescriptor(
        name="environment.stop",
        resource_scope=ResourceScope.ENVIRONMENT,
        execution_requirements=ExecutionRequirement.ENVIRONMENT_LIFECYCLE,
    )

    context = await router.prepare(operation, {"environment_id": "env-1"})

    assert context.environment is not None
    assert context.environment.environment_id == "env-1"
    assert context.environment_lease is not None
    assert context.environment_lease.environment_id == "env-1"
    assert context.environment_lease.generation == 3


@pytest.mark.asyncio
async def test_outer_extension_marker_cannot_reach_a_handler():
    router = await make_router(frozenset())

    with pytest.raises(TypeError, match="extension descriptor"):
        await router.prepare_protocol(
            OPERATION_BY_NAME["server_call"], {"environment_id": "env-1"}
        )
