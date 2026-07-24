import types

import pytest

from omniagents.core.agents.approval import (
    _make_mcp_predicate,
    session_allows_mcp_tool,
)
from omniagents.core.agents.mcp_identity import (
    lookup_identity,
    stamp_mcp_tool_identities,
)


def _mcp_tool(public_name: str, server_name: str):
    return types.SimpleNamespace(
        name=public_name,
        tool_origin=types.SimpleNamespace(type="mcp", mcp_server_name=server_name),
    )


def _plain_tool(name: str):
    return types.SimpleNamespace(name=name, tool_origin=None)


def _server(name: str, tool_names: list[str] | None):
    async def list_tools(run_context, agent):
        if tool_names is None:
            raise RuntimeError("listing failed")
        return [types.SimpleNamespace(name=n) for n in tool_names]

    return types.SimpleNamespace(name=name, list_tools=list_tools)


def _session():
    return types.SimpleNamespace()


@pytest.mark.asyncio
async def test_stamp_pairs_tools_across_servers_with_overlapping_names():
    """Two servers exposing the same original tool name get distinct tuples."""
    alpha = _server("alpha", ["create_page", "list_items"])
    beta = _server("beta", ["create_page"])
    agent = types.SimpleNamespace(mcp_servers=[alpha, beta])

    tools = [
        _plain_tool("read_file"),
        _mcp_tool("mcp_alpha__create_page", "alpha"),
        _mcp_tool("mcp_alpha__list_items", "alpha"),
        _mcp_tool("mcp_beta__create_page", "beta"),
    ]
    session = _session()

    await stamp_mcp_tool_identities(agent, None, tools, session)

    assert lookup_identity(session, "mcp_alpha__create_page") == (
        "alpha",
        "create_page",
    )
    assert lookup_identity(session, "mcp_alpha__list_items") == ("alpha", "list_items")
    assert lookup_identity(session, "mcp_beta__create_page") == ("beta", "create_page")
    assert lookup_identity(session, "read_file") is None
    assert tools[1]._mcp_identity == ("alpha", "create_page")
    assert not hasattr(tools[0], "_mcp_identity")


@pytest.mark.asyncio
async def test_stamp_falls_back_to_public_name_on_length_mismatch():
    """A dynamic server whose listing drifted still yields stable keys."""
    drifty = _server("drifty", ["only_one_now"])
    agent = types.SimpleNamespace(mcp_servers=[drifty])
    tools = [
        _mcp_tool("mcp_drifty__tool_a", "drifty"),
        _mcp_tool("mcp_drifty__tool_b", "drifty"),
    ]
    session = _session()

    await stamp_mcp_tool_identities(agent, None, tools, session)

    assert lookup_identity(session, "mcp_drifty__tool_a") == (
        "drifty",
        "mcp_drifty__tool_a",
    )
    assert lookup_identity(session, "mcp_drifty__tool_b") == (
        "drifty",
        "mcp_drifty__tool_b",
    )


@pytest.mark.asyncio
async def test_stamp_falls_back_when_listing_raises():
    broken = _server("broken", None)
    agent = types.SimpleNamespace(mcp_servers=[broken])
    tools = [_mcp_tool("mcp_broken__x", "broken")]
    session = _session()

    await stamp_mcp_tool_identities(agent, None, tools, session)

    assert lookup_identity(session, "mcp_broken__x") == ("broken", "mcp_broken__x")


@pytest.mark.asyncio
async def test_stamp_fast_path_skips_relisting_once_mapped():
    calls = {"n": 0}

    async def list_tools(run_context, agent):
        calls["n"] += 1
        return [types.SimpleNamespace(name="hello")]

    server = types.SimpleNamespace(name="srv", list_tools=list_tools)
    agent = types.SimpleNamespace(mcp_servers=[server])
    tools = [_mcp_tool("mcp_srv__hello", "srv")]
    session = _session()

    await stamp_mcp_tool_identities(agent, None, tools, session)
    await stamp_mcp_tool_identities(agent, None, tools, session)

    assert calls["n"] == 1
    assert tools[0]._mcp_identity == ("srv", "hello")


def test_session_allows_mcp_tool_normalizes_and_isolates():
    session = types.SimpleNamespace(always_allow_mcp_tools={("alpha", "create_page")})
    assert session_allows_mcp_tool(session, "alpha", "create_page")
    assert session_allows_mcp_tool(session, " alpha ", "create_page ")
    # Same tool name, different server: NOT covered.
    assert not session_allows_mcp_tool(session, "beta", "create_page")
    assert not session_allows_mcp_tool(session, "", "create_page")
    assert not session_allows_mcp_tool(types.SimpleNamespace(), "alpha", "create_page")


@pytest.mark.asyncio
async def test_mcp_predicate_checks_tuple_not_flat_name():
    session = types.SimpleNamespace(
        always_allow_mcp_tools={("alpha", "create_page")},
        # A flat-name grant must NOT leak into MCP tools.
        always_allow_tools={"mcp_beta__create_page"},
    )

    allowed = _make_mcp_predicate(
        ("alpha", "create_page"), "mcp_alpha__create_page", session, set(), [], False
    )
    assert await allowed(None, {}, "c1") is False  # granted via tuple

    other_server = _make_mcp_predicate(
        ("beta", "create_page"), "mcp_beta__create_page", session, set(), [], False
    )
    assert await other_server(None, {}, "c2") is True  # still prompts


@pytest.mark.asyncio
async def test_mcp_predicate_spec_names_and_patterns_match_public_name():
    session = types.SimpleNamespace()
    import re

    by_name = _make_mcp_predicate(
        ("srv", "hello"), "mcp_srv__hello", session, {"mcp_srv__hello"}, [], False
    )
    assert await by_name(None, {}, "c1") is False

    by_pattern = _make_mcp_predicate(
        ("srv", "hello"), "mcp_srv__hello", session, set(), [re.compile(r".*")], False
    )
    assert await by_pattern(None, {}, "c2") is False

    default = _make_mcp_predicate(
        ("srv", "hello"), "mcp_srv__hello", session, set(), [], False
    )
    assert await default(None, {}, "c3") is True
