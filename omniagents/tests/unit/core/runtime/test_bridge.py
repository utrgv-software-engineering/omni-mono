import asyncio
import types

import pytest

import omniagents.core.runtime.bridge as bridge
from omniagents.core.tools import RichToolOutput


class DummyChannel:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    async def async_call(self, method, args):
        if self.fail:
            raise RuntimeError("channel closed")
        self.calls.append((method, args))


class DummySession:
    def __init__(self, channels=None, history=None, session_id="session-1"):
        self.channels = list(channels or [])
        self.history = list(history or [])
        self.pending_events = []
        self.id = session_id

    def append_message(self, message):
        self.history.append(message)


@pytest.mark.asyncio
async def test_safe_notify_handles_disconnect():
    channel = DummyChannel()
    ok = await bridge._safe_notify(channel, "ping", {"value": 1})
    assert ok is True
    failing = DummyChannel(fail=True)
    ok2 = await bridge._safe_notify(failing, "ping", {"value": 1})
    assert ok2 is False


@pytest.mark.asyncio
async def test_broadcast_filters_dead_channels():
    live = DummyChannel()
    dead = DummyChannel(fail=True)
    session = DummySession(channels=[live, dead])
    delivered = await bridge._broadcast(session, "event", {"value": 2})
    assert delivered is True
    assert live.calls == [("event", {"value": 2})]
    assert dead not in session.channels


@pytest.mark.asyncio
async def test_broadcast_returns_false_without_channels():
    session = DummySession(channels=[])
    delivered = await bridge._broadcast(session, "event", {"value": 3})
    assert delivered is False


@pytest.mark.asyncio
async def test_broadcast_handles_remove_failure():
    class FussyList(list):
        def remove(self, value):
            raise ValueError("gone")

    dead = DummyChannel(fail=True)
    session = DummySession()
    session.channels = FussyList([dead])

    delivered = await bridge._broadcast(session, "event", {"value": 4})
    assert delivered is False


def test_stream_event_payload_variants(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "ItemHelpers",
        types.SimpleNamespace(text_message_output=lambda item: item.text),
    )
    run_id = "run-1"
    session_id = "session-1"
    call_item = types.SimpleNamespace(
        type="tool_call_item",
        to_input_item=lambda: {"name": "alpha", "arguments": "{}", "call_id": "c1"},
    )
    method, payload = bridge._stream_event_payload(
        call_item, run_id, session_id=session_id
    )
    assert method == "tool_called"
    assert payload["call_id"] == "c1"
    assert payload["session_id"] == session_id
    output_item = types.SimpleNamespace(
        type="tool_call_output_item",
        name="alpha",
        output=RichToolOutput("done", ui_metadata={"hint": "ok"}),
        to_input_item=lambda: {"call_id": "c1"},
    )
    method2, payload2 = bridge._stream_event_payload(
        output_item, run_id, session_id=session_id
    )
    assert method2 == "tool_result"
    assert payload2["metadata"] == {"hint": "ok"}
    assert payload2["session_id"] == session_id
    message_item = types.SimpleNamespace(
        type="message_output_item",
        text="hello",
        to_input_item=lambda: {"role": "assistant", "content": "hello"},
    )
    method3, payload3 = bridge._stream_event_payload(
        message_item, run_id, session_id=session_id
    )
    assert method3 == "message_output"
    assert payload3["content"] == "hello"
    assert payload3["session_id"] == session_id
    unknown_item = types.SimpleNamespace(type="other")
    method4, payload4 = bridge._stream_event_payload(
        unknown_item, run_id, session_id=session_id
    )
    assert method4 is None
    assert payload4 is None


def test_stream_event_payload_handles_input_error(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "ItemHelpers",
        types.SimpleNamespace(text_message_output=lambda item: ""),
    )

    class BadItem:
        def __init__(self):
            self.type = "tool_call_output_item"
            self.name = "alpha"
            self.output = "plain"

        def to_input_item(self):
            raise RuntimeError("bad item")

    method, payload = bridge._stream_event_payload(
        BadItem(), "run-x", session_id="session-x"
    )
    assert method == "tool_result"
    assert payload["output"] == "plain"
    assert "call_id" not in payload


