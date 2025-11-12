import asyncio
import json
from types import SimpleNamespace

import pytest

from agents import Agent, RunContextWrapper, handoff
from agents.tool import FunctionTool

from omniagents.core.agents.safe import SafeAgent
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.agents.service import AgentService


class FakeChannel:
    def __init__(self, service, decisions):
        self.service = service
        self.decisions = list(decisions)
        self.calls = []
        self.approval_requests = []
        self.pending = []
        self.run_end = asyncio.Event()

    async def async_call(self, method, args=None):
        self.calls.append((method, args))
        if (
            method == "client_request"
            and args
            and args.get("function") == "ui.request_tool_approval"
        ):
            self.approval_requests.append(args)
            decision = self.decisions.pop(0) if self.decisions else None
            if decision is not None:

                async def respond():
                    await asyncio.sleep(0)
                    await self.service.client_response(
                        request_id=args["request_id"],
                        ok=decision.get("ok", True),
                        result=decision.get("result"),
                        error=decision.get("error"),
                    )

                task = asyncio.create_task(respond())
                self.pending.append(task)
        if method == "run_end":
            self.run_end.set()
        return True


@pytest.fixture(autouse=True)
def temp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path))


def build_tracking_tools(definitions, recorder):
    tools = []
    for name, response in definitions:

        async def _invoke(_ctx, params_json, *, tool_name=name, output=response):
            payload = json.loads(params_json) if params_json else {}
            recorder.append({"name": tool_name, "args": payload})
            return json.dumps({"result": output, "tool": tool_name})

        tools.append(
            FunctionTool(
                name=name,
                description=name,
                params_json_schema={},
                on_invoke_tool=_invoke,
            )
        )
    return tools


def build_spec(
    tool_definitions, recorder, *, skip_approvals=False, halt_on_rejection=True
):
    async def build_agent(settings, mcp_server, stub_app):
        tools = build_tracking_tools(tool_definitions, recorder)
        return SafeAgent(
            name="safe",
            tools=tools,
            instructions="assist",
            model="gpt",
            approval_callback=stub_app._request_tool_approval,
            skip_approvals=skip_approvals,
            halt_on_rejection=halt_on_rejection,
        )

    return AgentSpec(
        name="safe",
        welcome_text="",
        get_agent_instructions=lambda: "assist",
        build_agent=build_agent,
    )


@pytest.mark.asyncio
async def test_safe_agent_requests_approval_via_rpc(monkeypatch):
    recorder = []
    spec = build_spec([("dangerous_tool", "executed")], recorder)
    service = AgentService(spec)
    decisions = [{"result": {"approved": True, "always_approve": False}}]
    channel = FakeChannel(service, decisions)
    service.channel = channel
    run_agents = []

    async def fake_stream_agent(agent, prompt, session, rpc_channel, **kwargs):
        run_agents.append(agent)
        tools = await agent.get_all_tools(None)
        await tools[0].on_invoke_tool(None, json.dumps({"task": prompt}))
        return {"completed": True}

    monkeypatch.setattr(
        "omniagents.core.agents.service.stream_agent", fake_stream_agent
    )
    await service.start_run("List files")
    await asyncio.wait_for(channel.run_end.wait(), timeout=1)
    if channel.pending:
        await asyncio.gather(*channel.pending, return_exceptions=True)
    assert len(channel.approval_requests) == 1
    request_payload = channel.approval_requests[0]["args"]
    assert request_payload["tool"] == "dangerous_tool"
    assert recorder[0] == {"name": "dangerous_tool", "args": {"task": "List files"}}
    assert isinstance(run_agents[0], SafeAgent)


