"""Items from providers without Responses ids are each kept, once.

Chat-completions providers give every item the agents SDK's placeholder id,
and a tool call's id is optional; neither may decide an item's identity.
"""

from unittest.mock import AsyncMock

import pytest
from agents import Agent, function_tool
from openai.types.responses import ResponseFunctionToolCall

from omniagents.core.conversation.store import get_conversation_store
from omniagents.core.runtime.bridge import stream_agent
from omniagents.core.session.manager import Session
from omniagents.testing import FakeModel


class _Service:
    _runtime_hooks: dict = {}
    set_client_status = AsyncMock()

    async def invoke_runtime_hooks(self, *args, **kwargs):
        return None


class _ChatCompletionsModel(FakeModel):
    """Replies carry the SDK placeholder id; tool calls carry no id."""

    def _decide(self, system_instructions, input):
        out = []
        for item in super()._decide(system_instructions, input):
            if isinstance(item, ResponseFunctionToolCall):
                item = ResponseFunctionToolCall(
                    type="function_call",
                    name=item.name,
                    call_id=item.call_id,
                    arguments=item.arguments,
                )
            elif getattr(item, "type", None) == "message":
                item.id = "__fake_id__"
            out.append(item)
        return out


@function_tool
def get_weather(city: str) -> str:
    """Weather for a city."""
    return f"sunny in {city}"


@pytest.mark.asyncio
async def test_each_reply_and_tool_call_is_kept_once(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIAGENTS_HOME", str(tmp_path))
    session = Session("ids", "chat-completions")
    agent = Agent(name="w", model=_ChatCompletionsModel(), tools=[get_weather])
    for prompt in ("what is the weather in paris", "second question"):
        await stream_agent(agent, prompt, session, None, service=_Service())

    calls = [row for row in session.history if row.get("type") == "function_call"]
    replies = [row for row in session.history if row.get("role") == "assistant"]
    assert len(calls) == 1
    assert len(replies) == 2
    assert all("id" not in row for row in replies)

    snapshot = get_conversation_store("ids", "chat-completions").read_snapshot(
        session.id
    )
    texts = [
        item.content.get("text")
        for item in snapshot["items"]
        if item.kind.value == "agent_message"
    ]
    assert texts == ["It is sunny in Paris.", "You said: second question"]