@pytest.mark.asyncio
async def test_stream_agent_emits_usage_and_running_summary(monkeypatch):
    monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))
    monkeypatch.setattr(
        bridge,
        "ItemHelpers",
        types.SimpleNamespace(text_message_output=lambda item: item.text),
    )

    class FakeResponseCompletedEvent:
        def __init__(self, response):
            self.response = response

    monkeypatch.setattr(bridge, "ResponseCompletedEvent", FakeResponseCompletedEvent)

    configs = []

    class FakeRunConfig:
        def __init__(self, **kwargs):
            configs.append(kwargs)

    monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
    monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

    run_id_holder = {}

    class FakeRunResult:
        def __init__(self, events):
            self._events = events

        async def stream_events(self):
            for event in self._events:
                yield event

    events = []

    class FakeUsageDetails:
        def __init__(self, cached_tokens=0, reasoning_tokens=0):
            self.cached_tokens = cached_tokens
            self.reasoning_tokens = reasoning_tokens

    class FakeUsage:
        def __init__(self):
            self.input_tokens = 10
            self.output_tokens = 4
            self.total_tokens = 14
            self.input_tokens_details = FakeUsageDetails(cached_tokens=2)
            self.output_tokens_details = FakeUsageDetails(reasoning_tokens=1)

    class FakeResponse:
        def __init__(self):
            self.usage = FakeUsage()
            self.model = "model-x"
            self.id = "resp-1"

    events.append(
        types.SimpleNamespace(
            type="raw_response_event",
            data=FakeResponseCompletedEvent(FakeResponse()),
        )
    )

    tool_call_item = types.SimpleNamespace(
        type="tool_call_item",
        to_input_item=lambda: {
            "name": "alpha",
            "arguments": "{}",
            "call_id": "call-1",
        },
    )

    tool_result_item = types.SimpleNamespace(
        type="tool_call_output_item",
        name="alpha",
        output=RichToolOutput("ok", ui_metadata={"detail": "ready"}),
        to_input_item=lambda: {"call_id": "call-1"},
    )

    message_item = types.SimpleNamespace(
        type="message_output_item",
        text="answer",
        to_input_item=lambda: {"role": "assistant", "content": "answer"},
    )

    events.append(
        types.SimpleNamespace(type="run_item_stream_event", item=tool_call_item)
    )
    events.append(
        types.SimpleNamespace(type="run_item_stream_event", item=tool_result_item)
    )
    events.append(
        types.SimpleNamespace(type="run_item_stream_event", item=message_item)
    )

    class FakeRunner:
        @classmethod
        def run_streamed(cls, *args, **kwargs):
            run_id_holder["args"] = args
            run_id_holder["kwargs"] = kwargs
            return FakeRunResult(events)

    monkeypatch.setattr("agents.Runner", FakeRunner)

    live = DummyChannel()
    dead = DummyChannel(fail=True)
    session = DummySession(channels=[live, dead], session_id="abc123")

    class DummyTool:
        def __init__(self):
            self.name = "alpha"
            self._running_summary = "Running"

    class DummyAgent:
        def __init__(self):
            self.name = "Agent"
            self.instructions = "inst"
            self.tools = [DummyTool()]
            self.model = "model-x"
            self.model_settings = types.SimpleNamespace(
                max_tokens=50, truncation="auto"
            )

    agent = DummyAgent()

    result = await bridge.stream_agent(
        agent,
        "prompt",
        session,
        rpc_channel=None,
        max_turns=5,
        context={"a": 1},
        workflow_name="workflow",
    )

    assert configs[0]["group_id"] == "session_abc123"
    assert configs[0]["workflow_name"] == "workflow"
    assert configs[0]["call_model_input_filter"] is bridge._strip_phase_from_model_input
    assert live.calls[0][0] == "token"
    run_id = live.calls[0][1]["run_id"]
    assert run_id
    # Running summary rides the tool_called payload — no ui.set_status
    # side-channel broadcasts for tool statuses anymore.
    assert not any(call[0] == "client_request" for call in live.calls)
    assert live.calls[1][0] == "tool_called"
    assert live.calls[1][1]["metadata"] == {"summary": "Running"}
    assert live.calls[2][0] == "tool_result"
    assert live.calls[3][0] == "message_output"
    assert result["usage"]["input_tokens"] == 10
    assert result["model"] == "model-x"
    assert result["max_output_tokens"] == 50
    assert dead not in session.channels
    assert session.pending_events == []
    assert session.history[0] == {"role": "user", "content": "prompt"}
    assert session.history[-1] == {"role": "assistant", "content": "answer"}
    assert run_id_holder["args"][0] is agent


