"""Voice-turn behavior of RealtimeSession: transcript accumulation + flush,
persistence into the paired GUI session, canonical recording, the
conversation-context tail, and tool-approval routing."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from agents.tool import FunctionTool

from omniagents.core.agents.approval import attach_tool_approval_predicates
from omniagents.core.agents.specs import AgentSpec, RealtimeSettings, SafeAgentOptions
from omniagents.rpc.realtime_events import (
    RealtimeTranscriptDeltaEvent,
    RealtimeTranscriptEvent,
    RealtimeTurnEndedEvent,
)
from omniagents.rpc.realtime_session import RealtimeSession


@pytest.fixture
def realtime_spec():
    return AgentSpec(
        name="VoiceAgent",
        welcome_text="Welcome",
        get_agent_instructions=lambda: "Test instructions",
        build_agent=None,
        model_name="gpt-realtime",
        realtime_mode=True,
        realtime_settings=RealtimeSettings(model_name="gpt-realtime"),
        project_slug="test_project",
        agent_slug="test_agent",
    )


@pytest.fixture
def settings_with_key():
    return {"openai_api_key": "sk-test-key"}


def make_paired_session(history=None):
    sess = Mock(name="gui_session")
    sess.id = "sess-1"
    sess.history = list(history or [])
    sess.append_message = Mock()
    return sess


def raw(data_type, **fields):
    return SimpleNamespace(
        type="raw_model_event", data=SimpleNamespace(type=data_type, **fields)
    )


async def start_streaming_session(spec, settings, events, **session_kwargs):
    """Boot a RealtimeSession over a mocked SDK session yielding *events*."""

    async def gen():
        for e in events:
            yield e

    mock_sdk = AsyncMock()
    mock_sdk.__aiter__ = lambda self: gen()
    mock_sdk.__aenter__ = AsyncMock(return_value=mock_sdk)

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True) as MockRunner,
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        MockRunner.return_value.run = AsyncMock(return_value=mock_sdk)
        session = RealtimeSession(spec, settings=settings, **session_kwargs)
        await session.start()
        await session.connect()
    return session, mock_sdk


# ---------------------------------------------------------------------------
# Transcript accumulation + flush
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assistant_deltas_flush_as_one_final_at_turn_end(
    realtime_spec, settings_with_key
):
    events = [
        raw("transcript_delta", item_id="item_a", delta="Hello ", response_id="r1"),
        raw("transcript_delta", item_id="item_a", delta="world", response_id="r1"),
        raw("turn_ended"),
    ]
    session, _ = await start_streaming_session(
        realtime_spec, settings_with_key, events
    )

    out = [e async for e in session.stream_events()]

    assert [type(e) for e in out] == [
        RealtimeTranscriptDeltaEvent,
        RealtimeTranscriptDeltaEvent,
        RealtimeTranscriptEvent,
        RealtimeTurnEndedEvent,
    ]
    final = out[2]
    assert final.role == "assistant"
    assert final.item_id == "item_a"
    assert final.transcript == "Hello world"
    # Accumulator drained: a second turn starts fresh.
    assert session._assistant_transcripts == {}


@pytest.mark.asyncio
async def test_flush_keeps_items_separate_by_item_id(
    realtime_spec, settings_with_key
):
    events = [
        raw("transcript_delta", item_id="item_a", delta="First", response_id="r1"),
        raw("transcript_delta", item_id="item_b", delta="Second", response_id="r1"),
        raw("turn_ended"),
    ]
    session, _ = await start_streaming_session(
        realtime_spec, settings_with_key, events
    )

    finals = [
        e
        async for e in session.stream_events()
        if isinstance(e, RealtimeTranscriptEvent)
    ]

    assert [(f.item_id, f.transcript) for f in finals] == [
        ("item_a", "First"),
        ("item_b", "Second"),
    ]


# ---------------------------------------------------------------------------
# Persistence into the paired session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_transcript_persists_to_paired_session(
    realtime_spec, settings_with_key
):
    paired = make_paired_session()
    events = [
        raw(
            "input_audio_transcription_completed",
            item_id="item_u",
            transcript="What time is it?",
        ),
    ]
    session, _ = await start_streaming_session(
        realtime_spec, settings_with_key, events, paired_session=paired
    )

    with patch(
        "omniagents.core.conversation.recorder.ensure_conversation_thread",
        return_value=None,
    ):
        out = [e async for e in session.stream_events()]

    assert len(out) == 1
    paired.append_message.assert_called_once_with(
        {"role": "user", "content": "What time is it?"}
    )


@pytest.mark.asyncio
async def test_assistant_flush_persists_output_parts_form(
    realtime_spec, settings_with_key
):
    paired = make_paired_session()
    events = [
        raw("transcript_delta", item_id="item_a", delta="Sure thing", response_id="r"),
        raw("turn_ended"),
    ]
    session, _ = await start_streaming_session(
        realtime_spec, settings_with_key, events, paired_session=paired
    )

    with patch(
        "omniagents.core.conversation.recorder.ensure_conversation_thread",
        return_value=None,
    ):
        [e async for e in session.stream_events()]

    paired.append_message.assert_called_once_with(
        {
            "role": "assistant",
            "content": [
                {"type": "output_text", "text": "Sure thing", "annotations": []}
            ],
            "type": "message",
        }
    )


@pytest.mark.asyncio
async def test_empty_transcripts_are_not_persisted(realtime_spec, settings_with_key):
    paired = make_paired_session()
    events = [
        raw("input_audio_transcription_completed", item_id="item_u", transcript="  "),
        raw("transcript_delta", item_id="item_a", delta=" ", response_id="r"),
        raw("turn_ended"),
    ]
    session, _ = await start_streaming_session(
        realtime_spec, settings_with_key, events, paired_session=paired
    )

    [e async for e in session.stream_events()]

    paired.append_message.assert_not_called()


@pytest.mark.asyncio
async def test_no_paired_session_falls_back_to_history_db(
    realtime_spec, settings_with_key
):
    events = [
        raw(
            "input_audio_transcription_completed",
            item_id="item_u",
            transcript="Hello there",
        ),
    ]
    session, _ = await start_streaming_session(
        realtime_spec, settings_with_key, events
    )

    with patch(
        "omniagents.rpc.realtime_session.history_db.append_message"
    ) as mock_append:
        [e async for e in session.stream_events()]

    mock_append.assert_called_once()
    args, kwargs = mock_append.call_args
    assert args[0] == session.session_id
    assert args[1] == {"role": "user", "content": "Hello there"}
    assert kwargs["project_slug"] == "test_project"


@pytest.mark.asyncio
async def test_stop_flushes_pending_assistant_transcript(
    realtime_spec, settings_with_key
):
    paired = make_paired_session()
    session, _ = await start_streaming_session(
        realtime_spec, settings_with_key, [], paired_session=paired
    )
    session._assistant_transcripts["item_x"] = "cut off mid-"

    with patch(
        "omniagents.core.conversation.recorder.ensure_conversation_thread",
        return_value=None,
    ):
        await session.stop()

    paired.append_message.assert_called_once()
    assert session._assistant_transcripts == {}


# ---------------------------------------------------------------------------
# Canonical recording turn model
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_canonical_turn_opens_on_user_and_closes_on_assistant(
    realtime_spec, settings_with_key
):
    paired = make_paired_session()
    recorder = Mock()
    events = [
        raw(
            "input_audio_transcription_completed",
            item_id="item_u",
            transcript="Question?",
        ),
        raw("transcript_delta", item_id="item_a", delta="Answer.", response_id="r"),
        raw("turn_ended"),
    ]
    session, _ = await start_streaming_session(
        realtime_spec, settings_with_key, events, paired_session=paired
    )

    with patch(
        "omniagents.core.conversation.recorder.ensure_conversation_thread",
        return_value=recorder,
    ):
        [e async for e in session.stream_events()]

    methods = [c.args[1] for c in recorder.record.call_args_list]
    assert methods == ["run_started", "message_output", "run_end"]
    run_started = recorder.record.call_args_list[0].args[2]
    assert run_started["prompt"] == "Question?"
    assert run_started["prompt_role"] == "user"
    # One voice exchange = one turn id throughout.
    turn_ids = {c.args[2]["run_id"] for c in recorder.record.call_args_list}
    assert len(turn_ids) == 1
    # Turn closed: the next exchange opens a new one.
    assert session._voice_turn_id is None


@pytest.mark.asyncio
async def test_canonical_assistant_without_prompt_opens_promptless_turn(
    realtime_spec, settings_with_key
):
    paired = make_paired_session()
    recorder = Mock()
    events = [
        raw("transcript_delta", item_id="item_a", delta="Unprompted", response_id="r"),
        raw("turn_ended"),
    ]
    session, _ = await start_streaming_session(
        realtime_spec, settings_with_key, events, paired_session=paired
    )

    with patch(
        "omniagents.core.conversation.recorder.ensure_conversation_thread",
        return_value=recorder,
    ):
        [e async for e in session.stream_events()]

    run_started = recorder.record.call_args_list[0].args[2]
    assert "prompt" not in run_started


# ---------------------------------------------------------------------------
# Text → voice context tail
# ---------------------------------------------------------------------------


def test_history_tail_renders_both_roles_and_output_parts(realtime_spec):
    paired = make_paired_session(
        history=[
            {"role": "user", "content": "Fix the login bug"},
            {
                "role": "assistant",
                "type": "message",
                "content": [
                    {"type": "output_text", "text": "On it.", "annotations": []}
                ],
            },
            {"type": "function_call", "name": "bash", "arguments": "{}"},
        ]
    )
    session = RealtimeSession(realtime_spec, paired_session=paired)

    tail = session._render_history_tail()

    assert tail == "User: Fix the login bug\nAssistant: On it."


def test_history_tail_caps_items_and_chars(realtime_spec):
    history = [{"role": "user", "content": f"message {i}"} for i in range(50)]
    paired = make_paired_session(history=history)
    session = RealtimeSession(realtime_spec, paired_session=paired)

    tail = session._render_history_tail()
    lines = tail.split("\n")
    assert len(lines) == session._HISTORY_TAIL_ITEMS
    assert lines[-1] == "User: message 49"

    big = [{"role": "user", "content": "x" * 1000} for _ in range(20)]
    session_big = RealtimeSession(
        realtime_spec, paired_session=make_paired_session(history=big)
    )
    assert len(session_big._render_history_tail()) <= session_big._HISTORY_TAIL_CHARS


def test_history_tail_empty_without_history(realtime_spec):
    session = RealtimeSession(
        realtime_spec, paired_session=make_paired_session(history=[])
    )
    assert session._render_history_tail() == ""


@pytest.mark.asyncio
async def test_start_appends_conversation_tail_to_instructions(
    realtime_spec, settings_with_key
):
    paired = make_paired_session(history=[{"role": "user", "content": "Earlier text"}])

    with (
        patch("agents.realtime.RealtimeAgent", create=True) as MockAgent,
        patch("agents.realtime.RealtimeRunner", create=True),
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        session = RealtimeSession(
            realtime_spec, settings=settings_with_key, paired_session=paired
        )
        await session.start()

    instructions = MockAgent.call_args[1]["instructions"]
    assert "Test instructions" in instructions
    assert "# Conversation so far" in instructions
    assert "User: Earlier text" in instructions


# ---------------------------------------------------------------------------
# Tool approval gating + routing
# ---------------------------------------------------------------------------


def make_function_tool(name):
    async def invoke(ctx, args):
        return "ok"

    return FunctionTool(
        name=name,
        description=f"{name} tool",
        params_json_schema={"type": "object", "properties": {}},
        on_invoke_tool=invoke,
    )


@pytest.mark.asyncio
async def test_attach_predicates_gates_unsafe_and_exempts_safe_and_bridged():
    session = SimpleNamespace(always_allow_tools=set())
    safe = make_function_tool("read_file")
    unsafe = make_function_tool("bash")
    bridged = make_function_tool("client_tool")

    attach_tool_approval_predicates(
        [safe, unsafe, bridged],
        session=session,
        safe_tool_names=["read_file"],
        exempt=[bridged],
    )

    assert callable(safe.needs_approval)
    assert callable(unsafe.needs_approval)
    assert bridged.needs_approval is False

    assert await safe.needs_approval(None, {}, "c1") is False
    assert await unsafe.needs_approval(None, {}, "c2") is True

    session.always_allow_tools.add("bash")
    assert await unsafe.needs_approval(None, {}, "c3") is False


@pytest.mark.asyncio
async def test_start_attaches_predicates_when_approvals_configured(
    realtime_spec, settings_with_key
):
    tool = make_function_tool("bash")
    bridged = make_function_tool("client_tool")
    realtime_spec.available_tools = [tool]
    realtime_spec.use_safe_agent = True
    realtime_spec.safe_agent_options = SafeAgentOptions(
        skip_approvals=False, safe_tool_names=["read_file"]
    )
    paired = make_paired_session()

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True),
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        session = RealtimeSession(
            realtime_spec,
            settings=settings_with_key,
            agent_service=Mock(),
            paired_session=paired,
            extra_tools=[bridged],
        )
        await session.start()

    assert callable(tool.needs_approval)
    assert bridged.needs_approval is False


@pytest.mark.asyncio
async def test_start_leaves_tools_ungated_without_paired_session(
    realtime_spec, settings_with_key
):
    tool = make_function_tool("bash")
    realtime_spec.available_tools = [tool]
    realtime_spec.use_safe_agent = True
    realtime_spec.safe_agent_options = SafeAgentOptions(skip_approvals=False)

    with (
        patch("agents.realtime.RealtimeAgent", create=True),
        patch("agents.realtime.RealtimeRunner", create=True),
        patch("omniagents.rpc.realtime_session.history_db.register_session"),
    ):
        session = RealtimeSession(realtime_spec, settings=settings_with_key)
        await session.start()

    assert tool.needs_approval is False


@pytest.mark.asyncio
async def test_tool_approval_required_routes_approve(
    realtime_spec, settings_with_key
):
    paired = make_paired_session()
    agent_service = Mock()
    agent_service.request_tool_approval = AsyncMock(
        return_value={
            "decision": "approve",
            "always_approve": True,
            "rejection_message": None,
        }
    )
    approval_event = SimpleNamespace(
        type="tool_approval_required",
        call_id="call_1",
        tool=SimpleNamespace(name="bash"),
        arguments='{"cmd": "ls"}',
    )
    session, mock_sdk = await start_streaming_session(
        realtime_spec,
        settings_with_key,
        [approval_event],
        agent_service=agent_service,
        paired_session=paired,
    )
    mock_sdk.approve_tool_call = AsyncMock()
    mock_sdk.reject_tool_call = AsyncMock()

    out = [e async for e in session.stream_events()]
    if session._approval_tasks:
        await asyncio.gather(*session._approval_tasks)

    # The approval pause is handled internally, never forwarded to the client.
    assert out == []
    agent_service.request_tool_approval.assert_awaited_once()
    kwargs = agent_service.request_tool_approval.await_args.kwargs
    assert kwargs["session"] is paired
    assert kwargs["call_id"] == "call_1"
    assert kwargs["tool_name"] == "bash"
    assert kwargs["run_id"] == session._run_id
    mock_sdk.approve_tool_call.assert_awaited_once_with("call_1", always=True)
    mock_sdk.reject_tool_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_tool_approval_required_routes_reject(
    realtime_spec, settings_with_key
):
    paired = make_paired_session()
    agent_service = Mock()
    agent_service.request_tool_approval = AsyncMock(
        return_value={
            "decision": "reject",
            "always_approve": False,
            "rejection_message": "not now",
        }
    )
    approval_event = SimpleNamespace(
        type="tool_approval_required",
        call_id="call_2",
        tool=SimpleNamespace(name="bash"),
        arguments="{}",
    )
    session, mock_sdk = await start_streaming_session(
        realtime_spec,
        settings_with_key,
        [approval_event],
        agent_service=agent_service,
        paired_session=paired,
    )
    mock_sdk.approve_tool_call = AsyncMock()
    mock_sdk.reject_tool_call = AsyncMock()

    [e async for e in session.stream_events()]
    if session._approval_tasks:
        await asyncio.gather(*session._approval_tasks)

    mock_sdk.reject_tool_call.assert_awaited_once_with(
        "call_2", rejection_message="not now"
    )
    mock_sdk.approve_tool_call.assert_not_awaited()


# ---------------------------------------------------------------------------
# MCP tools are resolved lazily by the SDK, after the static-list pass.
# ---------------------------------------------------------------------------


class _FakeMcpAgent:
    """Stands in for RealtimeAgent: static tools plus lazily-fetched MCP ones."""

    def __init__(self, static, mcp):
        self.tools = list(static)
        self._mcp = list(mcp)
        self.handoffs = []
        self.mcp_servers = []

    async def get_all_tools(self, run_context):
        return list(self.tools) + list(self._mcp)


@pytest.mark.asyncio
async def test_lazy_mcp_tools_are_gated():
    """An MCP tool the agent resolves at run time must still need approval.

    Regression: the realtime path attached predicates to the static tool
    list only, so tools from user-configured MCP servers ran ungated in a
    voice session while the same server was gated in text.
    """
    from omniagents.core.agents.approval import attach_lazy_tool_approval

    session = SimpleNamespace(always_allow_tools=set(), always_allow_patterns=[])
    static = make_function_tool("read_file")
    mcp_tool = make_function_tool("mcp_databricks__run_sql")
    agent = _FakeMcpAgent([static], [mcp_tool])

    attach_lazy_tool_approval(agent, session=session, safe_tool_names=["read_file"])

    # Untouched until the SDK actually resolves tools for a run.
    assert mcp_tool.needs_approval is False

    resolved = await agent.get_all_tools(None)
    assert {t.name for t in resolved} == {"read_file", "mcp_databricks__run_sql"}

    assert await mcp_tool.needs_approval(None, {}, "c1") is True
    # The safe list still applies to whatever comes back through this path.
    assert await static.needs_approval(None, {}, "c2") is False


@pytest.mark.asyncio
async def test_lazy_gating_reaches_handoff_targets():
    """Handoff targets carry their own MCP servers, so they need it too."""
    from omniagents.core.agents.approval import attach_lazy_tool_approval

    session = SimpleNamespace(always_allow_tools=set(), always_allow_patterns=[])
    nested_tool = make_function_tool("mcp_other__write")
    target = _FakeMcpAgent([], [nested_tool])
    root = _FakeMcpAgent([], [])
    root.handoffs = [target]

    attach_lazy_tool_approval(root, session=session)

    await target.get_all_tools(None)
    assert await nested_tool.needs_approval(None, {}, "c1") is True
