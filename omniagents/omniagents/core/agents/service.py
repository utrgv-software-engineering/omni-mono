"""
Centralized AgentService for handling all agent-related JSON-RPC operations.

This service provides a unified interface for agent operations across different
execution modes (CLI, Flet, Server), exposing methods via JSON-RPC protocol.
"""

import os
import asyncio
import uuid
from types import SimpleNamespace
from typing import Optional, Dict, Any
import inspect

from omniagents.rpc import RpcMethodsBase
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.session import sm, Session, append_message
from omniagents.core.runtime.bridge import stream_agent
from omniagents.core.agents.builder import _default_build_agent


# Optional verbose logging controlled by env var
DEBUG_SERVER = os.getenv("OMNIAGENTS_SERVER_DEBUG", "0") in ("1", "true", "True")


def _debug_rpc(method: str, payload: dict | None = None) -> None:
    """Print *method* and *payload* when ``OMNIAGENTS_SERVER_DEBUG`` is set."""
    if DEBUG_SERVER:
        if payload is not None:
            print(f"[srv] ★ {method} payload={payload!r}")
        else:
            print(f"[srv] ★ {method}")


class AgentService(RpcMethodsBase):
    """JSON-RPC methods for agent operations.
    
    This service handles:
    - Starting and stopping agent runs
    - Managing conversation history
    - Session management (list, archive)
    - Tool approval workflows
    - Event broadcasting to multiple channels
    """

    def __init__(self, spec: AgentSpec) -> None:
        """Initialize AgentService with an agent specification.
        
        Args:
            spec: The agent specification containing configuration,
                  instructions, tools, and other settings.
        """
        super().__init__()
        self.spec = spec
        # Tool-approval request / response helper state
        self._pending_approvals: dict[str, asyncio.Future] = {}

    # ------------------------------------------------------------------
    #  Helper: best-effort async_call that tolerates disconnected sockets.
    #  Returns True if the notification was delivered, False if the WebSocket
    #  had already been closed.
    # ------------------------------------------------------------------

    async def _safe_notify(self, channel, method: str, payload: dict) -> bool:  # noqa: D401
        try:
            await channel.async_call(method, args=payload)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    #  RPC method invoked by the *client* when the user answers the
    #  approval prompt.  Resolves the Future we saved when we asked.
    # ------------------------------------------------------------------

    async def tool_approval_response(
        self,
        approval_id: str,
        approved: bool,
        always_approve: bool = False,
        error_message: str | None = None,
    ) -> bool:  # noqa: D401
        _debug_rpc(
            "tool_approval_response",
            {
                "approval_id": approval_id,
                "approved": approved,
                "always_approve": always_approve,
                "error_message": error_message,
            },
        )
        fut = self._pending_approvals.pop(approval_id, None)
        if fut and not fut.done():
            fut.set_result((approved, always_approve, error_message))
        return True

    # ------------------------------------------------------------------
    #  Callback passed to SafeAgent: emits a *request* to the client and
    #  waits for the matching response.
    # ------------------------------------------------------------------

    async def _request_tool_approval(self, tool_name: str, formatted_args: str, _sess: Session | None = None):  # noqa: D401
        """Ask the client for approval; buffer request if not delivered."""

        approval_id = uuid.uuid4().hex
        payload = {
            "approval_id": approval_id,
            "tool": tool_name,
            "arguments": formatted_args,
        }

        delivered = await self._safe_notify(self.channel, "tool_approval_request", payload)
        if not delivered and _sess is not None:
            # Socket gone – remember so we can replay later
            _sess.pending_events.append(("tool_approval_request", payload))

        # Wait for user decision
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_approvals[approval_id] = fut
        return await fut  # -> (approved, always_approve, error_message)

    async def start_run(self, prompt: str, session_id: str | None = None, variables: dict | None = None) -> dict:  # noqa: D401
        sess: Session = sm.get_or_create(session_id)
        
        # Store variables in session for context construction
        if variables:
            sess.variables = variables

        # Track current channel for real-time notifications.
        if self.channel not in sess.channels:
            sess.channels.append(self.channel)

        if sess.current_task and not sess.current_task.done():
            raise RuntimeError("Run already active in this session")

        # --------------------------------------------------------------
        # Flush any events that were generated while the client was
        # disconnected (e.g. pending approval requests).
        # --------------------------------------------------------------

        if sess.pending_events:
            if DEBUG_SERVER:
                print(f"[srv]  ↻ replay {len(sess.pending_events)} queued events for {sess.id[:8]}")
            still_pending: list[tuple[str, dict]] = []
            for (meth, payl) in list(sess.pending_events):
                delivered_any = False
                # Try every channel currently known for the session
                for ch in list(sess.channels):
                    if await self._safe_notify(ch, meth, payl):
                        delivered_any = True
                if not delivered_any:
                    still_pending.append((meth, payl))
            sess.pending_events = still_pending

        # Build (or reuse) agent for this session
        if not hasattr(sess, "agent"):
            # Create approval callback that captures session context
            approval_callback = None
            if self.spec.use_safe_agent:
                approval_callback = lambda name, args: self._request_tool_approval(name, args, _sess=sess)
            
            # Build agent using spec's build_agent method
            from types import SimpleNamespace
            stub_app = SimpleNamespace(
                spec=self.spec,
                _request_tool_approval=approval_callback,
            )
            
            # Use the spec's build_agent method if available, otherwise use default
            if self.spec.build_agent:
                sess.agent = await self.spec.build_agent({}, None, stub_app)
            else:
                from omniagents.core.agents.builder import _default_build_agent
                sess.agent = await _default_build_agent(
                    {},  # Server doesn't use settings from UI
                    None,  # Server doesn't support MCP yet
                    self.spec,
                    approval_callback
                )
        
        agent = sess.agent

        async def _worker():
            try:
                # Build context from variables if provided
                if hasattr(sess, 'variables') and sess.variables:
                    # Use factory if available, otherwise use variables as context
                    if self.spec.build_context:
                        runtime_context = self.spec.build_context(sess.variables)
                    else:
                        runtime_context = sess.variables
                else:
                    # Fall back to spec context
                    runtime_context = self.spec.context
                await stream_agent(agent, prompt, sess, self.channel, max_turns=self.spec.max_turns, context=runtime_context)
            finally:
                # Inform client the run finished
                await self.channel.async_call(
                    "run_end", args={"run_id": asyncio.current_task().get_name(), "end_reason": "completed"}
                )

        task_name = f"run_{uuid.uuid4().hex}"
        _debug_rpc(
            "start_run",
            {
                "prompt": prompt,
                "session_id_in": session_id,
                "session_id": sess.id,
                "run_id": task_name,
            },
        )
        task = asyncio.create_task(_worker(), name=task_name)
        sm.bind_task(task, sess)
        return {"run_id": task_name, "session_id": sess.id}

    async def stop_run(self, run_id: str) -> bool:  # noqa: D401
        _debug_rpc("stop_run", {"run_id": run_id})
        return sm.cancel(run_id)

    async def send_user_message(self, run_id: str, content: str) -> None:  # noqa: D401
        _debug_rpc("send_user_message", {"run_id": run_id, "content": content})
        sess = sm.get_by_run_id(run_id)
        if not sess:
            raise RuntimeError("Unknown run_id")
        msg = {"role": "user", "content": content}
        sess.history.append(msg)
        append_message(sess.id, msg)
        # No immediate return value; next assistant chunk will arrive via stream.

    # ------------------------------------------------------------------
    #  NEW: expose conversation history to clients ---------------------
    # ------------------------------------------------------------------
    async def get_session_history(self, session_id: str) -> list[dict]:  # noqa: D401
        """Return JSON-serialisable history for *session_id*."""
        _debug_rpc("get_session_history", {"session_id": session_id})
        sess: Session = sm.get_or_create(session_id)

        # Track this Rpc channel too – allows further live events while
        # a run is still streaming.
        if self.channel not in sess.channels:
            sess.channels.append(self.channel)

        # First, replay any queued events (e.g. unfinished approval
        # requests) so the reconnecting client sees the current state
        # before we hand back the static history.
        if sess.pending_events:
            still_pending: list[tuple[str, dict]] = []
            for (meth, payl) in list(sess.pending_events):
                delivered_any = False
                # Try every channel currently known for the session
                for ch in list(sess.channels):
                    if await self._safe_notify(ch, meth, payl):
                        delivered_any = True
                if not delivered_any:
                    still_pending.append((meth, payl))
            sess.pending_events = still_pending

        # Return the raw history list; the WebSocket RPC layer will
        # serialise this to JSON for the client.
        result = sess.history
        _debug_rpc("get_session_history result", {"history": result})
        return result

    async def list_sessions(self) -> list[dict]:  # noqa: D401
        """Return metadata for all sessions."""
        result = sm.list_sessions()
        _debug_rpc("list_sessions", {"result": result})
        return result

    async def archive_session(self, session_id: str) -> bool:  # noqa: D401
        """Archive *session_id* so clients can hide it."""
        _debug_rpc("archive_session", {"session_id": session_id})
        return sm.archive_session(session_id)