@pytest.mark.asyncio
async def test_stream_agent_handles_cancelled_tool_calls(monkeypatch):
    monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))
    monkeypatch.setattr(
        bridge,
        "ItemHelpers",
        types.SimpleNamespace(
            text_message_output=lambda item: item.text if hasattr(item, "text") else ""
        ),
    )

    class FakeRunConfig:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
    monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

    broadcasts = []

    async def fake_broadcast(session_arg, method, payload):
        broadcasts.append((method, payload))
        return False

    monkeypatch.setattr(bridge, "_broadcast", fake_broadcast)

    class FakeRunResult:
        def __init__(self, events):
            self._events = events

        async def stream_events(self):
            for event in self._events:
                yield event

    tool_call_item = types.SimpleNamespace(
        type="tool_call_item",
        to_input_item=lambda: {
            "name": "alpha",
            "arguments": "{}",
            "call_id": "call-9",
        },
    )

    events = [types.SimpleNamespace(type="run_item_stream_event", item=tool_call_item)]

    class FakeRunner:
        @classmethod
        def run_streamed(cls, *args, **kwargs):
            return FakeRunResult(events)

    monkeypatch.setattr("agents.Runner", FakeRunner)

    session = DummySession(channels=[], session_id="xyz")

    class DummyAgent:
        def __init__(self):
            self.name = "Agent"
            self.instructions = "inst"
            self.tools = []
            self.model = None
            self.model_settings = None

    agent = DummyAgent()

    result = await bridge.stream_agent(agent, "ask", session, rpc_channel=None)

    assert broadcasts[0][0] == "tool_called"
    assert broadcasts[-1][0] == "tool_result"
    assert broadcasts[-1][1]["output"] == "[Cancelled by user]"
    assert session.history[-1]["output"] == "[Cancelled by user]"
    assert result["usage"]["total_tokens"] == 0


@pytest.mark.asyncio
async def test_stream_agent_without_user_message(monkeypatch):
    monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))

    configs = []

    class FakeRunConfig:
        def __init__(self, **kwargs):
            configs.append(kwargs)

    monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
    monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

    captured_inputs = []

    class FakeRunResult:
        async def stream_events(self):
            if False:
                yield None

    class FakeRunner:
        @classmethod
        def run_streamed(cls, agent_arg, agent_input, **kwargs):
            captured_inputs.append(agent_input)
            return FakeRunResult()

    monkeypatch.setattr("agents.Runner", FakeRunner)

    class TrackingSession(DummySession):
        def __init__(self):
            super().__init__(
                channels=[],
                history=[{"role": "system", "content": "ready"}],
                session_id="sess77",
            )
            self.append_count = 0

        def append_message(self, message):
            self.append_count += 1
            super().append_message(message)

    session = TrackingSession()

    class DummyAgent:
        def __init__(self):
            self.name = "Agent"
            self.instructions = "inst"
            self.tools = []
            self.model = "model-y"
            self.model_settings = types.SimpleNamespace(
                max_tokens=None, truncation=None
            )

    agent = DummyAgent()

    result = await bridge.stream_agent(
        agent,
        "prompt",
        session,
        rpc_channel=None,
        include_user_message=False,
        max_input_tokens=256,
    )

    # The bridge no longer feeds session.history through the runner —
    # the SDK pulls it from ``Session.get_items()`` itself.  When
    # ``include_user_message=False`` the bridge sends an empty new-turn
    # input list, so existing history stays untouched and ``append_message``
    # is never called by the bridge for this run.
    assert session.append_count == 0
    assert session.history == [{"role": "system", "content": "ready"}]
    assert captured_inputs[0] == []
    assert configs[0]["workflow_name"] == "omniagents_conversation"
    assert result["max_input_tokens"] == 256


@pytest.mark.asyncio
async def test_stream_agent_context_error_hook_fires_for_resume_from_history(
    monkeypatch,
):
    """The resident-wakeup shape: assistant-role runs resume from history
    (``include_user_message=False``). A context-length error on such a run
    must reach the runtime "error" hook (compaction + retry) — the old gate
    skipped it, so a day session that outgrew the window while the agent
    was parked bricked on every wakeup at turn 0."""
    monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))

    class FakeRunConfig:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
    monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

    attempts = []

    class FailingRunResult:
        async def stream_events(self):
            raise Exception(
                "Your input exceeds the context window of this model. "
                "Please adjust your input and try again."
            )
            yield None  # pragma: no cover — makes this an async generator

    class OkRunResult:
        async def stream_events(self):
            if False:
                yield None

    class FakeRunner:
        @classmethod
        def run_streamed(cls, agent_arg, agent_input, **kwargs):
            attempts.append(agent_input)
            return FailingRunResult() if len(attempts) == 1 else OkRunResult()

    monkeypatch.setattr("agents.Runner", FakeRunner)

    hook_calls = []

    class FakeService:
        async def invoke_runtime_hooks(self, name, *, session, run_id, params):
            hook_calls.append((name, params))
            # The real hook compacts the session here, then asks for a retry.
            return {"retry": True}

    session = DummySession(
        channels=[],
        history=[{"role": "assistant", "content": "wakeup ping", "type": "message"}],
        session_id="resident1",
    )

    class DummyAgent:
        def __init__(self):
            self.name = "Agent"
            self.instructions = "inst"
            self.tools = []
            self.model = "model-y"
            self.model_settings = types.SimpleNamespace(
                max_tokens=None, truncation=None
            )

    result = await bridge.stream_agent(
        DummyAgent(),
        "wakeup ping",
        session,
        rpc_channel=None,
        service=FakeService(),
        include_user_message=False,
    )

    assert len(hook_calls) == 1
    assert hook_calls[0][0] == "error"
    assert hook_calls[0][1]["is_context_length_exceeded"] is True
    # The hook-driven retry re-entered the run and it completed.
    assert len(attempts) == 2
    assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_stream_agent_reraises_cancelled_error(monkeypatch):
    monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))

    class FakeRunConfig:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
    monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

    class FakeRunner:
        @classmethod
        def run_streamed(cls, *args, **kwargs):
            raise asyncio.CancelledError()

    monkeypatch.setattr("agents.Runner", FakeRunner)

    session = DummySession(channels=[], session_id="cancel")

    class DummyAgent:
        def __init__(self):
            self.name = "Agent"
            self.instructions = "inst"
            self.tools = []
            self.model = None
            self.model_settings = None

    agent = DummyAgent()

    with pytest.raises(asyncio.CancelledError):
        await bridge.stream_agent(agent, "prompt", session, rpc_channel=None)


