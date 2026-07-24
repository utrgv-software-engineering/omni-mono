import asyncio
import json

import pytest
from agents.tool_context import ToolContext

from omniagents.builtin.tools.artifacts import display_artifact
from omniagents.core.tools.artifacts import emit_artifact, set_artifact_callback


@pytest.fixture(autouse=True)
def reset_artifact_callback():
    set_artifact_callback(None)
    yield
    set_artifact_callback(None)


def test_emit_artifact_callback():
    records = []
    set_artifact_callback(lambda t, c, m, aid: records.append((t, c, m, aid)))
    emit_artifact("title", "body", "markdown", artifact_id="id")
    assert records == [("title", "body", "markdown", "id")]


def test_display_artifact_tool():
    records = []
    set_artifact_callback(lambda t, c, m, aid: records.append((t, c, m, aid)))
    arguments = json.dumps(
        {
            "title": "a",
            "content": "b",
            "mode": "html",
            "artifact_id": "2",
        }
    )
    asyncio.run(
        display_artifact.on_invoke_tool(
            ToolContext(
                context=None,
                usage=None,
                tool_name="display_artifact",
                tool_call_id="test-call",
                tool_arguments=arguments,
            ),
            arguments,
        )
    )
    assert records == [("a", "b", "html", "2")]


def test_emit_artifact_without_callback():
    set_artifact_callback(None)
    emit_artifact("unused", "content")


def test_emit_artifact_callback_error(capsys):
    def callback(*_):
        raise ValueError("fail")

    set_artifact_callback(callback)
    emit_artifact("t", "c")
    captured = capsys.readouterr()
    assert "Error during artifact callback" in captured.out
