"""Verify ``core/runtime/bridge.py`` injects ``metadata.mcp_ui`` on
``tool_result`` when the MCP-Apps side-channel has a payload for the tool.
"""

from __future__ import annotations

import pytest

from omniagents.core.agents import mcp_ui
from omniagents.core.runtime.bridge import stream_event_payload


class _FakeOutputItem:
    """Stand-in for ``ToolCallOutputItem`` — bridge only reads ``type``,
    ``name``, ``output``, and ``to_input_item()``."""

    type = "tool_call_output_item"

    def __init__(self, name: str, output: str, call_id: str | None = None):
        self.name = name
        self.output = output
        self._call_id = call_id

    def to_input_item(self):
        return {"name": self.name, "call_id": self._call_id}


@pytest.fixture(autouse=True)
def _reset():
    mcp_ui.reset()
    yield
    mcp_ui.reset()


def test_tool_result_carries_mcp_ui_when_recorded():
    mcp_ui.record(
        "demo-server",
        "showRawHtml",
        {
            "type": "resource",
            "resource": {
                "uri": "ui://demo/raw-html",
                "mimeType": "text/html;profile=mcp-app",
                "text": "<h1>hi</h1>",
            },
        },
    )
    item = _FakeOutputItem("showRawHtml", "rendered", call_id="call-1")

    method, payload = stream_event_payload(item, "run-x", session_id="sess-y")

    assert method == "tool_result"
    meta = payload.get("metadata") or {}
    assert meta.get("display_type") == "mcp_ui"
    mcp_ui_meta = meta.get("mcp_ui") or {}
    assert mcp_ui_meta.get("server_name") == "demo-server"
    assert mcp_ui_meta.get("tool_name") == "showRawHtml"
    assert mcp_ui_meta["resource"]["resource"]["text"] == "<h1>hi</h1>"
    # The pop is one-shot — second event for the same tool should not
    # re-attach the same UI payload.
    second_item = _FakeOutputItem("showRawHtml", "later", call_id="call-2")
    _, second_payload = stream_event_payload(second_item, "run-x", session_id="sess-y")
    assert "metadata" not in second_payload


def test_tool_result_omits_mcp_ui_when_no_payload():
    item = _FakeOutputItem("plain_tool", "ok", call_id="call-1")
    method, payload = stream_event_payload(item, "run-x", session_id="sess-y")
    assert method == "tool_result"
    assert "metadata" not in payload