@pytest.mark.asyncio
async def test_stream_agent_skips_unknown_event_type(monkeypatch):
    monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))

    class FakeRunConfig:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
    monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

    class FakeRunResult:
        async def stream_events(self):
            yield types.SimpleNamespace(type="log_event", data={})

    class FakeRunner:
        @classmethod
        def run_streamed(cls, *args, **kwargs):
            return FakeRunResult()

    monkeypatch.setattr("agents.Runner", FakeRunner)

    session = DummySession(
        channels=[], history=[{"role": "assistant", "content": "hi"}], session_id="skip"
    )

    class DummyAgent:
        def __init__(self):
            self.name = "Agent"
            self.instructions = "inst"
            self.tools = []
            self.model = None
            self.model_settings = None

    agent = DummyAgent()

    result = await bridge.stream_agent(agent, "prompt", session, rpc_channel=None)

    assert session.history[0] == {"role": "assistant", "content": "hi"}
    assert session.history[1] == {"role": "user", "content": "prompt"}
    assert result["usage"]["total_tokens"] == 0


@pytest.mark.asyncio
async def test_stream_agent_ignores_tool_lookup_errors(monkeypatch):
    monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))
    monkeypatch.setattr(
        bridge,
        "ItemHelpers",
        types.SimpleNamespace(text_message_output=lambda item: ""),
    )

    class FakeRunConfig:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
    monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

    tool_call_item = types.SimpleNamespace(
        type="tool_call_item",
        to_input_item=lambda: {
            "name": "alpha",
            "arguments": "{}",
            "call_id": "call-x",
        },
    )

    class FakeRunResult:
        def __init__(self, events):
            self._events = events

        async def stream_events(self):
            for event in self._events:
                yield event

    events = [types.SimpleNamespace(type="run_item_stream_event", item=tool_call_item)]

    class FakeRunner:
        @classmethod
        def run_streamed(cls, *args, **kwargs):
            return FakeRunResult(events)

    monkeypatch.setattr("agents.Runner", FakeRunner)

    class BadTools:
        def __iter__(self):
            raise RuntimeError("broken tools")

    live = DummyChannel()
    session = DummySession(channels=[live], session_id="tools")

    class DummyAgent:
        def __init__(self):
            self.name = "Agent"
            self.instructions = "inst"
            self.tools = BadTools()
            self.model = None
            self.model_settings = None

    agent = DummyAgent()

    result = await bridge.stream_agent(agent, "prompt", session, rpc_channel=None)

    assert live.calls[0][0] == "tool_called"
    assert result["usage"]["total_tokens"] == 0


@pytest.mark.asyncio
async def test_buffering_orders_message_before_tool_calls(monkeypatch):
    monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))
    monkeypatch.setattr(
        bridge,
        "ItemHelpers",
        types.SimpleNamespace(
            text_message_output=lambda item: getattr(item, "text", "")
        ),
    )

    class FakeRunConfig:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
    monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

    broadcasts = []

    async def capture_broadcast(session_arg, method, payload):
        broadcasts.append((method, payload))
        return True

    monkeypatch.setattr(bridge, "_broadcast", capture_broadcast)

    reasoning_item = types.SimpleNamespace(
        type="reasoning_item",
        to_input_item=lambda: {
            "type": "reasoning",
            "id": "rs_buf",
            "summary": [{"text": "planning", "type": "summary_text"}],
            "status": "completed",
        },
    )

    tool_call_item = types.SimpleNamespace(
        type="tool_call_item",
        to_input_item=lambda: {
            "name": "alpha",
            "arguments": "{}",
            "call_id": "cbuf",
        },
    )

    message_item = types.SimpleNamespace(
        type="message_output_item",
        text="preamble",
        to_input_item=lambda: {"role": "assistant", "content": "preamble"},
    )

    events = [
        types.SimpleNamespace(type="run_item_stream_event", item=reasoning_item),
        types.SimpleNamespace(type="run_item_stream_event", item=tool_call_item),
        types.SimpleNamespace(type="run_item_stream_event", item=message_item),
    ]

    class FakeRunResult:
        def __init__(self, evts):
            self._events = evts

        async def stream_events(self):
            for e in self._events:
                yield e

    class FakeRunner:
        @classmethod
        def run_streamed(cls, *args, **kwargs):
            return FakeRunResult(events)

    monkeypatch.setattr("agents.Runner", FakeRunner)

    session = DummySession(channels=[], session_id="buf-1")

    class DummyTool:
        def __init__(self):
            self.name = "alpha"
            self._running_summary = "Running"

    class DummyAgent:
        def __init__(self):
            self.name = "Agent"
            self.instructions = "inst"
            self.tools = [DummyTool()]
            self.model = None
            self.model_settings = None

    agent = DummyAgent()

    await bridge.stream_agent(agent, "ask", session, rpc_channel=None)

    assert broadcasts[0][0] == "tool_called"
    assert broadcasts[0][1]["metadata"] == {"summary": "Running"}
    assert any(m == "message_output" for m, _ in broadcasts)


