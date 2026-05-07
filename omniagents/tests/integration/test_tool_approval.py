"""Tool approval primitives — unit tests for the SDK 0.16+ flow.

The SafeAgent class is gone; tool approval is now driven by:

* :func:`omniagents.core.agents.approval.configure_tool_approval` —
  attaches per-tool ``needs_approval`` predicates to every FunctionTool.
* The session's ``always_allow_tools`` / ``always_allow_patterns`` sets,
  consulted by those predicates.
* The service-side ``request_tool_approval`` /
  ``tool_approval_response`` pair (tested in test_tool_approval_routing).
"""

import asyncio

import pytest
from agents import Agent
from agents.tool import FunctionTool

from omniagents.core.agents.approval import configure_tool_approval


def _make_tool(name: str) -> FunctionTool:
    async def _invoke(ctx, params_json: str):
        return ""

    return FunctionTool(
        name=name,
        description="",
        params_json_schema={
            "additionalProperties": False,
            "type": "object",
            "properties": {},
        },
        on_invoke_tool=_invoke,
    )


class _FakeSession:
    """Minimal session stub for approval predicates."""

    def __init__(self):
        self.always_allow_tools: set[str] = set()
        self.always_allow_patterns: set[str] = set()


@pytest.mark.asyncio
async def test_predicate_requires_approval_by_default():
    tool = _make_tool("delete_file")
    agent = Agent(name="A", instructions="", tools=[tool], model="m")

    session = _FakeSession()
    configure_tool_approval(agent, session=session)

    assert callable(tool.needs_approval)
    assert (await tool.needs_approval(None, {}, "call_1")) is True


@pytest.mark.asyncio
async def test_predicate_skips_when_session_allows_tool():
    tool = _make_tool("read_file")
    agent = Agent(name="A", instructions="", tools=[tool], model="m")

    session = _FakeSession()
    session.always_allow_tools.add("read_file")
    configure_tool_approval(agent, session=session)

    assert (await tool.needs_approval(None, {}, "call_1")) is False


@pytest.mark.asyncio
async def test_predicate_honors_session_pattern():
    tool = _make_tool("get_user_info")
    agent = Agent(name="A", instructions="", tools=[tool], model="m")

    session = _FakeSession()
    session.always_allow_patterns.add(r"^get_")
    configure_tool_approval(agent, session=session)

    assert (await tool.needs_approval(None, {}, "call_1")) is False


@pytest.mark.asyncio
async def test_predicate_honors_spec_allow_list_and_patterns():
    safe = _make_tool("list_dir")
    risky = _make_tool("rm_rf")
    safe_pattern = _make_tool("status_health")
    agent = Agent(
        name="A", instructions="", tools=[safe, risky, safe_pattern], model="m"
    )

    session = _FakeSession()
    configure_tool_approval(
        agent,
        session=session,
        safe_tool_names=["list_dir"],
        safe_tool_patterns=[r".*_health$"],
    )

    assert (await safe.needs_approval(None, {}, "c1")) is False
    assert (await safe_pattern.needs_approval(None, {}, "c2")) is False
    assert (await risky.needs_approval(None, {}, "c3")) is True


@pytest.mark.asyncio
async def test_skip_approvals_disables_predicate():
    tool = _make_tool("anything")
    agent = Agent(name="A", instructions="", tools=[tool], model="m")

    session = _FakeSession()
    configure_tool_approval(agent, session=session, skip_approvals=True)

    # Predicate is attached but always returns False.
    assert callable(tool.needs_approval)
    assert (await tool.needs_approval(None, {}, "c1")) is False


@pytest.mark.asyncio
async def test_existing_needs_approval_is_not_overridden():
    tool = _make_tool("custom")

    async def custom_predicate(ctx, args, call_id):
        return True

    tool.needs_approval = custom_predicate
    agent = Agent(name="A", instructions="", tools=[tool], model="m")

    session = _FakeSession()
    configure_tool_approval(agent, session=session)

    assert tool.needs_approval is custom_predicate


@pytest.mark.asyncio
async def test_predicate_propagates_to_handoff_agents():
    sub_tool = _make_tool("sub_tool")
    sub_agent = Agent(name="Sub", instructions="", tools=[sub_tool], model="m")

    main_tool = _make_tool("main_tool")
    main_agent = Agent(
        name="Main",
        instructions="",
        tools=[main_tool],
        model="m",
        handoffs=[sub_agent],
    )

    session = _FakeSession()
    configure_tool_approval(main_agent, session=session)

    # Both the main agent's tool and the handoff target's tool should be configured.
    assert callable(main_tool.needs_approval)
    assert callable(sub_tool.needs_approval)
    assert (await sub_tool.needs_approval(None, {}, "c1")) is True
