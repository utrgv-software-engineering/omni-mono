"""MCP Apps on a session: its server sets' lifetimes and an app's own tool calls.

A set of a session's MCP servers is closed only when nothing holds it (a run
holds the set its agent was built with; an out-of-band call holds it for the
call), and an app's out-of-band ``mcp_call_tool`` passes the checks a run's
tool call does: the tool's visibility, the agent's tool-input guardrails, and
its approvals.
"""

import asyncio
import types

import pytest
from agents.tool_guardrails import ToolGuardrailFunctionOutput, ToolInputGuardrail

from omniagents.core.agents import mcp as mcp_mod
from omniagents.core.agents.service import AgentService
from omniagents.execution import AgentHostResources

from .test_agent_builder import make_spec


def build_service(**spec_changes) -> AgentService:
    spec = make_spec()
    for key, value in spec_changes.items():
        setattr(spec, key, value)
    return AgentService(spec, AgentHostResources.create("test-unit-core-mcp-app-calls-host"))


def live_set(session):
    """A session with a live server set: its lifecycle task waits to be told to close."""
    shutdown = asyncio.Event()
    task = asyncio.get_event_loop().create_task(shutdown.wait())
    session._mcp_lifecycle_task = task
    session._mcp_shutdown = shutdown
    session._mcp_servers_active = []
    return task, shutdown


# ——— Lifetimes ———


@pytest.mark.asyncio
async def test_a_held_set_is_retired_and_closes_with_its_last_holder():
    service = build_service()
    session = types.SimpleNamespace(id="s1")
    task, shutdown = live_set(session)
    run = service._mcp_hold(session, task)
    app_call = service._mcp_hold(session, task)

    await service._retire_session_mcp(session)

    # (the session has moved on; the held set stays open)
    assert session._mcp_lifecycle_task is None
    assert not shutdown.is_set()
    service._mcp_release(session, app_call)
    assert not shutdown.is_set()
    service._mcp_release(session, run)
    assert shutdown.is_set()
    await task


@pytest.mark.asyncio
async def test_a_set_nothing_holds_closes_when_retired():
    service = build_service()
    session = types.SimpleNamespace(id="s1")
    task, shutdown = live_set(session)

    await service._retire_session_mcp(session)

    assert shutdown.is_set()
    assert task.done()


@pytest.mark.asyncio
async def test_releasing_the_current_set_leaves_it_open():
    service = build_service()
    session = types.SimpleNamespace(id="s1")
    task, shutdown = live_set(session)
    token = service._mcp_hold(session, task)

    service._mcp_release(session, token)

    # (the live set stays for the next run or call)
    assert not shutdown.is_set()
    shutdown.set()
    await task


# ——— Visibility ———


class FakeTool:
    def __init__(self, name, visibility=None):
        self.name = name
        self.meta = {"ui": {"visibility": visibility}} if visibility else None


@pytest.mark.asyncio
async def test_the_agent_is_offered_only_tools_visible_to_the_model():
    tools = [FakeTool("show"), FakeTool("refresh", ["app"]), FakeTool("both", ["model", "app"])]

    class Server:
        name = "vis-test"

        async def list_tools(self, *args, **kwargs):
            return tools

        async def call_tool(self, *args, **kwargs):
            return None

    server = mcp_mod._attach_ui_capture(Server())
    offered = await server.list_tools()

    assert [t.name for t in offered] == ["show", "both"]
    assert mcp_mod.tool_visibility("vis-test", "refresh") == ("app",)
    assert mcp_mod.tool_visibility("vis-test", "show") == ("model", "app")


# ——— An app's own calls ———


class AppServer:
    name = "app-test"

    def __init__(self, tools):
        self._tools = tools
        self.called = []

    async def list_tools(self, *args, **kwargs):
        return self._tools

    async def call_tool(self, tool_name, arguments=None, *args, **kwargs):
        self.called.append((tool_name, arguments))
        return types.SimpleNamespace(model_dump=lambda **_: {"content": [{"type": "text", "text": "done"}]})


def app_service(server, **spec_changes):
    service = build_service(**spec_changes)
    session = types.SimpleNamespace(id="s1", channels=[])
    wrapped = mcp_mod._attach_ui_capture(server)

    async def ensure(sess, environment_lease=None, *, hold=None):
        return [wrapped]

    service._ensure_session_mcp_servers = ensure
    return service, session


@pytest.mark.asyncio
async def test_an_app_cannot_call_a_tool_only_the_model_may():
    server = AppServer([FakeTool("secret", ["model"])])
    service, session = app_service(server)

    with pytest.raises(PermissionError):
        await service._sf_mcp_call_tool(session, {"server_name": "app-test", "tool_name": "secret"})
    assert server.called == []


@pytest.mark.asyncio
async def test_an_app_call_waits_for_approval_like_the_agents_own():
    server = AppServer([FakeTool("send", ["app"])])
    opts = types.SimpleNamespace(skip_approvals=False, safe_tool_names=[], safe_tool_patterns=[])
    service, session = app_service(server, use_safe_agent=True, safe_agent_options=opts)
    asked = []

    async def request_tool_approval(**kwargs):
        asked.append(kwargs["tool_name"])
        return {"decision": "reject", "always_approve": False, "rejection_message": "Not now"}

    service.request_tool_approval = request_tool_approval
    out = await service._sf_mcp_call_tool(session, {"server_name": "app-test", "tool_name": "send", "arguments": {"to": "x"}})

    assert asked == ["mcp_app-test__send"]
    assert out["result"]["isError"] is True
    assert server.called == []

    # (granted always for this server's tool: no question, it runs)
    session.always_allow_mcp_tools = {("app-test", "send")}
    out = await service._sf_mcp_call_tool(session, {"server_name": "app-test", "tool_name": "send"})
    assert asked == ["mcp_app-test__send"]
    assert server.called == [("send", {})]


@pytest.mark.asyncio
async def test_an_app_call_passes_the_agents_tool_guardrails():
    server = AppServer([FakeTool("delete_all", ["app"])])
    seen = []

    def refuse(data):
        seen.append((data.context.tool_name, data.context.tool_arguments))
        return ToolGuardrailFunctionOutput.reject_content("Not run: a project rule forbids it")

    service, session = app_service(server, tool_input_guardrails=[ToolInputGuardrail(guardrail_function=refuse)])
    out = await service._sf_mcp_call_tool(session, {"server_name": "app-test", "tool_name": "delete_all", "arguments": {"all": True}})

    assert seen == [("mcp_app-test__delete_all", '{"all": true}')]
    assert out["result"]["content"][0]["text"] == "Not run: a project rule forbids it"
    assert server.called == []


# ——— A run's hold, let go of with its reservation ———


def test_a_runs_release_callbacks_run_once_even_when_the_store_fails():
    from omniagents.core.runtime.ownership import RunOwnership

    class Store:
        def __init__(self, fail):
            self.fail = fail

        def mutate_queue(self, thread_id, fn):
            if self.fail:
                raise RuntimeError("store unreachable")

    for fail in (False, True):
        ownership = RunOwnership(store=Store(fail), thread_id="t", run_id="r", owner="o", generation=1)
        released = []
        ownership.add_release_callback(lambda: released.append(1))
        if fail:
            with pytest.raises(RuntimeError):
                ownership.release()
        else:
            ownership.release()
        assert released == [1]
        if not fail:
            ownership.release()
            assert released == [1]