@pytest.mark.asyncio
async def test_buffering_orders_message_before_tool_result(monkeypatch):
    monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))
    monkeypatch.setattr(
        bridge,
        "ItemHelpers",
        types.SimpleNamespace(
            text_message_output=lambda item: getattr(item, "text", "")
        ),
    )

    class FakeRunConfig:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
    monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

    calls = []

    async def capture_broadcast(session_arg, method, payload):
        calls.append((method, payload))
        return True

    monkeypatch.setattr(bridge, "_broadcast", capture_broadcast)

    reasoning_item = types.SimpleNamespace(
        type="reasoning_item",
        to_input_item=lambda: {
            "type": "reasoning",
            "id": "rs_buf2",
            "summary": [{"text": "thinking", "type": "summary_text"}],
            "status": "completed",
        },
    )

    tool_call_item = types.SimpleNamespace(
        type="tool_call_item",
        to_input_item=lambda: {
            "name": "alpha",
            "arguments": "{}",
            "call_id": "cbuf2",
        },
    )

    tool_result_item = types.SimpleNamespace(
        type="tool_call_output_item",
        name="alpha",
        output="done",
        to_input_item=lambda: {"call_id": "cbuf2"},
    )

    message_item = types.SimpleNamespace(
        type="message_output_item",
        text="preamble2",
        to_input_item=lambda: {"role": "assistant", "content": "preamble2"},
    )

    events = [
        types.SimpleNamespace(type="run_item_stream_event", item=reasoning_item),
        types.SimpleNamespace(type="run_item_stream_event", item=tool_call_item),
        types.SimpleNamespace(type="run_item_stream_event", item=tool_result_item),
        types.SimpleNamespace(type="run_item_stream_event", item=message_item),
    ]

    class FakeRunResult:
        def __init__(self, evts):
            self._events = evts

        async def stream_events(self):
            for e in self._events:
                yield e

    class FakeRunner:
        @classmethod
        def run_streamed(cls, *args, **kwargs):
            return FakeRunResult(events)

    monkeypatch.setattr("agents.Runner", FakeRunner)

    session = DummySession(channels=[], session_id="buf-2")

    class DummyAgent:
        def __init__(self):
            self.name = "Agent"
            self.instructions = "inst"
            self.tools = []
            self.model = None
            self.model_settings = None

    agent = DummyAgent()

    await bridge.stream_agent(agent, "ask", session, rpc_channel=None)

    assert calls[0][0] == "tool_called"
    assert calls[1][0] == "tool_result"
    assert calls[2][0] == "message_output"


@pytest.mark.asyncio
async def test_buffering_synth_preamble_on_cancel(monkeypatch):
    monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))
    monkeypatch.setattr(
        bridge,
        "ItemHelpers",
        types.SimpleNamespace(
            text_message_output=lambda item: getattr(item, "text", "")
        ),
    )

    class FakeRunConfig:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
    monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

    seen = []

    async def capture_broadcast(session_arg, method, payload):
        seen.append((method, payload))
        return True

    monkeypatch.setattr(bridge, "_broadcast", capture_broadcast)

    reasoning_item = types.SimpleNamespace(
        type="reasoning_item",
        to_input_item=lambda: {
            "type": "reasoning",
            "id": "rs_cancel",
            "summary": [{"text": "planning tools", "type": "summary_text"}],
            "status": "completed",
        },
    )

    tool_call_item = types.SimpleNamespace(
        type="tool_call_item",
        to_input_item=lambda: {
            "name": "alpha",
            "arguments": "{}",
            "call_id": "ccancel",
        },
    )

    events = [
        types.SimpleNamespace(type="run_item_stream_event", item=reasoning_item),
        types.SimpleNamespace(type="run_item_stream_event", item=tool_call_item),
    ]

    class FakeRunResult:
        def __init__(self, evts):
            self._events = evts

        async def stream_events(self):
            for e in self._events:
                yield e
            raise asyncio.CancelledError()

    class FakeRunner:
        @classmethod
        def run_streamed(cls, *args, **kwargs):
            return FakeRunResult(events)

    monkeypatch.setattr("agents.Runner", FakeRunner)

    session = DummySession(channels=[], session_id="buf-3")

    class DummyAgent:
        def __init__(self):
            self.name = "Agent"
            self.instructions = "inst"
            self.tools = []
            self.model = None
            self.model_settings = None

    agent = DummyAgent()

    with pytest.raises(asyncio.CancelledError):
        await bridge.stream_agent(agent, "ask", session, rpc_channel=None)

    assert seen[0][0] == "tool_called"
    assert any(
        m == "tool_result" and p["output"] == "[Cancelled by user]" for m, p in seen
    )
    assert session.history[0]["role"] == "user"
    assert any(it.get("type") == "function_call_output" for it in session.history)


