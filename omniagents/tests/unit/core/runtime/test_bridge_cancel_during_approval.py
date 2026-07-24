"""Pin: cancelling a run while it's paused on tool approval must leave
the session in a state the API will accept on the next turn.

Repro for the user-visible error::

    Error code: 400 - 'No tool output found for function call call_XYZ.'

Symptom: the model emitted a function_call, the SDK paused with a
ToolApprovalItem, the user hit cancel before approve/reject, and the
NEXT user message tripped the API because the function_call sitting in
session history had no paired function_call_output.

This test drives the bridge's outer loop through one approval pause and
then cancels the run while it's awaiting ``request_tool_approval``. It
then asserts that the session history contains a matching
``function_call_output`` for every ``function_call`` — i.e. the bridge
fills the orphan in its ``finally`` cleanup.
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
    """Stand-in for RunState used only to make ``isinstance(_, RunState)``
    succeed via patch."""

    def __init__(self) -> None:
        self.approvals: list[Any] = []

    def approve(self, item: Any, always_approve: bool = False) -> None:
        self.approvals.append(item)

    def reject(self, *args, **kwargs) -> None:
        pass


class _FakeApprovalItem:
    def __init__(self, call_id: str, tool_name: str) -> None:
        self.tool_name = tool_name
        self.raw_item = Mock(call_id=call_id, name=tool_name, arguments="{}")


class _FakeToolCallItem:
    """Stand-in for a ToolCallItem stream event item — the bridge maps
    ``item.type == 'tool_call_item'`` into a ``tool_called`` notification
    which is what populates ``pending_tool_calls``."""

    type = "tool_call_item"

    def __init__(self, call_id: str, name: str, arguments: str = "{}") -> None:
        self._call_id = call_id
        self._name = name
        self._arguments = arguments

    def to_input_item(self) -> dict:
        return {
            "type": "function_call",
            "name": self._name,
            "call_id": self._call_id,
            "arguments": self._arguments,
            "id": f"fc_{self._call_id}",
        }


@pytest.mark.asyncio
async def test_cancel_during_approval_leaves_no_orphan_function_call():
    """The model emits a function_call, the SDK pauses for approval, the
    user cancels mid-await. The bridge's finally cleanup MUST append a
    synthetic ``function_call_output`` so the next turn's history is
    well-formed for OpenAI Responses."""

    call_id = "call_sVJEYXeUWeGOObUCVjDhjd7i"  # echoes the user's bug report
    tool_name = "execute_bash"

    # Build a stream result that:
    #   - yields a single tool_call_item event (populates pending_tool_calls)
    #   - ends with one ToolApprovalItem in result.interruptions
    interruption = _FakeApprovalItem(call_id, tool_name)
    fake_state = _FakeState()
    result = Mock()
    result.interruptions = [interruption]
    result.to_state = Mock(return_value=fake_state)
    result.context_wrapper = Mock()

    tool_call_event = Mock()
    tool_call_event.type = "run_item_stream_event"
    tool_call_event.item = _FakeToolCallItem(call_id, tool_name)

    async def _stream_events():
        yield tool_call_event

    result.stream_events = _stream_events

    # Faithful to the real SDK flow: ``_finalize_streamed_interruption``
    # persists the function_call to the session BEFORE setting
    # ``result.interruptions``. Without this, the test can't tell whether
    # the bridge cleanup actually pairs the persisted function_call.
    def fake_run_streamed(agent, run_input, **kwargs):
        session_arg = kwargs.get("session")
        if session_arg is not None and not isinstance(run_input, _FakeState):
            # Schedule the SDK-side persistence to happen before the
            # bridge consumes the stream so ordering matches production.
            session_arg.history.append(
                {
                    "type": "function_call",
                    "id": f"fc_{call_id}",
                    "call_id": call_id,
                    "name": tool_name,
                    "arguments": "{}",
                }
            )
        return result

    # Service whose request_tool_approval hangs forever (the user never
    # answers; cancellation arrives while we're awaiting this).
    service = Mock()
    approval_awaited = asyncio.Event()

    async def _hang(**kwargs):
        approval_awaited.set()
        await asyncio.Event().wait()  # never completes

    service.request_tool_approval = _hang
    service.request_client_function = AsyncMock()
    service.invoke_runtime_hooks = AsyncMock(return_value=None)
    service.emit_runtime_hook = AsyncMock()

    session = Session("test_project", "test_agent")
    session.id = "sess-cancel"
    session.history = []
    session.channels = []
    session.pending_events = []
    session.pending_tool_approvals = {}

    rpc_channel = Mock()
    rpc_channel.async_call = AsyncMock()

    with (
        patch("agents.Runner.run_streamed", side_effect=fake_run_streamed),
        patch.object(bridge_module, "RunState", _FakeState),
    ):
        run_task = asyncio.create_task(
            stream_agent(
                _DummyAgent(),
                "Tail the bg job log",
                session,
                rpc_channel,
                service=service,
                context={"workspace_root": "/tmp", "session_id": "sess-cancel"},
            )
        )
        # Let the stream loop run far enough to:
        #   1) emit the tool_called event (populating pending_tool_calls)
        #   2) reach the interruption-handling block
        #   3) start awaiting service.request_tool_approval
        await asyncio.wait_for(approval_awaited.wait(), timeout=2.0)

        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run_task

    # The bridge's finally block must have appended a synthetic
    # function_call_output paired with the orphan function_call, so the
    # NEXT turn's history has no unresolved call_id.
    call_ids_with_output = {
        h.get("call_id")
        for h in session.history
        if isinstance(h, dict)
        and isinstance(h.get("type"), str)
        and h["type"].endswith("_call_output")
        and h.get("call_id")
    }
    call_ids_called = {
        h.get("call_id")
        for h in session.history
        if isinstance(h, dict)
        and isinstance(h.get("type"), str)
        and h["type"].endswith("_call")
        and not h["type"].endswith("_call_output")
        and h.get("call_id")
    }

    orphaned = call_ids_called - call_ids_with_output
    assert not orphaned, (
        f"function_call(s) without matching function_call_output remain in "
        f"session.history after cancellation: {orphaned!r}. "
        f"OpenAI Responses rejects this on the next turn with "
        f"'No tool output found for function call ...'. "
        f"Full history: {session.history!r}"
    )
