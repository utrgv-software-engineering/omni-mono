"""Tests for reasoning item pairing logic in bridge.py.

These tests ensure that reasoning items are correctly paired with their
following message or tool call items, preventing API errors like:
"Item 'msg_X' was provided without its required 'reasoning' item: 'rs_Y'"
"""

import asyncio
import types

import pytest

import omniagents.core.runtime.bridge as bridge


class DummyChannel:
    """Mock RPC channel for testing broadcasts."""

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    async def async_call(self, method, args):
        if self.fail:
            raise RuntimeError("channel closed")
        self.calls.append((method, args))


class DummySession:
    """Mock session for testing item persistence."""

    def __init__(self, channels=None, history=None, session_id="session-test"):
        self.channels = list(channels or [])
        self.history = list(history or [])
        self.pending_events = []
        self.id = session_id

    def append_message(self, message):
        self.history.append(message)


class TestReasoningMessagePairing:
    """Test reasoning items paired with message items."""

    @pytest.mark.asyncio
    async def test_reasoning_followed_by_message_persists_both(self, monkeypatch):
        """Reasoning + message should persist both in correct order."""
        monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))
        monkeypatch.setattr(
            bridge,
            "ItemHelpers",
            types.SimpleNamespace(text_message_output=lambda item: "text"),
        )

        class FakeRunConfig:
            def __init__(self, **kwargs):
                pass

        monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
        monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

        reasoning_item = types.SimpleNamespace(
            type="reasoning_item",
            to_input_item=lambda: {
                "type": "reasoning",
                "id": "rs_test123",
                "summary": [{"text": "thinking", "type": "summary_text"}],
                "status": "completed",
            },
        )

        message_item = types.SimpleNamespace(
            type="message_output_item",
            to_input_item=lambda: {
                "type": "message",
                "id": "msg_test456",
                "role": "assistant",
                "content": [{"type": "text", "text": "answer"}],
                "status": "completed",
            },
        )

        events = [
            types.SimpleNamespace(type="run_item_stream_event", item=reasoning_item),
            types.SimpleNamespace(type="run_item_stream_event", item=message_item),
        ]

        class FakeRunResult:
            async def stream_events(self):
                for event in events:
                    yield event

        class FakeRunner:
            @classmethod
            def run_streamed(cls, *args, **kwargs):
                return FakeRunResult()

        monkeypatch.setattr("agents.Runner", FakeRunner)

        session = DummySession(channels=[], session_id="pair-test")

        class DummyAgent:
            def __init__(self):
                self.name = "Agent"
                self.instructions = "inst"
                self.tools = []
                self.model = None
                self.model_settings = None

        agent = DummyAgent()

        await bridge.stream_agent(agent, "prompt", session, rpc_channel=None)

        # Should have: user message, reasoning, message
        assert len(session.history) == 3
        assert session.history[0]["role"] == "user"  # User prompt
        assert session.history[1]["type"] == "reasoning"  # Reasoning item
        assert session.history[1]["id"] == "rs_test123"
        assert session.history[2]["type"] == "message"  # Message item
        assert session.history[2]["id"] == "msg_test456"

    @pytest.mark.asyncio
    async def test_message_without_reasoning_persists_normally(self, monkeypatch):
        """Message without preceding reasoning should persist normally."""
        monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))
        monkeypatch.setattr(
            bridge,
            "ItemHelpers",
            types.SimpleNamespace(text_message_output=lambda item: "text"),
        )

        class FakeRunConfig:
            def __init__(self, **kwargs):
                pass

        monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
        monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

        message_item = types.SimpleNamespace(
            type="message_output_item",
            to_input_item=lambda: {
                "type": "message",
                "id": "msg_solo",
                "role": "assistant",
                "content": [{"type": "text", "text": "answer"}],
                "status": "completed",
            },
        )

        events = [
            types.SimpleNamespace(type="run_item_stream_event", item=message_item)
        ]

        class FakeRunResult:
            async def stream_events(self):
                for event in events:
                    yield event

        class FakeRunner:
            @classmethod
            def run_streamed(cls, *args, **kwargs):
                return FakeRunResult()

        monkeypatch.setattr("agents.Runner", FakeRunner)

        session = DummySession(channels=[], session_id="solo-msg")

        class DummyAgent:
            def __init__(self):
                self.name = "Agent"
                self.instructions = "inst"
                self.tools = []
                self.model = None
                self.model_settings = None

        agent = DummyAgent()

        await bridge.stream_agent(agent, "prompt", session, rpc_channel=None)

        # Should have: user message, message (no reasoning)
        assert len(session.history) == 2
        assert session.history[0]["role"] == "user"
        assert session.history[1]["type"] == "message"
        assert session.history[1]["id"] == "msg_solo"