@pytest.mark.asyncio
async def test_no_buffering_without_reasoning(monkeypatch):
    monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))
    monkeypatch.setattr(
        bridge,
        "ItemHelpers",
        types.SimpleNamespace(
            text_message_output=lambda item: getattr(item, "text", "")
        ),
    )

    class FakeRunConfig:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
    monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

    calls = []

    async def capture_broadcast(session_arg, method, payload):
        calls.append((method, payload))
        return True

    monkeypatch.setattr(bridge, "_broadcast", capture_broadcast)

    tool_call_item = types.SimpleNamespace(
        type="tool_call_item",
        to_input_item=lambda: {
            "name": "alpha",
            "arguments": "{}",
            "call_id": "norb",
        },
    )

    tool_result_item = types.SimpleNamespace(
        type="tool_call_output_item",
        name="alpha",
        output="done",
        to_input_item=lambda: {"call_id": "norb"},
    )

    message_item = types.SimpleNamespace(
        type="message_output_item",
        text="msg",
        to_input_item=lambda: {"role": "assistant", "content": "msg"},
    )

    events = [
        types.SimpleNamespace(type="run_item_stream_event", item=tool_call_item),
        types.SimpleNamespace(type="run_item_stream_event", item=tool_result_item),
        types.SimpleNamespace(type="run_item_stream_event", item=message_item),
    ]

    class FakeRunResult:
        def __init__(self, evts):
            self._events = evts

        async def stream_events(self):
            for e in self._events:
                yield e

    class FakeRunner:
        @classmethod
        def run_streamed(cls, *args, **kwargs):
            return FakeRunResult(events)

    monkeypatch.setattr("agents.Runner", FakeRunner)

    session = DummySession(channels=[], session_id="buf-4")

    class DummyAgent:
        def __init__(self):
            self.name = "Agent"
            self.instructions = "inst"
            self.tools = []
            self.model = None
            self.model_settings = None

    agent = DummyAgent()

    await bridge.stream_agent(agent, "ask", session, rpc_channel=None)

    assert calls[0][0] == "tool_called"
    assert calls[1][0] == "tool_result"
    assert calls[2][0] == "message_output"


@pytest.mark.asyncio
async def test_running_summary_rides_tool_called_through_buffering(monkeypatch):
    monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))
    monkeypatch.setattr(
        bridge,
        "ItemHelpers",
        types.SimpleNamespace(
            text_message_output=lambda item: getattr(item, "text", "")
        ),
    )

    class FakeRunConfig:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
    monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

    calls = []

    async def capture_broadcast(session_arg, method, payload):
        calls.append((method, payload))
        return True

    monkeypatch.setattr(bridge, "_broadcast", capture_broadcast)

    reasoning_item = types.SimpleNamespace(
        type="reasoning_item",
        to_input_item=lambda: {
            "type": "reasoning",
            "id": "rs_stat",
            "summary": [{"text": "deciding", "type": "summary_text"}],
            "status": "completed",
        },
    )

    tool_call_item = types.SimpleNamespace(
        type="tool_call_item",
        to_input_item=lambda: {
            "name": "alpha",
            "arguments": "{}",
            "call_id": "cstat",
        },
    )

    message_item = types.SimpleNamespace(
        type="message_output_item",
        text="preamble",
        to_input_item=lambda: {"role": "assistant", "content": "preamble"},
    )

    events = [
        types.SimpleNamespace(type="run_item_stream_event", item=reasoning_item),
        types.SimpleNamespace(type="run_item_stream_event", item=tool_call_item),
        types.SimpleNamespace(type="run_item_stream_event", item=message_item),
    ]

    class FakeRunResult:
        def __init__(self, evts):
            self._events = evts

        async def stream_events(self):
            for e in self._events:
                yield e

    class FakeRunner:
        @classmethod
        def run_streamed(cls, *args, **kwargs):
            return FakeRunResult(events)

    monkeypatch.setattr("agents.Runner", FakeRunner)

    session = DummySession(channels=[], session_id="buf-5")

    class DummyTool:
        def __init__(self):
            self.name = "alpha"
            self._running_summary = "Busy"

    class DummyAgent:
        def __init__(self):
            self.name = "Agent"
            self.instructions = "inst"
            self.tools = [DummyTool()]
            self.model = None
            self.model_settings = None

    agent = DummyAgent()

    await bridge.stream_agent(agent, "ask", session, rpc_channel=None)

    assert not any(m == "client_request" for m, _ in calls)
    tool_payloads = [p for m, p in calls if m == "tool_called"]
    assert tool_payloads and tool_payloads[0]["metadata"] == {"summary": "Busy"}
    assert any(m == "message_output" for m, _ in calls)