@pytest.mark.asyncio
async def test_handoff_wrapping_propagates_settings():
    async def callback(name, formatted, metadata):
        return True, False, None

    child_tool_calls = []
    grand_tool_calls = []

    def tracking_tool(name, bucket):
        async def _invoke(_ctx, params_json):
            payload = json.loads(params_json) if params_json else {}
            bucket.append(payload)
            return json.dumps({"result": name})

        return FunctionTool(
            name=name, description=name, params_json_schema={}, on_invoke_tool=_invoke
        )

    grandchild_agent = Agent(
        name="grandchild",
        instructions="",
        model="gpt",
        tools=[tracking_tool("grand_tool", grand_tool_calls)],
    )
    child_agent = Agent(
        name="child",
        instructions="",
        model="gpt",
        tools=[tracking_tool("child_tool", child_tool_calls)],
        handoffs=[handoff(grandchild_agent)],
    )
    parent_agent = SafeAgent(
        name="parent",
        tools=[tracking_tool("parent_tool", [])],
        instructions="",
        model="gpt",
        approval_callback=callback,
        safe_tool_names=["child_tool"],
        safe_tool_patterns=[r"grand_.*"],
        handoffs=[handoff(child_agent)],
    )
    ctx = RunContextWrapper(context=None)
    wrapped_child = await parent_agent.handoffs[0].on_invoke_handoff(
        ctx, json.dumps({})
    )
    assert isinstance(wrapped_child, SafeAgent)
    assert wrapped_child._approval_callback is callback
    assert wrapped_child.is_tool_safe(tracking_tool("child_tool", [])) is True
    nested = await wrapped_child.handoffs[0].on_invoke_handoff(ctx, json.dumps({}))
    assert isinstance(nested, SafeAgent)
    assert nested._approval_callback is callback
    assert nested.is_tool_safe(tracking_tool("grand_tool", [])) is True


@pytest.mark.asyncio
async def test_skip_approvals_runs_without_requests(monkeypatch):
    recorder = []
    spec = build_spec([("safe_tool", "done")], recorder, skip_approvals=True)
    service = AgentService(spec)
    channel = FakeChannel(service, [])
    service.channel = channel

    async def fake_stream_agent(agent, prompt, session, rpc_channel, **kwargs):
        tools = await agent.get_all_tools(None)
        await tools[0].on_invoke_tool(None, json.dumps({"prompt": prompt}))
        return {"completed": True}

    monkeypatch.setattr(
        "omniagents.core.agents.service.stream_agent", fake_stream_agent
    )
    await service.start_run("Execute")
    await asyncio.wait_for(channel.run_end.wait(), timeout=1)
    assert channel.approval_requests == []
    assert recorder[0] == {"name": "safe_tool", "args": {"prompt": "Execute"}}


@pytest.mark.asyncio
async def test_multi_tool_rejection_halts_execution(monkeypatch):
    recorder = []
    spec = build_spec(
        [("first_tool", "ok"), ("second_tool", "error")],
        recorder,
        halt_on_rejection=True,
    )
    service = AgentService(spec)
    decisions = [
        {"result": {"approved": True, "always_approve": False}},
        {"result": {"approved": False, "always_approve": False, "error_message": "no"}},
    ]
    channel = FakeChannel(service, decisions)
    service.channel = channel
    run_agents = []
    final_outputs = []

    async def fake_stream_agent(agent, prompt, session, rpc_channel, **kwargs):
        run_agents.append(agent)
        tools = await agent.get_all_tools(None)
        assert len(tools) == 2
        for tool in tools:
            result = await tool.on_invoke_tool(None, json.dumps({"prompt": prompt}))
            outcome = await agent._custom_tool_use_behavior(
                None, [SimpleNamespace(output=result)]
            )
            final_outputs.append(outcome)
            if outcome.is_final_output:
                break
        return {"completed": True}

    monkeypatch.setattr(
        "omniagents.core.agents.service.stream_agent", fake_stream_agent
    )
    await service.start_run("Deploy")
    await asyncio.wait_for(channel.run_end.wait(), timeout=1)
    if channel.pending:
        await asyncio.gather(*channel.pending, return_exceptions=True)
    assert len(channel.approval_requests) == 2
    assert recorder[0]["name"] == "first_tool"
    assert len(recorder) == 1
    assert channel.approval_requests[1]["args"]["tool"] == "second_tool"
    assert run_agents[0]._halt_run is True
    assert any(
        item.is_final_output and item.final_output == "" for item in final_outputs
    )
