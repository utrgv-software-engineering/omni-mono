"""Explicit public Workplace communication. Final responses are not publication."""

import uuid
from typing import Any
from agents.run_context import RunContextWrapper
from omniagents.core.tools.discovery import function_tool
from omniagents.core.session.helpers import session_id_from_ctx
from .library_artifacts import _request


def _chat(ctx, conversation):
    chat = conversation or session_id_from_ctx(ctx)
    if not chat:
        raise ValueError("A registered Workplace conversation is required")
    return chat


@function_tool
async def chat_send_message(
    ctx: RunContextWrapper[Any],
    text: str,
    conversation: str | None = None,
    message_id: str | None = None,
) -> dict:
    """Publish a message to the user in Workplace Chat. This is the ONLY way to publish a text reply.

    Use for replies, questions, progress updates and background follow-ups. Your final
    response and tool outputs are internal and are never automatically published.
    Defaults to this conversation. Pass another registered conversation ID only when
    intentionally addressing that chat. Reuse message_id when retrying an uncertain
    delivery; do not resend with a new ID. Publication does not start an agent run.
    """
    return await _request(
        "POST",
        "/chat/messages",
        json={
            "conversation": _chat(ctx, conversation),
            "text": text,
            "message_id": message_id
            or getattr(ctx, "tool_call_id", None)
            or str(uuid.uuid4()),
        },
    )


@function_tool
async def chat_read_messages(
    ctx: RunContextWrapper[Any], conversation: str | None = None, after: int = 0
) -> dict:
    """Read intentionally published messages, not execution history. Page with the returned cursor."""
    return await _request(
        "GET",
        "/chat/messages",
        params={"conversation": _chat(ctx, conversation), "after": after},
    )


@function_tool
async def chat_list_conversations(ctx: RunContextWrapper[Any]) -> dict:
    """List this factory's registered public chats and their IDs. Does not start work."""
    return await _request("GET", "/chat/conversations")
