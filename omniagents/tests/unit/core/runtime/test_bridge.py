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
    call_item = types.SimpleNamespace(
        type="tool_call_item",
        to_input_item=lambda: {"name": "alpha", "arguments": "{}", "call_id": "c1"},
    )
    method, payload = bridge._stream_event_payload(call_item, run_id)
    assert method == "tool_called"
    assert payload["call_id"] == "c1"
    output_item = types.SimpleNamespace(
        type="tool_call_output_item",
        name="alpha",
        output=RichToolOutput("done", ui_metadata={"hint": "ok"}),
        to_input_item=lambda: {"call_id": "c1"},
    )
    method2, payload2 = bridge._stream_event_payload(output_item, run_id)
    assert method2 == "tool_result"
    assert payload2["metadata"] == {"hint": "ok"}
    message_item = types.SimpleNamespace(
        type="message_output_item",
        text="hello",
        to_input_item=lambda: {"role": "assistant", "content": "hello"},
    )
    method3, payload3 = bridge._stream_event_payload(message_item, run_id)
    assert method3 == "message_output"
    assert payload3["content"] == "hello"
    unknown_item = types.SimpleNamespace(type="other")
    method4, payload4 = bridge._stream_event_payload(unknown_item, run_id)
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

    method, payload = bridge._stream_event_payload(BadItem(), "run-x")
    assert method == "tool_result"
    assert payload["output"] == "plain"
    assert "call_id" not in payload


@pytest.mark.asyncio
async def test_stream_agent_emits_usage_and_status_overrides(monkeypatch):
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
            self._client_status_text = "Running"

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

    assert configs[0] == {
        "group_id": "session_abc123",
        "workflow_name": "workflow",
    }
    assert live.calls[0][0] == "token"
    run_id = live.calls[0][1]["run_id"]
    assert run_id
    assert any(call[0] == "client_request" for call in live.calls)
    assert live.calls[2][0] == "tool_called"
    assert live.calls[3][1]["request_id"] == "status_clear_call-1"
    assert live.calls[4][0] == "tool_result"
    assert live.calls[5][0] == "message_output"
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
        context_window=256,
    )

    assert session.append_count == 0
    assert session.history == [{"role": "system", "content": "ready"}]
    assert captured_inputs[0] == [{"role": "system", "content": "ready"}]
    assert configs[0]["workflow_name"] == "omniagents_conversation"
    assert result["context_window"] == 256


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
async def test_stream_agent_handles_status_clear_failure(monkeypatch):
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
            "call_id": "call-clear",
        },
    )

    tool_result_item = types.SimpleNamespace(
        type="tool_call_output_item",
        name="alpha",
        output="done",
        to_input_item=lambda: {"call_id": "call-clear"},
    )

    events = [
        types.SimpleNamespace(type="run_item_stream_event", item=tool_call_item),
        types.SimpleNamespace(type="run_item_stream_event", item=tool_result_item),
    ]

    class FakeRunResult:
        def __init__(self, events):
            self._events = events

        async def stream_events(self):
            for event in self._events:
                yield event

    class FakeRunner:
        @classmethod
        def run_streamed(cls, *args, **kwargs):
            return FakeRunResult(events)

    monkeypatch.setattr("agents.Runner", FakeRunner)

    calls = []

    async def flaky_broadcast(session_arg, method, payload):
        calls.append((method, payload))
        if method == "client_request" and payload.get("request_id", "").startswith(
            "status_clear"
        ):
            raise RuntimeError("fail")
        return True

    monkeypatch.setattr(bridge, "_broadcast", flaky_broadcast)

    class DummyAgent:
        def __init__(self):
            self.name = "Agent"
            self.instructions = "inst"
            self.tools = [
                types.SimpleNamespace(name="alpha", _client_status_text="Busy")
            ]
            self.model = None
            self.model_settings = None

    agent = DummyAgent()
    session = DummySession(channels=[DummyChannel()], session_id="status")

    result = await bridge.stream_agent(agent, "prompt", session, rpc_channel=None)

    assert calls[0][0] == "client_request"
    assert calls[-1][0] == "tool_result"
    assert result["usage"]["total_tokens"] == 0


