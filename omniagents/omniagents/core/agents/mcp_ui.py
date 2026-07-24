"""MCP Apps UI side-channel.

The OpenAI Agents SDK collapses ``CallToolResult.content`` into plain text /
image dicts before it reaches our notification bridge (see
``agents.mcp.util.invoke_mcp_tool``). That collapse drops the structured
``EmbeddedResource`` blocks an MCP Apps UI tool returns, so by the time the
bridge emits ``tool_result`` there is no way to tell that the tool produced
a UIResource.

To recover it we intercept ``MCPServer.call_tool`` at the wrapper layer
(``core/agents/mcp.py``) and stash any UI resource blocks in this module,
keyed by ``(server_name, raw_tool_name)``. The bridge then looks up the
public (prefixed) tool name on the streamed event, pops the matching UI
payload, and attaches it to the ``tool_result`` notification's ``metadata``
field — which the launcher already forwards to the chat machine.

The side-channel is process-local and uses a small FIFO per key so two
concurrent invocations of the same tool stay ordered.
"""

from __future__ import annotations

from collections import deque
from threading import Lock
from typing import Any, Deque, Dict, List, Optional

# MCP Apps content type for HTML UI resources. See
# https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/draft/apps.mdx
UI_RESOURCE_MIME = "text/html;profile=mcp-app"

# Bounded FIFO so a runaway tool can't grow the deque unboundedly.
_MAX_PENDING_PER_KEY = 32

_lock = Lock()
# Keyed by raw tool name — matches what the bridge sees in ``tool_result``
# when ``mcp_config.include_server_in_tool_names`` is False (the SDK default).
# Each pending entry carries its ``server_name`` so the launcher can route
# postMessage actions back to the right server.
_pending: Dict[str, Deque[Dict[str, Any]]] = {}

# ``ToolCallOutputItem`` from the Agents SDK exposes ``call_id`` but no
# ``name`` — the bridge has to recover the tool name via the prior
# ``tool_call_item`` event, which does carry it via ``to_input_item()``.
# This module-level map memoizes that lookup. Capped to avoid unbounded
# growth across long sessions.
_MAX_CALL_ID_ENTRIES = 1024
_call_id_to_tool: Dict[str, str] = {}

# MCP Apps `_meta.ui.resourceUri` cache — captured when an MCP server's
# `list_tools()` is called, used at `call_tool` time to know whether the
# tool's UI lives at a separate resource URI (Prefab/FastMCP pattern)
# rather than being inlined in the response (mcp-ui demo pattern).
# Keyed by raw tool name; value carries `server_name` + `resource_uri`.
_tool_ui_resource: Dict[str, Dict[str, str]] = {}


def _is_ui_resource_block(content_item: Any) -> bool:
    """Return True for content blocks that carry an MCP Apps UI resource.

    Matches the MCP Apps mime type ``text/html;profile=mcp-app`` and also
    legacy ``ui://`` URIs (the original mcp-ui scheme, which the MCP Apps
    spec subsumes).
    """
    if content_item is None:
        return False
    # Pydantic model or dict — both expose ``type`` / ``resource``.
    block_type = getattr(content_item, "type", None) or (
        content_item.get("type") if isinstance(content_item, dict) else None
    )
    if block_type != "resource":
        return False
    resource = getattr(content_item, "resource", None)
    if resource is None and isinstance(content_item, dict):
        resource = content_item.get("resource")
    if resource is None:
        return False
    mime = getattr(resource, "mimeType", None) or (
        resource.get("mimeType") if isinstance(resource, dict) else None
    )
    uri = getattr(resource, "uri", None) or (
        resource.get("uri") if isinstance(resource, dict) else None
    )
    # ``uri`` is ``pydantic.AnyUrl`` (not a str subclass) on Pydantic v2,
    # so coerce both fields before substring checks.
    mime_str = str(mime) if mime is not None else ""
    uri_str = str(uri) if uri is not None else ""
    if "profile=mcp-app" in mime_str.replace(" ", ""):
        return True
    if uri_str.startswith("ui://"):
        return True
    return False


def _serialize_block(content_item: Any) -> Dict[str, Any]:
    """Convert a CallToolResult content block to a JSON-safe dict."""
    if hasattr(content_item, "model_dump"):
        try:
            return content_item.model_dump(mode="json", by_alias=True)
        except Exception:
            pass
    if hasattr(content_item, "dict"):
        try:
            return content_item.dict()
        except Exception:
            pass
    if isinstance(content_item, dict):
        return dict(content_item)
    return {"raw": str(content_item)}