class TestReasoningToolCallPairing:
    """Test reasoning items paired with tool call items."""

    @pytest.mark.asyncio
    async def test_reasoning_followed_by_function_call_persists_both(self, monkeypatch):
        """Reasoning + function_call should persist both in correct order."""
        monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))
        monkeypatch.setattr(
            bridge,
            "ItemHelpers",
            types.SimpleNamespace(text_message_output=lambda item: "text"),
        )

        class FakeRunConfig:
            def __init__(self, **kwargs):
                pass

        monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
        monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

        reasoning_item = types.SimpleNamespace(
            type="reasoning_item",
            to_input_item=lambda: {
                "type": "reasoning",
                "id": "rs_func123",
                "summary": [{"text": "deciding to call tool", "type": "summary_text"}],
                "status": "completed",
            },
        )

        function_call_item = types.SimpleNamespace(
            type="tool_call_item",
            to_input_item=lambda: {
                "type": "function_call",
                "id": "fc_xyz",
                "call_id": "fc_xyz",
                "name": "search",
                "arguments": '{"query": "test"}',
                "status": "completed",
            },
        )

        events = [
            types.SimpleNamespace(type="run_item_stream_event", item=reasoning_item),
            types.SimpleNamespace(
                type="run_item_stream_event", item=function_call_item
            ),
        ]

        class FakeRunResult:
            async def stream_events(self):
                for event in events:
                    yield event

        class FakeRunner:
            @classmethod
            def run_streamed(cls, *args, **kwargs):
                return FakeRunResult()

        monkeypatch.setattr("agents.Runner", FakeRunner)

        session = DummySession(channels=[], session_id="func-call-test")

        class DummyAgent:
            def __init__(self):
                self.name = "Agent"
                self.instructions = "inst"
                self.tools = []
                self.model = None
                self.model_settings = None

        agent = DummyAgent()

        await bridge.stream_agent(agent, "prompt", session, rpc_channel=None)

        # Should have: user message, reasoning, function_call, cancelled output
        assert len(session.history) == 4
        assert session.history[0]["role"] == "user"
        assert session.history[1]["type"] == "reasoning"
        assert session.history[1]["id"] == "rs_func123"
        assert session.history[2]["type"] == "function_call"
        assert session.history[2]["id"] == "fc_xyz"
        # Tool call gets cancelled output added in finally block
        assert session.history[3]["type"] == "function_call_output"

    @pytest.mark.asyncio
    async def test_reasoning_followed_by_computer_call_persists_both(self, monkeypatch):
        """Reasoning + computer_call should persist both in correct order."""
        monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))
        monkeypatch.setattr(
            bridge,
            "ItemHelpers",
            types.SimpleNamespace(text_message_output=lambda item: "text"),
        )

        class FakeRunConfig:
            def __init__(self, **kwargs):
                pass

        monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
        monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

        reasoning_item = types.SimpleNamespace(
            type="reasoning_item",
            to_input_item=lambda: {
                "type": "reasoning",
                "id": "rs_comp456",
                "summary": [
                    {"text": "deciding to use computer", "type": "summary_text"}
                ],
                "status": "completed",
            },
        )

        computer_call_item = types.SimpleNamespace(
            type="tool_call_item",
            to_input_item=lambda: {
                "type": "computer_call",
                "id": "cc_abc",
                "call_id": "cc_abc",
                "action": {"type": "screenshot"},
                "status": "completed",
            },
        )

        events = [
            types.SimpleNamespace(type="run_item_stream_event", item=reasoning_item),
            types.SimpleNamespace(
                type="run_item_stream_event", item=computer_call_item
            ),
        ]

        class FakeRunResult:
            async def stream_events(self):
                for event in events:
                    yield event

        class FakeRunner:
            @classmethod
            def run_streamed(cls, *args, **kwargs):
                return FakeRunResult()

        monkeypatch.setattr("agents.Runner", FakeRunner)

        session = DummySession(channels=[], session_id="comp-call-test")

        class DummyAgent:
            def __init__(self):
                self.name = "Agent"
                self.instructions = "inst"
                self.tools = []
                self.model = None
                self.model_settings = None

        agent = DummyAgent()

        await bridge.stream_agent(agent, "prompt", session, rpc_channel=None)

        # Should have: user message, reasoning, computer_call, cancelled output
        assert len(session.history) == 4
        assert session.history[0]["role"] == "user"
        assert session.history[1]["type"] == "reasoning"
        assert session.history[1]["id"] == "rs_comp456"
        assert session.history[2]["type"] == "computer_call"
        assert session.history[2]["id"] == "cc_abc"
        # Tool call gets cancelled output added in finally block
        assert session.history[3]["type"] == "function_call_output"


