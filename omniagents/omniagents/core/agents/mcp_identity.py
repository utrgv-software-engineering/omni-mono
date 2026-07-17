"""(server, tool) identity for MCP-derived function tools.

With ``include_server_in_tool_names`` enabled, the SDK gives every
MCP-derived FunctionTool a deterministic prefixed public name
(``mcp_<server>__<tool>``). That flat name is the *serialization* of the
tool's identity at the model boundary — the canonical identity is the
``(server_name, original_tool_name)`` tuple, and nothing in the trust
path (approval grants, allow-lists) may recover it by parsing the name.

This module builds the forward map instead: ``MCPUtil`` converts each
server's ``list_tools()`` batch to FunctionTools index-for-index and
iterates ``agent.mcp_servers`` in order, so grouping the converted tools
by their ``tool_origin.mcp_server_name`` (in encounter order) and zipping
against a fresh ``list_tools()`` recovers every pairing without touching
the name encoding. Unpairable batches (length drift from a dynamic
server, duplicate server names) fall back to ``(server, public_name)`` —
grant keys must be *stable*; pretty is optional.

The map lives on the session (``session.mcp_tool_identities``) for the
bridge/service to translate event payloads, and each tool is stamped with
``_mcp_identity`` so the approval predicate needs no lookup.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

McpToolIdentity = Tuple[str, str]


def _origin_server_name(tool: Any) -> str | None:
    """Return the MCP server name from a FunctionTool's origin, if any."""
    origin = getattr(tool, "tool_origin", None)
    if origin is None:
        return None
    otype = getattr(origin, "type", None)
    otype_value = getattr(otype, "value", otype)
    if otype_value != "mcp":
        return None
    server_name = getattr(origin, "mcp_server_name", None)
    return str(server_name) if server_name else None


def get_session_identities(session: Any) -> Dict[str, McpToolIdentity]:
    """Return (creating if needed) the session's public-name → identity map."""
    identities = getattr(session, "mcp_tool_identities", None)
    if identities is None:
        identities = {}
        try:
            setattr(session, "mcp_tool_identities", identities)
        except Exception:
            pass
    return identities


def lookup_identity(session: Any, public_name: str) -> McpToolIdentity | None:
    """Identity tuple for a public tool name, or None for non-MCP tools."""
    identities = getattr(session, "mcp_tool_identities", None)
    if not identities:
        return None
    return identities.get(public_name)


async def stamp_mcp_tool_identities(agent: Any, run_context: Any, tools: Any, session: Any) -> None:
    """Pair MCP-derived tools with their (server, original) identity.

    Stamps ``_mcp_identity`` on each MCP-origin tool and records the pair
    in ``session.mcp_tool_identities``. Idempotent and cheap on repeat
    calls: once every public name present is already mapped, no server is
    re-listed.
    """
    servers = list(getattr(agent, "mcp_servers", None) or [])
    if not servers:
        return

    by_server: Dict[str, List[Any]] = {}
    mcp_tools: List[Any] = []
    for tool in tools:
        server_name = _origin_server_name(tool)
        if server_name:
            by_server.setdefault(server_name, []).append(tool)
            mcp_tools.append(tool)
    if not mcp_tools:
        return

    identities = get_session_identities(session)

    # Fast path: everything already mapped — just (re)stamp the attribute.
    if all(t.name in identities for t in mcp_tools):
        for tool in mcp_tools:
            setattr(tool, "_mcp_identity", identities[tool.name])
        return

    seen_server_names: set[str] = set()
    for server in servers:
        server_name = str(getattr(server, "name", "") or "")
        converted = by_server.get(server_name)
        if not converted:
            continue
        if server_name in seen_server_names:
            # Duplicate server names make order-correlation ambiguous —
            # the grouped list already merged both servers' tools, so the
            # first pass handled (or fell back for) all of them.
            continue
        seen_server_names.add(server_name)

        originals: List[str] | None
        try:
            listed = await server.list_tools(run_context, agent)
            originals = [str(getattr(t, "name", "") or "") for t in (listed or [])]
        except Exception:
            logger.debug("mcp_identity: list_tools failed for %s", server_name, exc_info=True)
            originals = None

        if originals is None or len(originals) != len(converted) or not all(originals):
            # Stable-key fallback: the public name is deterministic and
            # unique, so it still keys grants correctly — just not pretty.
            logger.debug(
                "mcp_identity: falling back to public names for %s (%s listed vs %s converted)",
                server_name,
                len(originals) if originals is not None else "?",
                len(converted),
            )
            for tool in converted:
                identity = (server_name, tool.name)
                identities[tool.name] = identity
                setattr(tool, "_mcp_identity", identity)
            continue

        for tool, original_name in zip(converted, originals):
            identity = (server_name, original_name)
            identities[tool.name] = identity
            setattr(tool, "_mcp_identity", identity)
