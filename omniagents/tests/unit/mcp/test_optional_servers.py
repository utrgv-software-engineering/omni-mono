"""An optional MCP server is left out when the environment has no URL for it."""

from types import SimpleNamespace

from omniagents.core.agents.mcp import build_mcp_servers, is_absent_optional
from omniagents.core.agents.specs import MCPServerConfig


def _spec(*servers):
    return SimpleNamespace(mcp_servers=list(servers))


def test_an_optional_server_without_a_url_is_left_out():
    canvas = MCPServerConfig(
        name="canvas",
        type="streamable_http",
        params={"url": ""},
        options={"optional": True},
    )
    github = MCPServerConfig(
        name="github", type="streamable_http", params={"url": "http://127.0.0.1:1/mcp"}
    )
    assert is_absent_optional(canvas, canvas.params)
    assert [s.name for s in build_mcp_servers(_spec(canvas, github), {})] == ["github"]
    present = MCPServerConfig(
        name="canvas",
        type="streamable_http",
        params={"url": "http://127.0.0.1:1/canvas/mcp"},
        options={"optional": True},
    )
    assert [s.name for s in build_mcp_servers(_spec(present), {})] == ["canvas"]


def test_a_required_server_without_a_url_is_still_an_error():
    import pytest

    bare = MCPServerConfig(name="canvas", type="streamable_http", params={"url": ""})
    with pytest.raises(ValueError, match="requires a url"):
        build_mcp_servers(_spec(bare), {})