class TestOrphanedReasoningHandling:
    """Test that orphaned reasoning items are properly discarded."""

    @pytest.mark.asyncio
    async def test_reasoning_followed_by_output_discards_reasoning(self, monkeypatch):
        """Reasoning followed by tool output (not call) should discard reasoning."""
        monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))
        monkeypatch.setattr(
            bridge,
            "ItemHelpers",
            types.SimpleNamespace(text_message_output=lambda item: "text"),
        )

        class FakeRunConfig:
            def __init__(self, **kwargs):
                pass

        monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
        monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

        reasoning_item = types.SimpleNamespace(
            type="reasoning_item",
            to_input_item=lambda: {
                "type": "reasoning",
                "id": "rs_orphan",
                "summary": [{"text": "orphaned", "type": "summary_text"}],
                "status": "completed",
            },
        )

        output_item = types.SimpleNamespace(
            type="tool_call_output_item",
            to_input_item=lambda: {
                "type": "function_call_output",
                "call_id": "fc_old",
                "output": "result",
            },
        )

        events = [
            types.SimpleNamespace(type="run_item_stream_event", item=reasoning_item),
            types.SimpleNamespace(type="run_item_stream_event", item=output_item),
        ]

        class FakeRunResult:
            async def stream_events(self):
                for event in events:
                    yield event

        class FakeRunner:
            @classmethod
            def run_streamed(cls, *args, **kwargs):
                return FakeRunResult()

        monkeypatch.setattr("agents.Runner", FakeRunner)

        session = DummySession(channels=[], session_id="orphan-test")

        class DummyAgent:
            def __init__(self):
                self.name = "Agent"
                self.instructions = "inst"
                self.tools = []
                self.model = None
                self.model_settings = None

        agent = DummyAgent()

        await bridge.stream_agent(agent, "prompt", session, rpc_channel=None)

        # Should have: user message, output (reasoning discarded)
        assert len(session.history) == 2
        assert session.history[0]["role"] == "user"
        assert session.history[1]["type"] == "function_call_output"
        # Reasoning should NOT be in history
        assert not any(item.get("type") == "reasoning" for item in session.history)

    @pytest.mark.asyncio
    async def test_reasoning_at_end_of_cancelled_run_not_persisted(self, monkeypatch):
        """Reasoning item at end of cancelled run should be discarded."""
        monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))
        monkeypatch.setattr(
            bridge,
            "ItemHelpers",
            types.SimpleNamespace(text_message_output=lambda item: "text"),
        )

        class FakeRunConfig:
            def __init__(self, **kwargs):
                pass

        monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
        monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

        reasoning_item = types.SimpleNamespace(
            type="reasoning_item",
            to_input_item=lambda: {
                "type": "reasoning",
                "id": "rs_cancel",
                "summary": [{"text": "interrupted", "type": "summary_text"}],
                "status": "in_progress",
            },
        )

        events = [
            types.SimpleNamespace(type="run_item_stream_event", item=reasoning_item)
        ]

        class FakeRunResult:
            async def stream_events(self):
                for event in events:
                    yield event
                # Simulate cancellation after reasoning but before message
                raise asyncio.CancelledError()

        class FakeRunner:
            @classmethod
            def run_streamed(cls, *args, **kwargs):
                return FakeRunResult()

        monkeypatch.setattr("agents.Runner", FakeRunner)

        session = DummySession(channels=[], session_id="cancel-test")

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

        # Should have only user message (reasoning not persisted due to cancellation)
        assert len(session.history) == 1
        assert session.history[0]["role"] == "user"
        assert not any(item.get("type") == "reasoning" for item in session.history)