@pytest.mark.asyncio
async def test_always_approve_covers_parallel_interruptions(monkeypatch):
    """An "Always" on the first of N parallel same-tool approvals must
    silently approve the remaining N-1 interruptions in the batch instead
    of prompting for each one."""
    monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))
    monkeypatch.setattr(
        bridge,
        "ItemHelpers",
        types.SimpleNamespace(text_message_output=lambda item: ""),
    )

    class FakeRunConfig:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
    monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

    def make_interruption(call_id):
        return types.SimpleNamespace(
            tool_name="execute_bash",
            raw_item=types.SimpleNamespace(
                call_id=call_id, name="execute_bash", arguments="{}"
            ),
        )

    class FakeState:
        def __init__(self):
            self.approved = []
            self.rejected = []

        def approve(self, item, always_approve=False):
            self.approved.append(item.raw_item.call_id)

        def reject(self, item, rejection_message=None):
            self.rejected.append(item.raw_item.call_id)

    state = FakeState()

    class FirstRunResult:
        interruptions = [make_interruption(f"c{i}") for i in range(1, 5)]

        async def stream_events(self):
            return
            yield

        def to_state(self):
            return state

    class SecondRunResult:
        interruptions = []

        async def stream_events(self):
            return
            yield

    results = [FirstRunResult(), SecondRunResult()]

    class FakeRunner:
        @classmethod
        def run_streamed(cls, *args, **kwargs):
            return results.pop(0)

    monkeypatch.setattr("agents.Runner", FakeRunner)

    session = DummySession(channels=[], session_id="par-approve")

    prompts = []

    async def fake_request_tool_approval(
        *, session, call_id, tool_name, arguments, run_id=None
    ):
        prompts.append(call_id)
        # Mimic the user clicking "Always": tool_approval_response adds the
        # tool to the session's allow-set before resolving the future.
        session.always_allow_tools = {"execute_bash"}
        return {
            "decision": "approve",
            "always_approve": True,
            "rejection_message": None,
        }

    service = types.SimpleNamespace(request_tool_approval=fake_request_tool_approval)

    class DummyAgent:
        def __init__(self):
            self.name = "Agent"
            self.instructions = "inst"
            self.tools = []
            self.model = None
            self.model_settings = None

    await bridge.stream_agent(
        DummyAgent(), "run 4 commands", session, rpc_channel=None, service=service
    )

    # Only the first parallel call prompted; the "Always" covered the rest.
    assert prompts == ["c1"]
    assert state.approved == ["c1", "c2", "c3", "c4"]
    assert state.rejected == []


def test_stream_event_payload_mcp_identity_labels(monkeypatch):
    """MCP-derived calls carry server_label/tool_label; plain tools don't."""
    monkeypatch.setattr(
        bridge,
        "ItemHelpers",
        types.SimpleNamespace(text_message_output=lambda item: ""),
    )
    session = types.SimpleNamespace(
        mcp_tool_identities={"mcp_srv__hello": ("srv", "hello")}
    )

    mcp_call = types.SimpleNamespace(
        type="tool_call_item",
        to_input_item=lambda: {
            "name": "mcp_srv__hello",
            "arguments": "{}",
            "call_id": "c1",
        },
    )
    method, payload = bridge._stream_event_payload(
        mcp_call, "run-1", session_id="s1", session=session
    )
    assert method == "tool_called"
    assert payload["tool"] == "mcp_srv__hello"
    assert payload["server_label"] == "srv"
    assert payload["tool_label"] == "hello"

    plain_call = types.SimpleNamespace(
        type="tool_call_item",
        to_input_item=lambda: {"name": "read_file", "arguments": "{}", "call_id": "c2"},
    )
    _, plain_payload = bridge._stream_event_payload(
        plain_call, "run-1", session_id="s1", session=session
    )
    assert "server_label" not in plain_payload
    assert "tool_label" not in plain_payload

    mcp_result = types.SimpleNamespace(
        type="tool_call_output_item",
        name="mcp_srv__hello",
        output="done",
        to_input_item=lambda: {"call_id": "c1"},
    )
    method2, result_payload = bridge._stream_event_payload(
        mcp_result, "run-1", session_id="s1", session=session
    )
    assert method2 == "tool_result"
    assert result_payload["server_label"] == "srv"
    assert result_payload["tool_label"] == "hello"


