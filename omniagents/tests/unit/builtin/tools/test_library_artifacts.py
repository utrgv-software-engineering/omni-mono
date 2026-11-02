import json
from unittest.mock import AsyncMock
from agents.tool_context import ToolContext

import pytest

from omniagents.builtin.tools import library_artifacts as tools
from omniagents.core.tools import presentation


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
    monkeypatch.setattr(
        presentation, "emit_artifact", lambda *args: emitted.append(args)
    )
    result = await tools.artifact_show.on_invoke_tool(context, '{"artifact":"abc"}')
    assert result["id"] == "abc"
    title, content, mode, identity = emitted[0]
    assert (title, mode, identity) == ("Plan", "workplace_view", "artifact:abc")
    value = json.loads(content)
    assert value["kind"] == "artifact" and value["id"] == "abc"
    assert value["presentation_id"] and value["issued_at"] > 0


@pytest.mark.asyncio
async def test_canvas_show_checks_board_before_presenting(monkeypatch):
    monkeypatch.setattr(
        tools,
        "_request",
        AsyncMock(
            return_value={
                "id": "canvas1",
                "title": "Design",
                "boards": ["Home.dc.html"],
            }
        ),
    )
    emitted = []
    monkeypatch.setattr(
        presentation, "emit_artifact", lambda *args: emitted.append(args)
    )
    ctx = ToolContext(
        context={}, tool_name="canvas_show", tool_call_id="show", tool_arguments="{}"
    )
    await tools.canvas_show.on_invoke_tool(
        ctx, '{"canvas":"canvas1","board":"Home.dc.html"}'
    )
    assert json.loads(emitted[0][1])["board"] == "Home.dc.html"
    result = await tools.canvas_show.on_invoke_tool(
        ctx, '{"canvas":"canvas1","board":"Missing"}'
    )
    assert "error" in str(result).lower()
    assert len(emitted) == 1


@pytest.mark.asyncio
async def test_feedback_tool_reads_without_presenting_or_starting_work(monkeypatch):
    comments = {
        "comments": [{"body": "Use blue", "author": "Guest reviewer", "guest": True}]
    }
    request = AsyncMock(return_value=comments)
    monkeypatch.setattr(tools, "_request", request)
    ctx = ToolContext(
        context={},
        tool_name="artifact_comments",
        tool_call_id="comments",
        tool_arguments="{}",
    )
    result = await tools.artifact_comments.on_invoke_tool(ctx, '{"artifact":"abc"}')
    assert result == comments
    request.assert_awaited_once_with("GET", "/artifacts/abc/comments")
