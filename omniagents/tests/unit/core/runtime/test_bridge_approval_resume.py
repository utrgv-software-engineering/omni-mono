"""Regression test for the bridge's tool-approval resume path.

When ``stream_agent`` resumes after a tool-approval interruption it calls
``Runner.run_streamed(agent, state, ...)`` a second time.  The SDK's
``resolve_resumed_context`` replaces ``run_state._context`` with a fresh
``RunContextWrapper`` whenever ``context is not None`` — which would throw
away the approval entries that ``state.approve()`` just wrote, causing the
SDK to re-emit the same ``ToolApprovalItem`` and the UI to redisplay the
dialog the user already answered.

The bridge therefore must NOT pass ``context=`` on resume iterations.
This test pins that contract.
"""

from __future__ import annotations

import asyncio
from typing import Any, List
from unittest.mock import AsyncMock, Mock, patch

import pytest

from agents.run_state import RunState

import omniagents.core.runtime.bridge as bridge_module
from omniagents.core.runtime.bridge import stream_agent
from omniagents.core.session import Session


class _DummyAgent:
    def __init__(self) -> None:
        self.name = "T"
        self.tools: List[Any] = []
        self.instructions = ""
        self.model = "gpt-4"
        self.model_settings = None
        self.mcp_servers: List[Any] = []
        self.mcp_config: dict = {}
        self.handoffs: List[Any] = []
        self.handoff_description = None
        self.input_guardrails: List[Any] = []
        self.output_guardrails: List[Any] = []
        self.output_type = None
        self.prompt = None

    async def get_all_tools(self) -> List[Any]:
        return []

    def reset_run_state(self) -> None:
        pass


class _FakeState:
    """Minimal RunState double whose .approve() records the call.

    The bridge does ``state = result.to_state()`` then ``state.approve(item)``
    and feeds ``state`` back as the next ``run_input``.  We only need the
    object to ``isinstance(_, RunState)`` true so the bridge's resume guard
    fires — registering as a virtual subclass is enough.
    """

    def __init__(self) -> None:
        self.approvals: list[Any] = []
        self.rejections: list[tuple[Any, str | None]] = []

    def approve(self, item: Any, always_approve: bool = False) -> None:
        self.approvals.append((item, always_approve))

    def reject(
        self,
        item: Any,
        always_reject: bool = False,
        *,
        rejection_message: str | None = None,
    ) -> None:
        self.rejections.append((item, rejection_message))


# Real ``RunState`` is a dataclass with SDK-internal fields we don't want
# to populate here. The bridge's resume guard is ``isinstance(run_input,
# RunState)``; we make that check pass by swapping the symbol the bridge
# imported to point at ``_FakeState`` for the duration of the test (see
# the ``patch`` in the test body).


class _FakeApprovalItem:
    """Mimics ToolApprovalItem's shape used by the bridge."""

    def __init__(self, call_id: str, tool_name: str) -> None:
        self.tool_name = tool_name
        self.raw_item = Mock(call_id=call_id, name=tool_name, arguments="{}")


def _make_stream_result(*, interruptions: list[Any]):
    """Build a stand-in for ``RunResultStreaming``.

    The bridge only touches: ``stream_events()`` (async iter),
    ``interruptions``, and ``to_state()`` on the interrupted result.
    """
    state = _FakeState()
    result = Mock()
    result.interruptions = list(interruptions)
    result.to_state = Mock(return_value=state)
    # context_wrapper attribute exists on real RunResultStreaming; some
    # bridge code paths read it via getattr — give it a benign stub.
    result.context_wrapper = Mock()

    async def _empty_events():
        # Async generator that yields nothing, so the bridge's
        # ``async for event in result.stream_events():`` falls through
        # to the post-stream interruption check.
        if False:
            yield None

    result.stream_events = _empty_events
    return result, state


@pytest.mark.asyncio
async def test_resume_does_not_pass_context_to_runner():
    """After a tool-approval interruption, the second ``run_streamed`` call
    must omit ``context=`` so the SDK keeps ``run_state._context`` with the
    approval entries intact."""

    interrupted_result, fake_state = _make_stream_result(
        interruptions=[_FakeApprovalItem("call_42", "execute_bash")]
    )
    final_result, _ = _make_stream_result(interruptions=[])

    captured_calls: list[dict] = []

    def fake_run_streamed(agent, run_input, **kwargs):
        # Snapshot the kwargs so the test can inspect what the bridge
        # actually passed to the SDK on each iteration.
        captured_calls.append({"input": run_input, "kwargs": dict(kwargs)})
        # First call returns the interrupted result; subsequent calls
        # return the clean (no-interruptions) result.
        return interrupted_result if len(captured_calls) == 1 else final_result

    # Service stub whose request_tool_approval immediately approves.
    service = Mock()
    service.request_tool_approval = AsyncMock(
        return_value={
            "decision": "approve",
            "always_approve": False,
            "rejection_message": None,
        }
    )
    service.request_client_function = AsyncMock()
    service.invoke_runtime_hooks = AsyncMock(return_value=None)
    service.emit_runtime_hook = AsyncMock()

    session = Session("test_project", "test_agent")
    session.id = "sess-1"
    session.history = []
    session.channels = []
    session.pending_events = []
    session.pending_tool_approvals = {}

    rpc_channel = Mock()
    rpc_channel.async_call = AsyncMock()

    sentinel_ctx = {"workspace_root": "/tmp", "session_id": "sess-1"}

    with (
        patch("agents.Runner.run_streamed", side_effect=fake_run_streamed),
        # Swap the RunState reference the bridge uses for ``isinstance``
        # so our ``_FakeState`` registers as a RunState without us having
        # to build a real one (it requires SDK-internal fields).
        patch.object(bridge_module, "RunState", _FakeState),
    ):
        await stream_agent(
            _DummyAgent(),
            "Run a command",
            session,
            rpc_channel,
            service=service,
            context=sentinel_ctx,
        )

    # Two iterations: initial run + post-approval resume.
    assert len(captured_calls) == 2, (
        f"expected exactly two Runner.run_streamed calls "
        f"(initial + resume), got {len(captured_calls)}"
    )

    initial, resume = captured_calls

    # Initial call: bridge seeds context= from the caller and the input
    # is the raw prompt (a list of input items, not a RunState).
    assert (
        "context" in initial["kwargs"]
    ), "initial run_streamed call must receive context= from the bridge"
    assert initial["kwargs"]["context"] is sentinel_ctx
    assert not isinstance(initial["input"], RunState)

    # Resume call: the input MUST be the (fake) RunState produced by
    # to_state(), and context= MUST NOT appear in kwargs — otherwise the
    # SDK's resolve_resumed_context wipes the approval entries.
    assert resume["input"] is fake_state
    assert isinstance(resume["input"], _FakeState)
    assert "context" not in resume["kwargs"], (
        "resumed run_streamed must NOT receive context= "
        "(SDK would replace run_state._context and drop approval entries)"
    )

    # Sanity: the approval actually flowed through state.approve().
    assert fake_state.approvals, (
        "bridge must call state.approve() once the service returns "
        "decision='approve'"
    )
    assert fake_state.approvals[0][0].raw_item.call_id == "call_42"