def test_stream_event_payload_translates_mcp_ui_key(monkeypatch):
    """mcp_ui records under the ORIGINAL tool name; the bridge must pop it
    even though the stream carries the prefixed public name."""
    from omniagents.core.agents import mcp_ui

    monkeypatch.setattr(
        bridge,
        "ItemHelpers",
        types.SimpleNamespace(text_message_output=lambda item: ""),
    )
    mcp_ui.reset()
    try:
        mcp_ui.record(
            "srv",
            "hello",
            {"type": "resource", "resource": {"uri": "ui://x", "text": "<div/>"}},
        )
        session = types.SimpleNamespace(
            mcp_tool_identities={"mcp_srv__hello": ("srv", "hello")}
        )
        item = types.SimpleNamespace(
            type="tool_call_output_item",
            name="mcp_srv__hello",
            output="ok",
            to_input_item=lambda: {"call_id": "c1"},
        )
        _, payload = bridge._stream_event_payload(
            item, "run-1", session_id="s1", session=session
        )
        assert payload["metadata"]["display_type"] == "mcp_ui"
        assert payload["metadata"]["mcp_ui"]["server_name"] == "srv"
    finally:
        mcp_ui.reset()


@pytest.mark.asyncio
async def test_always_approve_mcp_batch_is_tuple_scoped(monkeypatch):
    """Always on the first of N parallel MCP interruptions covers the rest of
    the SAME (server, tool); a same-named tool on another server still asks."""
    monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))
    monkeypatch.setattr(
        bridge,
        "ItemHelpers",
        types.SimpleNamespace(text_message_output=lambda item: ""),
    )

    class FakeRunConfig:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
    monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

    def make_interruption(call_id, public_name):
        return types.SimpleNamespace(
            tool_name=public_name,
            raw_item=types.SimpleNamespace(
                call_id=call_id, name=public_name, arguments="{}"
            ),
        )

    class FakeState:
        def __init__(self):
            self.approved = []
            self.rejected = []

        def approve(self, item, always_approve=False):
            self.approved.append(item.raw_item.call_id)

        def reject(self, item, rejection_message=None):
            self.rejected.append(item.raw_item.call_id)

    state = FakeState()

    class FirstRunResult:
        interruptions = [
            make_interruption("c1", "mcp_alpha__do_thing"),
            make_interruption("c2", "mcp_alpha__do_thing"),
            make_interruption("c3", "mcp_alpha__do_thing"),
            make_interruption("c4", "mcp_beta__do_thing"),
        ]

        async def stream_events(self):
            return
            yield

        def to_state(self):
            return state

    class SecondRunResult:
        interruptions = []

        async def stream_events(self):
            return
            yield

    results = [FirstRunResult(), SecondRunResult()]

    class FakeRunner:
        @classmethod
        def run_streamed(cls, *args, **kwargs):
            return results.pop(0)

    monkeypatch.setattr("agents.Runner", FakeRunner)

    session = DummySession(channels=[], session_id="mcp-batch")
    session.mcp_tool_identities = {
        "mcp_alpha__do_thing": ("alpha", "do_thing"),
        "mcp_beta__do_thing": ("beta", "do_thing"),
    }

    prompts = []

    async def fake_request_tool_approval(
        *, session, call_id, tool_name, arguments, run_id=None
    ):
        prompts.append(tool_name)
        # Mimic "Always": the response handler adds the (server, tool)
        # tuple resolved from the identity map to the tuple set.
        identity = session.mcp_tool_identities[tool_name]
        allow = getattr(session, "always_allow_mcp_tools", None)
        if allow is None:
            allow = set()
            session.always_allow_mcp_tools = allow
        allow.add(identity)
        return {
            "decision": "approve",
            "always_approve": True,
            "rejection_message": None,
        }

    service = types.SimpleNamespace(request_tool_approval=fake_request_tool_approval)

    class DummyAgent:
        def __init__(self):
            self.name = "Agent"
            self.instructions = "inst"
            self.tools = []
            self.model = None
            self.model_settings = None

    await bridge.stream_agent(
        DummyAgent(), "run things", session, rpc_channel=None, service=service
    )

    # alpha prompted once (covering c2/c3); beta's same-named tool prompted
    # separately — the grant is tuple-scoped, not name-scoped.
    assert prompts == ["mcp_alpha__do_thing", "mcp_beta__do_thing"]
    assert state.approved == ["c1", "c2", "c3", "c4"]
    assert state.rejected == []
