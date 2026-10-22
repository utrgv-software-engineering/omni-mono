"""Items from providers without Responses ids are each kept, once.

Chat-completions providers give every item the agents SDK's placeholder id,
and a tool call's id is optional; neither may decide an item's identity.
"""

from unittest.mock import AsyncMock

import pytest
from agents import Agent, function_tool
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseReasoningItem,
)
from openai.types.responses.response_reasoning_item import Content

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
    """Replies carry the SDK placeholder id; tool calls carry no id and come
    with raw reasoning and an empty assistant message, as live
    chat-completions providers send."""

    def _decide(self, system_instructions, input):
        out = []
        for item in super()._decide(system_instructions, input):
            if isinstance(item, ResponseFunctionToolCall):
                out.append(
                    ResponseReasoningItem(
                        id="__fake_id__",
                        type="reasoning",
                        summary=[],
                        content=[Content(type="reasoning_text", text="check the weather")],
                    )
                )
                out.append(
                    ResponseOutputMessage(
                        id="__fake_id__",
                        type="message",
                        role="assistant",
                        status="completed",
                        content=[
                            ResponseOutputText(
                                type="output_text", text="", annotations=[]
                            )
                        ],
                    )
                )
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
    # The model context keeps the empty message the provider sent.
    assert len(replies) == 3
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
    # One reasoning item: the end-of-run flush must not re-append it.
    assert sum(1 for row in session.history if row.get("type") == "reasoning") == 1
    assert [
        item.content.get("summary")
        for item in snapshot["items"]
        if item.kind.value == "reasoning"
    ] == ["check the weather"]
