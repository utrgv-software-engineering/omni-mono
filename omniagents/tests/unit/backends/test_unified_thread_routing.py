"""A default environment is data, not a second routing path.

``build_app(default_host_workspace_root=...)`` (TUI, web UI, notebook) and a
targetless AgentHost whose controller binds each thread explicitly (desktop
``omni serve``) must route a session identically: every operation reads the
thread's binding and nothing else. The first operation a fresh session sends
can be a slash command, a server function, a run or a voice turn; each must
land in the same environment either way.
"""

from pathlib import Path

import pytest
from agents import Agent

from omniagents.backends.server.app import build_app
from omniagents.core.agents.specs import AgentSpec
from omniagents.execution import (
    AgentHostResources,
    bind_request_context,
    register_host_environment,
    require_environment_lease,
    reset_request_context,
)
from omniagents.rpc.protocol_catalog import (
    OPERATION_BY_NAME,
    ExecutionRequirement,
    ResourceScope,
)


async def _build_agent(settings, mcp, app):
    return Agent(name="unit", tools=[], instructions="Unit", model="gpt-4o-mini")


def _register_where(service) -> None:
    service.register_server_function(
        "where",
        lambda args: require_environment_lease().workspace.root,
        resource_scope=ResourceScope.THREAD,
        execution_requirements=ExecutionRequirement.WORKSPACE_READ,
    )


def _spec() -> AgentSpec:
    return AgentSpec(
        name="Unit",
        welcome_text="",
        get_agent_instructions=lambda: "Unit",
        build_agent=_build_agent,
        register_server_functions=_register_where,
    )


async def _routes(service, session_id: str) -> dict:
    """Where each first-operation kind of a fresh session executes."""

    context = await service.prepare_rpc_context(
        None, "server_call", {"function": "where", "session_id": session_id}
    )
    token = bind_request_context(context)
    try:
        server_call_root = await service.server_call("where", {}, session_id)
    finally:
        reset_request_context(token)
    run = await service.operation_router.prepare_protocol(
        OPERATION_BY_NAME["start_run"],
        {"session_id": session_id, "environment_selection": {"mode": "inherit"}},
    )
    voice = await service.prepare_environment_context_for_session(session_id)
    return {
        "server_call": Path(server_call_root).resolve(),
        "run": Path(run.environment_lease.workspace.root).resolve(),
        "voice": Path(voice.environment_lease.workspace.root).resolve(),
    }


@pytest.mark.asyncio
async def test_default_environment_binds_fresh_threads_like_the_desktop_controller(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    expected = {kind: workspace.resolve() for kind in ("server_call", "run", "voice")}

    # TUI / web / notebook: the default environment is composition data.
    standalone = build_app(spec=_spec(), default_host_workspace_root=str(workspace))
    standalone_service = standalone.state.agent_service
    assert await _routes(standalone_service, "fresh-session") == expected
    bound = await standalone_service.thread_bindings.get("fresh-session")
    assert bound.environment_id == standalone.state.default_environment_id

    # Desktop: a targetless host, the controller binds the thread explicitly.
    resources = AgentHostResources.create("desktop-host")
    desktop = build_app(spec=_spec(), agent_host_resources=resources)
    assert desktop.state.default_environment_id is None
    environment = register_host_environment(
        resources, str(workspace), owner_user_id=None
    )
    desktop_service = desktop.state.agent_service
    await desktop_service.agent_host_bind_thread(
        "fresh-session",
        {
            "workspace_id": environment.workspace_id,
            "environment_selection": {
                "mode": "existing",
                "environment_id": environment.environment_id,
            },
        },
    )
    assert await _routes(desktop_service, "fresh-session") == expected


@pytest.mark.asyncio
async def test_an_explicit_binding_overrides_the_default(tmp_path):
    default_root = tmp_path / "default"
    other_root = tmp_path / "other"
    default_root.mkdir()
    other_root.mkdir()
    app = build_app(spec=_spec(), default_host_workspace_root=str(default_root))
    service = app.state.agent_service
    other = register_host_environment(
        service.agent_host_resources, str(other_root), owner_user_id=None
    )
    await service.agent_host_bind_thread(
        "rebound",
        {
            "workspace_id": other.workspace_id,
            "environment_selection": {
                "mode": "existing",
                "environment_id": other.environment_id,
            },
        },
    )
    routes = await _routes(service, "rebound")
    assert set(routes.values()) == {other_root.resolve()}


@pytest.mark.asyncio
async def test_a_targetless_host_leaves_unbound_threads_without_an_environment():
    app = build_app(spec=_spec())
    service = app.state.agent_service
    with pytest.raises(Exception, match="requires an execution environment"):
        await service.prepare_rpc_context(
            None, "server_call", {"function": "where", "session_id": "unbound"}
        )
    assert await service.prepare_environment_context_for_session("unbound") is None
