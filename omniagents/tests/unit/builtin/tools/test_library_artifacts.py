import json
from unittest.mock import AsyncMock
from agents.tool_context import ToolContext

import pytest

from omniagents.builtin.tools import library_artifacts as tools


@pytest.mark.asyncio
async def test_create_returns_writable_location_and_show_emits_existing_chat_artifact(
    monkeypatch,
):
    item = dict(
        id="abc",
        title="Plan",
        kind="document",
        folder="/workspace/Library/abc",
        main="index.md",
        url="https://workplace.test/library/abc",
        markdown="# Plan\nDo this next.",
    )
    request = AsyncMock(return_value=item)
    monkeypatch.setattr(tools, "_request", request)
    context = ToolContext(
        context={},
        tool_name="artifact_create",
        tool_call_id="create",
        tool_arguments="{}",
    )
    result = await tools.artifact_create.on_invoke_tool(
        context, json.dumps({"title": "Plan", "kind": "document"})
    )
    # Function tools return the original structured result, before RPC serialization.
    assert result["folder"] == "/workspace/Library/abc"
    request.assert_awaited_with(
        "POST", "/artifacts", json={"title": "Plan", "kind": "document"}
    )
    emitted = []
    monkeypatch.setattr(tools, "emit_artifact", lambda *args: emitted.append(args))
    result = await tools.artifact_show.on_invoke_tool(context, '{"artifact":"abc"}')
    assert result["id"] == "abc"
    assert emitted == [
        (
            "Plan",
            "# Plan\nDo this next.\n\n[Open in Library](<https://workplace.test/library/abc>)",
            "markdown",
            "library:abc",
        )
    ]
