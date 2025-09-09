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
from omniagents.core.agents.safe import SafeAgent
from omniagents.core.session import sm, Session, append_message
from omniagents.core.runtime.bridge import stream_agent
from omniagents.core.agents.builder import _default_build_agent
from omniagents.core.debug import Debug


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

    async def _request_tool_approval(self, tool_name: str, formatted_args: str, metadata: Optional[Dict[str, Any]] = None, _sess: Session | None = None):  # noqa: D401
        """Ask the client for approval; buffer request if not delivered."""

        approval_id = uuid.uuid4().hex
        payload = {
            "approval_id": approval_id,
            "tool": tool_name,
            "arguments": formatted_args,
        }
        
        # Add metadata if present (backward compatible)
        if metadata:
            payload["metadata"] = metadata

        if Debug.enabled():
            print(f"[srv]   → tool_approval_request approval_id={approval_id} details={payload}")
            
        delivered = await self._safe_notify(self.channel, "tool_approval_request", payload)
        if not delivered and _sess is not None:
            # Socket gone – remember so we can replay later
            _sess.pending_events.append(("tool_approval_request", payload))

        # Wait for user decision
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_approvals[approval_id] = fut
        return await fut  # -> (approved, always_approve, error_message)

    async def start_run(self, prompt: str, session_id: str | None = None, variables: dict | None = None, context: Any | None = None) -> dict:  # noqa: D401
        sess: Session = sm.get_or_create(session_id)
        
        # Store variables and/or context in session
        if variables is not None:
            sess.variables = variables
        if context is not None:
            sess.context = context

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
                approval_callback = lambda name, args, metadata=None: self._request_tool_approval(name, args, metadata, _sess=sess)
            
            # Build agent using spec's build_agent method
            from types import SimpleNamespace
            stub_app = SimpleNamespace(
                spec=self.spec,
                _request_tool_approval=approval_callback,
            )
            
            # Use the spec's build_agent method if available, otherwise use default
            if self.spec.build_agent:
                agent = await self.spec.build_agent({}, None, stub_app)
                
                # If safe mode is enabled and agent is not already a SafeAgent, wrap it
                if self.spec.use_safe_agent and approval_callback and not isinstance(agent, SafeAgent):
                    opts = self.spec.safe_agent_options
                    
                    agent = SafeAgent(
                        name=agent.name,
                        tools=agent.tools,
                        instructions=agent.instructions,
                        model=agent.model,
                        model_settings=getattr(agent, 'model_settings', None),
                        mcp_servers=getattr(agent, 'mcp_servers', []) or [],
                        handoffs=getattr(agent, 'handoffs', []),
                        handoff_description=getattr(agent, 'handoff_description', None),
                        prompt=getattr(agent, 'prompt', None),
                        approval_callback=approval_callback,
                        safe_tool_names=opts.safe_tool_names,
                        safe_tool_patterns=opts.safe_tool_patterns,
                        skip_approvals=opts.skip_approvals,
                        halt_on_rejection=opts.halt_on_rejection,
                    )
                
                sess.agent = agent
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
            end_reason = "error"  # Default in case of unexpected exception
            final_details: dict | None = None
            error_info: dict | None = None

            # Resolve runtime context once
            if hasattr(sess, 'context') and sess.context is not None:
                runtime_context = sess.context
            elif hasattr(sess, 'variables') and sess.variables is not None:
                if self.spec.build_context:
                    runtime_context = self.spec.build_context(sess.variables)
                else:
                    runtime_context = sess.variables
            else:
                runtime_context = self.spec.context

            # Retry policy (env configurable)
            import math
            max_retries = int(os.getenv("OMNIAGENTS_RETRY_MAX", "3"))
            base_delay = float(os.getenv("OMNIAGENTS_RETRY_BASE", "1.5"))

            def _is_retryable(ex: Exception) -> bool:
                msg = str(ex).lower()
                retry_keywords = (
                    "timeout",
                    "timed out",
                    "time out",
                    "rate limit",
                    "429",
                    "503",
                    "502",
                    "service unavailable",
                    "overloaded",
                    "temporarily",
                    "connection reset",
                    "read error",
                    "network",
                )
                return any(k in msg for k in retry_keywords)

            attempt = 0
            include_user_message = True
            try:
                while True:
                    try:
                        final_details = await stream_agent(
                            agent,
                            prompt,
                            sess,
                            self.channel,
                            max_turns=self.spec.max_turns,
                            context=runtime_context,
                            context_window=getattr(self.spec, "context_window", None),
                            include_user_message=include_user_message,
                        )
                        end_reason = "completed"
                        error_info = None
                        break
                    except asyncio.CancelledError:
                        end_reason = "cancelled"
                        raise
                    except Exception as e:
                        attempt += 1
                        # Decide to retry
                        if attempt <= max_retries and _is_retryable(e):
                            # Exponential backoff with jitter
                            delay = base_delay * (2 ** (attempt - 1))
                            # Announce retry status to client
                            try:
                                await self.channel.async_call(
                                    "run_status",
                                    args={
                                        "run_id": asyncio.current_task().get_name(),
                                        "status": "retrying",
                                        "message": str(e),
                                        "attempt": attempt,
                                        "total": max_retries,
                                        "next_delay": delay,
                                    },
                                )
                            except Exception:
                                pass
                            try:
                                await asyncio.sleep(delay)
                            except asyncio.CancelledError:
                                end_reason = "cancelled"
                                raise
                            # On retries, don't re-append the user message to history
                            include_user_message = False
                            continue
                        # Exhausted or not retryable
                        error_info = {"message": str(e), "type": e.__class__.__name__}
                        break
            finally:
                # Inform client the run finished
                payload = {
                    "run_id": asyncio.current_task().get_name(),
                    "end_reason": end_reason,
                }
                if error_info is not None:
                    payload["error"] = error_info
                if isinstance(final_details, dict):
                    if "usage" in final_details:
                        payload["usage"] = final_details["usage"]
                    if final_details.get("model") is not None:
                        payload["model"] = final_details.get("model")
                    if final_details.get("context_window") is not None:
                        payload["context_window"] = final_details.get("context_window")
                    if final_details.get("max_output_tokens") is not None:
                        payload["max_output_tokens"] = final_details.get("max_output_tokens")
                    if final_details.get("truncation") is not None:
                        payload["truncation"] = final_details.get("truncation")
                await self.channel.async_call("run_end", args=payload)

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

    # (Removed) get_model_info: model metadata is now sent inline with usage events