def test_filter_history_for_api_handles_parallel_function_calls_after_reasoning():
    """Test that reasoning followed by multiple parallel function calls is reordered correctly."""
    history = [
        {"role": "user", "content": "investigate codebase"},
        {"type": "reasoning", "id": "rs_123", "summary": []},
        {"type": "function_call", "id": "fc_1", "call_id": "fc_1", "name": "list_dir"},
        {"type": "function_call", "id": "fc_2", "call_id": "fc_2", "name": "glob"},
        {"type": "function_call", "id": "fc_3", "call_id": "fc_3", "name": "grep"},
        {
            "type": "message",
            "id": "msg_1",
            "role": "assistant",
            "content": [{"type": "text", "text": "Scanning repo"}],
        },
        {"type": "function_call_output", "call_id": "fc_1", "output": "result1"},
        {"type": "function_call_output", "call_id": "fc_2", "output": "result2"},
        {"type": "function_call_output", "call_id": "fc_3", "output": "result3"},
    ]

    result = bridge._filter_history_for_api(history)

    # Expected order: user, reasoning, message, fc_1, fc_2, fc_3, output1, output2, output3
    assert len(result) == 9
    assert result[0]["role"] == "user"
    assert result[1]["type"] == "reasoning"
    assert result[1]["id"] == "rs_123"
    assert result[2]["type"] == "message"
    assert result[2]["id"] == "msg_1"
    assert result[3]["type"] == "function_call"
    assert result[3]["id"] == "fc_1"
    assert result[4]["type"] == "function_call"
    assert result[4]["id"] == "fc_2"
    assert result[5]["type"] == "function_call"
    assert result[5]["id"] == "fc_3"
    assert result[6]["type"] == "function_call_output"
    assert result[6]["call_id"] == "fc_1"
    assert result[7]["type"] == "function_call_output"
    assert result[7]["call_id"] == "fc_2"
    assert result[8]["type"] == "function_call_output"
    assert result[8]["call_id"] == "fc_3"


def test_filter_history_for_api_handles_reasoning_with_single_function_call():
    """Test that reasoning followed by a single function call is reordered correctly."""
    history = [
        {"type": "reasoning", "id": "rs_456", "summary": []},
        {"type": "function_call", "id": "fc_1", "call_id": "fc_1", "name": "search"},
        {"type": "message", "id": "msg_1", "role": "assistant", "content": []},
        {"type": "function_call_output", "call_id": "fc_1", "output": "result"},
    ]

    result = bridge._filter_history_for_api(history)

    # Expected order: reasoning, message, fc_1, output
    assert len(result) == 4
    assert result[0]["type"] == "reasoning"
    assert result[1]["type"] == "message"
    assert result[2]["type"] == "function_call"
    assert result[3]["type"] == "function_call_output"


def test_filter_history_for_api_handles_reasoning_without_function_calls():
    """Test that reasoning followed by just a message (no function calls) works."""
    history = [
        {"type": "reasoning", "id": "rs_789", "summary": []},
        {"type": "message", "id": "msg_1", "role": "assistant", "content": []},
    ]

    result = bridge._filter_history_for_api(history)

    # Expected order: reasoning, message
    assert len(result) == 2
    assert result[0]["type"] == "reasoning"
    assert result[1]["type"] == "message"


def test_filter_history_for_api_preserves_non_reasoning_items():
    """Test that items without reasoning are preserved as-is."""
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"type": "function_call_output", "call_id": "fc_x", "output": "old result"},
    ]

    result = bridge._filter_history_for_api(history)

    # Should be unchanged
    assert result == history


def test_filter_history_for_api_handles_multiple_reasoning_groups():
    """Test multiple reasoning items in sequence are handled correctly."""
    history = [
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "tool1"},
        {"type": "message", "id": "msg_1", "role": "assistant", "content": []},
        {"type": "function_call_output", "call_id": "call_1", "output": "r1"},
        {"type": "reasoning", "id": "rs_2", "summary": []},
        {"type": "function_call", "id": "fc_2", "call_id": "call_2", "name": "tool2"},
        {"type": "function_call_output", "call_id": "call_2", "output": "r2"},
    ]

    result = bridge._filter_history_for_api(history)

    # Expected: rs_1, msg_1, fc_1, out_1, synth_fc_2, synth_out_2
    # rs_2 is dropped (no message) but fc_2 and out_2 are preserved as synthetic with new IDs
    assert len(result) == 6
    assert result[0]["id"] == "rs_1"
    assert result[1]["id"] == "msg_1"
    assert result[2]["id"] == "fc_1"
    assert result[3]["call_id"] == "call_1"
    # Synthetic function_call and output
    assert result[4]["type"] == "function_call"
    assert result[4]["id"].startswith("fc_synth_")
    assert result[4]["name"] == "tool2"
    assert result[5]["type"] == "function_call_output"
    assert result[5]["call_id"].startswith("call_synth_")
    assert result[5]["output"] == "r2"


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
            self._client_status_text = "Running"

    class DummyAgent:
        def __init__(self):
            self.name = "Agent"
            self.instructions = "inst"
            self.tools = [DummyTool()]
            self.model = None
            self.model_settings = None

    agent = DummyAgent()

    await bridge.stream_agent(agent, "ask", session, rpc_channel=None)

    assert broadcasts[0][0] == "client_request"
    assert broadcasts[1][0] == "tool_called"
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
async def test_buffers_client_status_events_until_preamble(monkeypatch):
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
            self._client_status_text = "Busy"

    class DummyAgent:
        def __init__(self):
            self.name = "Agent"
            self.instructions = "inst"
            self.tools = [DummyTool()]
            self.model = None
            self.model_settings = None

    agent = DummyAgent()

    await bridge.stream_agent(agent, "ask", session, rpc_channel=None)

    assert calls[0][0] == "client_request"
    assert any(m == "message_output" for m, _ in calls)
    assert any(
        m == "client_request" and p.get("request_id", "").startswith("status_")
        for m, p in calls
    )