def extract_ui_resources(call_tool_result: Any) -> List[Dict[str, Any]]:
    """Return UI resource blocks from a ``CallToolResult``, or [] if none."""
    content = getattr(call_tool_result, "content", None)
    if content is None and isinstance(call_tool_result, dict):
        content = call_tool_result.get("content")
    if not content:
        return []
    out: List[Dict[str, Any]] = []
    for item in content:
        if _is_ui_resource_block(item):
            out.append(_serialize_block(item))
    return out


def record(server_name: str, tool_name: str, ui_resource: Dict[str, Any]) -> None:
    """Stash a UI resource block for later pickup by the bridge.

    ``server_name`` is preserved in the payload so the launcher can route
    follow-up postMessage actions (``tools/call``, ``resources/read``)
    back to the originating server.
    """
    payload = {
        "server_name": server_name,
        "tool_name": tool_name,
        "resource": ui_resource,
    }
    with _lock:
        dq = _pending.get(tool_name)
        if dq is None:
            dq = deque(maxlen=_MAX_PENDING_PER_KEY)
            _pending[tool_name] = dq
        dq.append(payload)


def pop(tool_name: str) -> Optional[Dict[str, Any]]:
    """Pop the oldest pending UI payload for ``tool_name``, or None.

    The returned dict has the shape::

        {
            "server_name": str,
            "tool_name": str,
            "resource": {  # serialized MCP EmbeddedResource block
                "type": "resource",
                "resource": {"uri", "mimeType", "text" | "blob", ...},
                ...
            },
        }
    """
    with _lock:
        dq = _pending.get(tool_name)
        if not dq:
            return None
        try:
            return dq.popleft()
        except IndexError:
            return None


def remember_call(call_id: str, tool_name: str) -> None:
    """Memoize the tool name for a given ``call_id`` (seen on ``tool_called``).

    Bounded by ``_MAX_CALL_ID_ENTRIES`` — when full, evicts the oldest
    insertion. Concurrent agent runs in one process are unusual but we
    still want to avoid leaking memory if it happens.
    """
    if not call_id or not tool_name:
        return
    with _lock:
        if len(_call_id_to_tool) >= _MAX_CALL_ID_ENTRIES:
            # ``dict`` preserves insertion order — pop the oldest.
            try:
                _call_id_to_tool.pop(next(iter(_call_id_to_tool)))
            except StopIteration:
                pass
        _call_id_to_tool[call_id] = tool_name


def lookup_tool_name(call_id: str) -> Optional[str]:
    if not call_id:
        return None
    with _lock:
        return _call_id_to_tool.get(call_id)


def register_tool_ui_resource(
    server_name: str, tool_name: str, resource_uri: str
) -> None:
    """Cache a tool's MCP-Apps ``_meta.ui.resourceUri`` for later lookup.

    Called from the ``list_tools`` wrapper in ``core/agents/mcp.py`` so
    the side-channel knows which tools use the resource-URI flavor of
    MCP Apps (Prefab / FastMCP) versus the inline-content flavor (the
    mcp-ui demo). When the wrapper sees a ``call_tool`` for such a tool,
    it stashes the URI plus the result's ``structuredContent`` instead
    of inline HTML.
    """
    if not server_name or not tool_name or not resource_uri:
        return
    with _lock:
        _tool_ui_resource[tool_name] = {
            "server_name": server_name,
            "resource_uri": resource_uri,
        }


def lookup_tool_ui_resource(tool_name: str) -> Optional[Dict[str, str]]:
    if not tool_name:
        return None
    with _lock:
        entry = _tool_ui_resource.get(tool_name)
        return dict(entry) if entry else None


def record_resource_uri_payload(
    server_name: str,
    tool_name: str,
    resource_uri: str,
    structured_content: Any,
) -> None:
    """Stash a resource-URI-style UI payload (FastMCP / Prefab pattern).

    The bridge pops this in lieu of (or alongside) inline HTML. The
    host fetches the renderer HTML via ``mcp.read_resource(resource_uri)``
    and uses ``structured_content`` as the AppRenderer ``toolResult``.
    """
    payload = {
        "server_name": server_name,
        "tool_name": tool_name,
        "resource_uri": resource_uri,
        "structured_content": structured_content,
    }
    with _lock:
        dq = _pending.get(tool_name)
        if dq is None:
            dq = deque(maxlen=_MAX_PENDING_PER_KEY)
            _pending[tool_name] = dq
        dq.append(payload)


def reset() -> None:
    """Clear all pending state. Used between agent runs / tests."""
    with _lock:
        _pending.clear()
        _call_id_to_tool.clear()
        _tool_ui_resource.clear()
