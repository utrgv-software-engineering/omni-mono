"""Unit tests for the MCP Apps UI side-channel.

Covers ``omniagents.core.agents.mcp_ui`` — extraction of UI resource blocks
from ``CallToolResult``, FIFO ordering of the per-tool deque, and the
``mcp_ui.record`` / ``mcp_ui.pop`` round-trip the bridge uses to attach
``metadata.mcp_ui`` to ``tool_result`` notifications.
"""

from __future__ import annotations

import pytest
from pydantic import AnyUrl

from omniagents.core.agents import mcp_ui


@pytest.fixture(autouse=True)
def _reset_side_channel():
    """Each test starts with an empty pending map."""
    mcp_ui.reset()
    yield
    mcp_ui.reset()


def _mcp_app_block(uri: str = "ui://demo/widget", html: str = "<h1>hi</h1>") -> dict:
    return {
        "type": "resource",
        "resource": {
            "uri": uri,
            "mimeType": "text/html;profile=mcp-app",
            "text": html,
        },
    }


def test_extract_ui_resources_detects_mcp_app_mime():
    result = {"content": [_mcp_app_block()]}
    blocks = mcp_ui.extract_ui_resources(result)
    assert len(blocks) == 1
    assert blocks[0]["resource"]["mimeType"] == "text/html;profile=mcp-app"


def test_extract_ui_resources_detects_legacy_ui_uri_without_profile():
    """Legacy mcp-ui resources may omit the ``profile=mcp-app`` mime suffix
    but always use the ``ui://`` URI scheme."""
    result = {
        "content": [
            {
                "type": "resource",
                "resource": {
                    "uri": "ui://legacy/widget",
                    "mimeType": "text/html",
                    "text": "<p>hi</p>",
                },
            }
        ]
    }
    blocks = mcp_ui.extract_ui_resources(result)
    assert len(blocks) == 1


def test_extract_ui_resources_detects_anyurl_uri():
    """The live MCP SDK delivers ``resource.uri`` as ``pydantic.AnyUrl``,
    not a plain ``str``. Regression guard for an earlier ``isinstance(uri, str)``
    check that silently dropped real-world payloads."""

    class _Res:
        def __init__(self):
            self.uri = AnyUrl("ui://demo/widget")
            self.mimeType = "text/html"

    class _Block:
        type = "resource"

        def __init__(self):
            self.resource = _Res()

    class _Result:
        content = [_Block()]

    blocks = mcp_ui.extract_ui_resources(_Result())
    assert len(blocks) == 1


def test_extract_ui_resources_ignores_text_blocks():
    result = {"content": [{"type": "text", "text": "plain output"}]}
    assert mcp_ui.extract_ui_resources(result) == []


def test_extract_ui_resources_ignores_non_html_resources():
    result = {
        "content": [
            {
                "type": "resource",
                "resource": {
                    "uri": "file:///tmp/data.json",
                    "mimeType": "application/json",
                    "text": "{}",
                },
            }
        ]
    }
    assert mcp_ui.extract_ui_resources(result) == []


def test_record_and_pop_round_trip():
    mcp_ui.record("demo-server", "showRawHtml", _mcp_app_block())
    payload = mcp_ui.pop("showRawHtml")
    assert payload is not None
    assert payload["server_name"] == "demo-server"
    assert payload["tool_name"] == "showRawHtml"
    assert payload["resource"]["resource"]["uri"] == "ui://demo/widget"


def test_pop_returns_none_when_empty():
    assert mcp_ui.pop("nope") is None


def test_pop_is_fifo_per_tool():
    mcp_ui.record("srv", "tool", _mcp_app_block(uri="ui://a"))
    mcp_ui.record("srv", "tool", _mcp_app_block(uri="ui://b"))
    first = mcp_ui.pop("tool")
    second = mcp_ui.pop("tool")
    third = mcp_ui.pop("tool")
    assert first and first["resource"]["resource"]["uri"] == "ui://a"
    assert second and second["resource"]["resource"]["uri"] == "ui://b"
    assert third is None


def test_record_caps_at_max_pending():
    """Unbounded growth would let a misbehaving tool exhaust memory."""
    cap = mcp_ui._MAX_PENDING_PER_KEY  # type: ignore[attr-defined]
    for i in range(cap + 5):
        mcp_ui.record("srv", "t", _mcp_app_block(uri=f"ui://{i}"))
    drained = []
    while True:
        item = mcp_ui.pop("t")
        if item is None:
            break
        drained.append(item)
    assert len(drained) == cap
    # Oldest items were dropped — the surviving window is the most recent ``cap``.
    assert drained[0]["resource"]["resource"]["uri"] == f"ui://{5}"


def test_reset_clears_pending():
    mcp_ui.record("srv", "t", _mcp_app_block())
    mcp_ui.reset()
    assert mcp_ui.pop("t") is None
