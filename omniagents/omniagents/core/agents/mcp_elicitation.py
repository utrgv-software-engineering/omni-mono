"""Route MCP server-initiated elicitations through the #308 protocol.

MCP servers may ask the user for structured input mid-tool-call
(``elicitation/create``). The GUI protocol already has exactly one lane
for that — the structured user-elicitation protocol (tkt_F9tu2m9PjdpY,
``request_elicitation`` / ``elicitation_requested`` /
``elicitation_response`` / ``elicitation_resolved``) — so this module
wires MCP's requests INTO that lane instead of inventing a parallel one:

* an MCP form request (``message`` + ``requestedSchema``) becomes a
  ``form``-kind elicitation with ``source="mcp:<server>"``,
* the first client response maps back to the MCP ``ElicitResult``
  (``accept``+content / ``decline`` / ``cancel``).

Wiring point: the openai-agents SDK constructs ``mcp.ClientSession``
inside ``connect()`` without an ``elicitation_callback``, and the MCP
client only *advertises* the elicitation capability when the callback is
present at ``initialize()`` time. We therefore install (once, process
wide) a ``ClientSession`` subclass into the SDK module that pulls the
callback from a :class:`~contextvars.ContextVar` at construction — the
session MCP lifecycle task sets that var per server before entering it,
so only session-scoped connections advertise and receive elicitations;
management probes and out-of-session connects keep the default
"not supported" behavior.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Awaitable, Callable, Iterator, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "elicitation_scope",
    "install_client_session_elicitation",
    "make_mcp_elicitation_handler",
]

#: The elicitation callback the *current task* wants new ClientSessions
#: to carry (None = default: elicitation unsupported).
_current_elicitation_callback: ContextVar[Optional[Any]] = ContextVar(
    "omniagents_mcp_elicitation_callback", default=None
)

_installed = False


def install_client_session_elicitation() -> None:
    """Install the ContextVar-aware ``ClientSession`` into the SDK.

    Idempotent and behavior-preserving: when no scope is active the
    subclass is byte-for-byte the stock ``ClientSession``.
    """
    global _installed
    if _installed:
        return
    try:
        import agents.mcp.server as sdk_server
        from mcp import ClientSession
    except Exception:  # pragma: no cover - SDK not importable
        logger.debug("MCP elicitation wiring unavailable", exc_info=True)
        return

    if getattr(sdk_server.ClientSession, "_omni_elicitation_aware", False):
        _installed = True
        return

    class _ElicitationAwareClientSession(ClientSession):  # type: ignore[misc]
        _omni_elicitation_aware = True

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            if kwargs.get("elicitation_callback") is None:
                callback = _current_elicitation_callback.get()
                if callback is not None:
                    kwargs["elicitation_callback"] = callback
            super().__init__(*args, **kwargs)

    sdk_server.ClientSession = _ElicitationAwareClientSession  # type: ignore[misc]
    _installed = True


@contextmanager
def elicitation_scope(callback: Any) -> Iterator[None]:
    """Make *callback* the elicitation handler for sessions created here."""
    install_client_session_elicitation()
    token = _current_elicitation_callback.set(callback)
    try:
        yield
    finally:
        _current_elicitation_callback.reset(token)


def make_mcp_elicitation_handler(
    request_elicitation: Callable[..., Awaitable[dict]],
    *,
    server_name: str,
) -> Any:
    """Build the MCP ``ElicitationFnT`` bridging into #308's lane.

    ``request_elicitation`` is ``AgentService.request_elicitation``
    pre-bound to the owning session (a ``functools.partial`` closing over
    ``session=``). Responses are validated by the elicitation protocol
    itself (schema-checked ``accept`` values), so the MCP server receives
    contract-clean content.
    """

    async def handle_elicitation(context: Any, params: Any) -> Any:
        from mcp import types as mcp_types

        message = str(getattr(params, "message", "") or "")
        schema = getattr(params, "requestedSchema", None)
        if schema is not None and hasattr(schema, "model_dump"):
            try:
                schema = schema.model_dump(mode="json", by_alias=True)
            except Exception:
                schema = None
        if schema is not None and not isinstance(schema, dict):
            schema = None
        try:
            result = await request_elicitation(
                kind="form",
                message=message or f"{server_name} requests input",
                input_schema=schema,
                source=f"mcp:{server_name}",
                persist_response=False,
            )
        except Exception:
            logger.debug(
                "MCP elicitation for %s failed; declining",
                server_name,
                exc_info=True,
            )
            return mcp_types.ElicitResult(action="decline")
        action = str(result.get("action") or "decline")
        if action == "accept":
            value = result.get("value")
            return mcp_types.ElicitResult(
                action="accept",
                content=value if isinstance(value, dict) else {},
            )
        if action == "decline":
            return mcp_types.ElicitResult(action="decline")
        # cancel / expire / anything terminal-unanswered
        return mcp_types.ElicitResult(action="cancel")

    return handle_elicitation