class TestMultipleReasoningItems:
    """Test scenarios with multiple reasoning items."""

    @pytest.mark.asyncio
    async def test_multiple_reasoning_message_pairs_all_persisted(self, monkeypatch):
        """Multiple reasoning+message pairs should all be persisted correctly."""
        monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))
        monkeypatch.setattr(
            bridge,
            "ItemHelpers",
            types.SimpleNamespace(text_message_output=lambda item: "text"),
        )

        class FakeRunConfig:
            def __init__(self, **kwargs):
                pass

        monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
        monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

        events = []

        # First reasoning+message pair
        events.append(
            types.SimpleNamespace(
                type="run_item_stream_event",
                item=types.SimpleNamespace(
                    type="reasoning_item",
                    to_input_item=lambda: {
                        "type": "reasoning",
                        "id": "rs_1",
                        "summary": [],
                        "status": "completed",
                    },
                ),
            )
        )
        events.append(
            types.SimpleNamespace(
                type="run_item_stream_event",
                item=types.SimpleNamespace(
                    type="message_output_item",
                    to_input_item=lambda: {
                        "type": "message",
                        "id": "msg_1",
                        "role": "assistant",
                        "content": [],
                        "status": "completed",
                    },
                ),
            )
        )

        # Second reasoning+tool call pair
        events.append(
            types.SimpleNamespace(
                type="run_item_stream_event",
                item=types.SimpleNamespace(
                    type="reasoning_item",
                    to_input_item=lambda: {
                        "type": "reasoning",
                        "id": "rs_2",
                        "summary": [],
                        "status": "completed",
                    },
                ),
            )
        )
        events.append(
            types.SimpleNamespace(
                type="run_item_stream_event",
                item=types.SimpleNamespace(
                    type="tool_call_item",
                    to_input_item=lambda: {
                        "type": "function_call",
                        "id": "fc_2",
                        "call_id": "fc_2",
                        "name": "search",
                        "arguments": "{}",
                        "status": "completed",
                    },
                ),
            )
        )

        class FakeRunResult:
            async def stream_events(self):
                for event in events:
                    yield event

        class FakeRunner:
            @classmethod
            def run_streamed(cls, *args, **kwargs):
                return FakeRunResult()

        monkeypatch.setattr("agents.Runner", FakeRunner)

        session = DummySession(channels=[], session_id="multi-test")

        class DummyAgent:
            def __init__(self):
                self.name = "Agent"
                self.instructions = "inst"
                self.tools = []
                self.model = None
                self.model_settings = None

        agent = DummyAgent()

        await bridge.stream_agent(agent, "prompt", session, rpc_channel=None)

        # Should have: user, rs_1, msg_1, rs_2, fc_2, cancelled output
        assert len(session.history) == 6
        assert session.history[0]["role"] == "user"
        assert session.history[1]["id"] == "rs_1"
        assert session.history[2]["id"] == "msg_1"
        assert session.history[3]["id"] == "rs_2"
        assert session.history[4]["id"] == "fc_2"
        # fc_2 tool call gets cancelled output added in finally block
        assert session.history[5]["type"] == "function_call_output"


class TestEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_malformed_reasoning_item_ignored(self, monkeypatch):
        """Malformed reasoning item that fails to_input_item should be ignored."""
        monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))
        monkeypatch.setattr(
            bridge,
            "ItemHelpers",
            types.SimpleNamespace(text_message_output=lambda item: "text"),
        )

        class FakeRunConfig:
            def __init__(self, **kwargs):
                pass

        monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
        monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

        class BadReasoningItem:
            def __init__(self):
                self.type = "reasoning_item"

            def to_input_item(self):
                raise RuntimeError("malformed")

        message_item = types.SimpleNamespace(
            type="message_output_item",
            to_input_item=lambda: {
                "type": "message",
                "id": "msg_safe",
                "role": "assistant",
                "content": [],
                "status": "completed",
            },
        )

        events = [
            types.SimpleNamespace(
                type="run_item_stream_event", item=BadReasoningItem()
            ),
            types.SimpleNamespace(type="run_item_stream_event", item=message_item),
        ]

        class FakeRunResult:
            async def stream_events(self):
                for event in events:
                    yield event

        class FakeRunner:
            @classmethod
            def run_streamed(cls, *args, **kwargs):
                return FakeRunResult()

        monkeypatch.setattr("agents.Runner", FakeRunner)

        session = DummySession(channels=[], session_id="error-test")

        class DummyAgent:
            def __init__(self):
                self.name = "Agent"
                self.instructions = "inst"
                self.tools = []
                self.model = None
                self.model_settings = None

        agent = DummyAgent()

        # Should not raise, should handle gracefully
        await bridge.stream_agent(agent, "prompt", session, rpc_channel=None)

        # Should have: user, message (bad reasoning ignored)
        assert len(session.history) == 2
        assert session.history[0]["role"] == "user"
        assert session.history[1]["id"] == "msg_safe"

    @pytest.mark.asyncio
    async def test_tool_call_without_reasoning_persists_normally(self, monkeypatch):
        """Tool call without reasoning should persist normally."""
        monkeypatch.setattr(bridge.Debug, "enabled", classmethod(lambda cls: False))
        monkeypatch.setattr(
            bridge,
            "ItemHelpers",
            types.SimpleNamespace(text_message_output=lambda item: "text"),
        )

        class FakeRunConfig:
            def __init__(self, **kwargs):
                pass

        monkeypatch.setattr("agents.RunConfig", FakeRunConfig)
        monkeypatch.setattr(bridge, "RunConfig", FakeRunConfig)

        function_call_item = types.SimpleNamespace(
            type="tool_call_item",
            to_input_item=lambda: {
                "type": "function_call",
                "id": "fc_solo",
                "call_id": "fc_solo",
                "name": "search",
                "arguments": "{}",
                "status": "completed",
            },
        )

        events = [
            types.SimpleNamespace(type="run_item_stream_event", item=function_call_item)
        ]

        class FakeRunResult:
            async def stream_events(self):
                for event in events:
                    yield event

        class FakeRunner:
            @classmethod
            def run_streamed(cls, *args, **kwargs):
                return FakeRunResult()

        monkeypatch.setattr("agents.Runner", FakeRunner)

        session = DummySession(channels=[], session_id="solo-call")

        class DummyAgent:
            def __init__(self):
                self.name = "Agent"
                self.instructions = "inst"
                self.tools = []
                self.model = None
                self.model_settings = None

        agent = DummyAgent()

        await bridge.stream_agent(agent, "prompt", session, rpc_channel=None)

        # Should have: user, function_call, cancelled output
        assert len(session.history) == 3
        assert session.history[0]["role"] == "user"
        assert session.history[1]["type"] == "function_call"
        assert session.history[1]["id"] == "fc_solo"
        # Tool call without output gets cancelled output added
        assert session.history[2]["type"] == "function_call_output"
