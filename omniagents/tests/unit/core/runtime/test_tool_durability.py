"""Exercise the real SDK invocation boundary, not stream-event timing."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agents import Agent, RunConfig, Runner, function_tool
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
)

from omniagents.core.conversation.model import ItemKind
from omniagents.core.conversation.recorder import (
    ConversationRecorder,
    recorder_for_session,
)
from omniagents.core.runtime.tool_durability import DurableToolHooks
from omniagents.core.session.manager import Session
from .test_persisted_response import CompletedResponseModel


class ToolModel(CompletedResponseModel):
    def __init__(self):
        self.calls = 0

    async def stream_response(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            yield ResponseCompletedEvent(
                type="response.completed",
                sequence_number=0,
                response=Response(
                    id="resp_tool",
                    created_at=0,
                    object="response",
                    model="offline",
                    parallel_tool_calls=False,
                    tool_choice="auto",
                    tools=[],
                    output=[
                        ResponseFunctionToolCall(
                            type="function_call",
                            name="effect",
                            call_id="reused",
                            arguments="{}",
                        )
                    ],
                ),
            )
        else:
            async for event in super().stream_response(*args, **kwargs):
                yield event


@pytest.mark.asyncio
async def test_stale_runtime_cannot_make_model_or_tool_calls():
    from omniagents.core.runtime.ownership import reserve_run, current_ownership

    session = Session("durability", "fencing")
    rec = recorder_for_session(session)
    rec.ensure_thread(session.id)
    owner = reserve_run(rec.store, session.id)
    hooks = DurableToolHooks(session, owner.run_id)
    owner.release()
    replacement = reserve_run(rec.store, session.id)
    token = current_ownership.set(owner)
    model = ToolModel()
    try:
        result = Runner.run_streamed(
            Agent(name="fenced", model=model),
            "must not execute",
            session=session,
            hooks=hooks,
            run_config=RunConfig(tracing_disabled=True),
        )
        with pytest.raises(RuntimeError, match="no longer current"):
            await result.run_loop_task
        assert model.calls == 0
        with pytest.raises(RuntimeError, match="no longer current"):
            await hooks.on_tool_start(SimpleNamespace(tool_call_id="call"), None, None)
        replacement.assert_current()
    finally:
        current_ownership.reset(token)
        replacement.release()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "claim", "complete"])
async def test_real_sdk_persists_before_invoking_and_before_exposing_result(
    monkeypatch, failure
):
    session = Session("durability", "tool")
    session.active_run_id = "run"
    rec = recorder_for_session(session)
    rec.ensure_thread(session.id)
    effects = []

    @function_tool
    async def effect() -> str:
        """Count an external effect."""
        effects.append("called")
        return "durable result"

    if failure:

        def fail(*args, **kwargs):
            raise RuntimeError("injected persistence failure")

        monkeypatch.setattr(ConversationRecorder, f"{failure}_tool_execution", fail)
    hooks = DurableToolHooks(session, "run")
    result = Runner.run_streamed(
        Agent(name="tool-test", model=ToolModel(), tools=[effect]),
        "run tool",
        session=session,
        hooks=hooks,
        run_config=RunConfig(tracing_disabled=True),
    )
    if failure:
        with pytest.raises(Exception, match="injected persistence failure"):
            await result.run_loop_task
    else:
        await result.run_loop_task
    assert len(effects) == (0 if failure == "claim" else 1)
    tools = [
        item
        for item in rec.store.list_items(session.id, limit=30)
        if item.kind == ItemKind.TOOL_CALL
    ]
    if failure == "claim":
        assert tools == []
    else:
        assert len(tools) == 1
        assert tools[0].content["execution_state"] == (
            "claimed" if failure else "completed"
        )
        if not failure:
            assert tools[0].content["output"] == "durable result"
        # A replacement invocation cannot execute a claimed/completed call again.
        with pytest.raises(RuntimeError, match="already claimed"):
            await DurableToolHooks(session, "run").on_tool_start(
                SimpleNamespace(tool_call_id="reused", tool_arguments="{}"),
                None,
                effect,
            )


@pytest.mark.asyncio
async def test_hooks_preserve_delegate_and_reused_ids_in_different_runs():
    session = Session("durability", "delegation")
    rec = recorder_for_session(session)
    rec.ensure_thread(session.id)
    delegate = SimpleNamespace(
        on_tool_start=AsyncMock(), on_tool_end=AsyncMock(), on_agent_start=AsyncMock()
    )
    for run in ("first", "second"):
        hooks = DurableToolHooks(session, run, delegate)
        context = SimpleNamespace(tool_call_id="same", tool_arguments="{}")
        tool = SimpleNamespace(name="tool")
        await hooks.on_tool_start(context, None, tool)
        await hooks.on_tool_end(context, None, tool, run)
        assert hooks.on_agent_start is delegate.on_agent_start
    assert delegate.on_tool_start.await_count == delegate.on_tool_end.await_count == 2
    items = rec.store.list_items(session.id, limit=10)
    assert [(item.turn_id, item.content["output"]) for item in items] == [
        ("first", "first"),
        ("second", "second"),
    ]


@pytest.mark.asyncio
async def test_real_mcp_adapter_correlates_ui_and_persists_it_before_stream_delivery():
    from agents.mcp.util import MCPUtil
    from agents.tool_context import ToolContext
    from mcp.types import CallToolResult, Tool
    from omniagents.core.agents.mcp import _attach_ui_capture
    from omniagents.core.agents import mcp_ui

    async def call_tool(name, arguments, **kwargs):
        assert kwargs == {"meta": {"existing": "metadata"}}
        return CallToolResult.model_validate(
            {
                "content": [
                    {
                        "type": "resource",
                        "resource": {
                            "uri": "ui://widget",
                            "mimeType": "text/html",
                            "text": "<p>owned widget</p>",
                        },
                    }
                ]
            }
        )

    server = _attach_ui_capture(
        SimpleNamespace(
            name="server",
            call_tool=call_tool,
            tool_meta_resolver=lambda ctx: {"existing": "metadata"},
            use_structured_content=False,
        )
    )
    session = Session("durability", "mcp")
    recorder_for_session(session).ensure_thread(session.id)
    hooks = DurableToolHooks(session, "run")
    context = ToolContext(
        context={}, tool_call_id="call", tool_name="widget", tool_arguments="{}"
    )
    token = mcp_ui.begin_run_scope()
    try:
        await hooks.on_tool_start(context, None, SimpleNamespace(name="widget"))
        result = await MCPUtil.invoke_mcp_tool(
            server, Tool(name="widget", inputSchema={"type": "object"}), context, "{}"
        )
        await hooks.on_tool_end(context, None, SimpleNamespace(name="widget"), result)
        item = recorder_for_session(session).store.list_items(session.id, limit=10)[0]
        assert (
            item.content["metadata"]["mcp_ui"]["resource"]["resource"]["text"]
            == "<p>owned widget</p>"
        )
        assert mcp_ui.pop("widget", "server", "call") is not None
    finally:
        mcp_ui.end_run_scope(token)
        mcp_ui.set_invocation_id(None)
