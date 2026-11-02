import json
from unittest.mock import AsyncMock
from agents.tool_context import ToolContext
import pytest
from omniagents.builtin.tools import chat_messages as tools


@pytest.mark.asyncio
async def test_publication_is_explicit_scoped_and_retry_stable(monkeypatch):
    request = AsyncMock(return_value={"published": True})
    monkeypatch.setattr(tools, "_request", request)
    ctx = ToolContext(
        context={"session_id": "personal-side"},
        tool_name="chat_send_message",
        tool_call_id="call-1",
        tool_arguments="{}",
    )
    for _ in range(2):
        result = await tools.chat_send_message.on_invoke_tool(
            ctx,
            json.dumps(
                {"text": "Public answer", "conversation": None, "message_id": None}
            ),
        )
        assert result["published"]
        request.assert_awaited_with(
            "POST",
            "/chat/messages",
            json={
                "conversation": "personal-side",
                "text": "Public answer",
                "message_id": "call-1",
            },
        )
    await tools.chat_send_message.on_invoke_tool(
        ctx,
        json.dumps(
            {
                "text": "Follow-up",
                "conversation": "factory-personal",
                "message_id": "stable",
            }
        ),
    )
    request.assert_awaited_with(
        "POST",
        "/chat/messages",
        json={
            "conversation": "factory-personal",
            "text": "Follow-up",
            "message_id": "stable",
        },
    )
