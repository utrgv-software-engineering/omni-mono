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

Wiring point: the Agents SDK creates an MCP Client without callbacks and
then calls its low-level session directly. Install a context-local client
factory so legacy callbacks and modern input-required rounds both use the
owning session's user-input handler. Modern retries use MCP's own driver,
including its opaque request-state echo and round limit. Connections made
outside an elicitation scope do not advertise an input handler.
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
    """Install the context-aware SDK client factory once per process."""
    global _installed
    if _installed:
        return
    try:
        import agents.mcp.server as sdk_server
        from mcp import ClientSession
    except Exception:  # pragma: no cover - SDK not importable
        logger.debug("MCP elicitation wiring unavailable", exc_info=True)
        return

    if getattr(sdk_server, "MCP_V2", False):
        from mcp import Client

        class ElicitationClient(Client):
            async def __aenter__(self):
                await super().__aenter__()
                # Agents SDK calls the low-level session directly. Preserve its
                # call path while using MCP's own multi-round input driver.
                if self.session.discover_result is not None:
                    for name in ("call_tool", "read_resource", "get_prompt"):
                        original = getattr(self.session, name)

                        def wrap(method):
                            async def invoke(*args, **kwargs):
                                if kwargs.get("allow_input_required"):
                                    return await method(*args, **kwargs)
                                responses = kwargs.pop("input_responses", None)
                                state = kwargs.pop("request_state", None)
                                kwargs.pop("allow_input_required", None)

                                async def retry(responses, state):
                                    return await method(
                                        *args,
                                        **kwargs,
                                        input_responses=responses,
                                        request_state=state,
                                        allow_input_required=True,
                                    )

                                return await self._drive_input_required(
                                    await retry(responses, state), retry
                                )

                            return invoke

                        setattr(self.session, name, wrap(original))
                return self

        def create_client(transport, **kwargs):
            return ElicitationClient(
                transport,
                mode="auto",
                cache=None,
                elicitation_callback=_current_elicitation_callback.get(),
                **kwargs,
            )

        sdk_server.create_v2_client = create_client
        _installed = True
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
        schema = getattr(
            params, "requested_schema", getattr(params, "requestedSchema", None)
        )
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
