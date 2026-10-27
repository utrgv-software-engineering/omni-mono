"""Approval predicates go on an agent's own copies of its tools.

Tool objects are shared by every agent in a process. Gating them in place
made every later agent's calls go through the first session's predicate:
an agent that should not be gated was, and one that should be was gated
under another session, so whether an action was reviewed depended on which
agent (a worker, say) had been set up first.
"""

from types import SimpleNamespace

from agents import Agent, function_tool

from omniagents.core.agents.approval import (
    attach_tool_approval_predicates,
    configure_tool_approval,
)


@function_tool
def push(branch: str) -> str:
    """Push a branch."""
    return branch


def test_gating_one_agent_leaves_the_shared_tool_and_other_agents_alone():
    gated = Agent(name="worker", tools=[push])
    ungated = Agent(name="implementer", tools=[push])

    configure_tool_approval(gated, session=SimpleNamespace(id="worker-session"))

    [own] = gated.tools
    assert own is not push and callable(own.needs_approval)
    assert push.needs_approval is False  # the shared tool is unchanged
    assert ungated.tools[0].needs_approval is False


def test_each_session_gets_its_own_predicate():
    first = Agent(name="a", tools=[push])
    second = Agent(name="b", tools=[push])
    configure_tool_approval(first, session=SimpleNamespace(id="s1"))
    configure_tool_approval(second, session=SimpleNamespace(id="s2"))
    assert first.tools[0].needs_approval is not second.tools[0].needs_approval


def test_bare_tool_lists_are_gated_as_copies():
    [own] = attach_tool_approval_predicates([push], session=SimpleNamespace(id="s"))
    assert own is not push and callable(own.needs_approval)
    assert push.needs_approval is False
