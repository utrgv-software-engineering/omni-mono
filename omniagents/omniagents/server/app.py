from __future__ import annotations

import os
import asyncio
import uuid
from types import SimpleNamespace
from typing import Optional
import json  # local to avoid top-level dep

from fastapi import FastAPI, WebSocket, Depends, HTTPException, status
from fastapi_websocket_rpc import RpcMethodsBase, WebsocketRPCEndpoint

from omniagents.core.config_loader import load_agent_spec_from_yaml
from omniagents.core.app_base import _default_build_agent
from omniagents.core.specs import AgentSpec

from .sessions import sm, Session
from .runner_bridge import stream_agent
from .history_db import append_message

__all__ = ["build_app"]

# ------------------------------------------------------------------
# Optional verbose logging controlled by env var
# ------------------------------------------------------------------
DEBUG_SERVER = os.getenv("OMNIAGENTS_SERVER_DEBUG", "0") in ("1", "true", "True")

def _token_dependency(required_token: Optional[str] = None):  # noqa: D401
    """Return a FastAPI dependency that validates the ?token param."""

    async def _verify_token(websocket: WebSocket):  # noqa: D401
        if required_token:
            if websocket.query_params.get("token") != required_token:
                # Close immediately with 4403 (custom) if invalid
                await websocket.close(code=4403)
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token")

    return Depends(_verify_token)


def build_app(config_path: str | None = None, spec: 'AgentSpec' | None = None, auth_token: str | None = None) -> FastAPI:  # noqa: D401
    """Return a fully configured FastAPI app exposing the agent via RPC.

    Args:
        config_path: Path to a YAML spec file. **Ignored if ``spec`` is provided.**
        spec: An already-constructed :class:`~omniagents.core.specs.AgentSpec` instance.  When
            supplied, this allows launching a remote server for programmatically created
            agents (e.g. via ``AgentApp.from_agent``) without the need for an intermediate
            YAML file.
        auth_token: Optional bearer token that clients must pass via the ``?token=XYZ``
            query parameter.
    """

    # ------------------------------------------------------------------
    # Resolve the AgentSpec ------------------------------------------------
    # ------------------------------------------------------------------
    if spec is None:
        if config_path is None:
            raise ValueError("Either `config_path` or `spec` must be provided to build_app().")
        spec = load_agent_spec_from_yaml(config_path)

    app = FastAPI()

    # ------------------------------------------------------------------
    # Register RPC endpoint at "/ws" with optional token dependency
    # ------------------------------------------------------------------

    class AgentService(RpcMethodsBase):
        """JSON-RPC methods available to the client."""

        # --------------------------------------------------------------
        #  Tool-approval request / response helper state
        # --------------------------------------------------------------
        _pending_approvals: dict[str, asyncio.Future] = {}

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
        ):  # noqa: D401
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

        async def start_run(self, prompt: str, session_id: str | None = None):  # noqa: D401
            sess: Session = sm.get_or_create(session_id)

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
                # Provide a closure that captures *sess* so the callback can
                # stash undelivered approval-requests for later replay.
                stub_app = SimpleNamespace(
                    spec=spec,
                    _request_tool_approval=lambda name, args: self._request_tool_approval(name, args, _sess=sess),
                )

                # Prefer the builder defined on the spec (allows custom or pre-constructed
                # agents coming from ``AgentApp.from_agent``).  Fallback to the default
                # builder if none is provided.
                if getattr(spec, "build_agent", None):
                    builder = spec.build_agent  # type: ignore[attr-defined]
                    # Support both async and sync builder functions
                    import inspect
                    if inspect.iscoroutinefunction(builder):
                        agent = await builder({}, None, stub_app)
                    else:
                        agent = builder({}, None, stub_app)
                else:
                    agent = await _default_build_agent({}, None, stub_app)
                sess.agent = agent
            else:
                agent = sess.agent

            async def _worker():
                try:
                    await stream_agent(agent, prompt, sess, self.channel, max_turns=spec.max_turns)
                finally:
                    # Inform client the run finished
                    await self.channel.async_call(
                        "run_end", args={"run_id": asyncio.current_task().get_name(), "end_reason": "completed"}
                    )

            task_name = f"run_{uuid.uuid4().hex}"
            if DEBUG_SERVER:
                print(f"[srv] ★ start_run RPC  prompt={prompt[:80]!r} session_id_in={session_id} -> sess={sess.id[:8]} run_id={task_name}")
            task = asyncio.create_task(_worker(), name=task_name)
            sm.bind_task(task, sess)
            return {"run_id": task_name, "session_id": sess.id}

        async def stop_run(self, run_id: str):  # noqa: D401
            if DEBUG_SERVER:
                print(f"[srv] ★ stop_run  run_id={run_id}")
            return sm.cancel(run_id)

        async def send_user_message(self, run_id: str, content: str):  # noqa: D401
            if DEBUG_SERVER:
                print(f"[srv] ★ send_user_message  run_id={run_id} content={content[:80]!r}")
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
        async def get_session_history(self, session_id: str):  # noqa: D401
            """Return JSON-serialisable history for *session_id*."""
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
            return sess.history

        async def list_sessions(self):  # noqa: D401
            """Return metadata for all sessions."""
            return sm.list_sessions()

        async def archive_session(self, session_id: str):  # noqa: D401
            """Archive *session_id* so clients can hide it."""
            return sm.archive_session(session_id)

    endpoint = WebsocketRPCEndpoint(AgentService())
    # Attach route with dependency if token is set
    if auth_token:
        endpoint.register_route(app, "/ws", dependencies=[_token_dependency(auth_token)])
    else:
        endpoint.register_route(app, "/ws")
    return app 