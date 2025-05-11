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

from .sessions import sm, Session
from .runner_bridge import stream_agent

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


def build_app(config_path: str, auth_token: str | None = None) -> FastAPI:  # noqa: D401
    """Return a fully configured FastAPI app exposing the agent via RPC."""
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

        async def _request_tool_approval(self, tool_name: str, formatted_args: str):  # noqa: D401
            approval_id = uuid.uuid4().hex

            # Fire a notification – the client will present the UI and reply
            await self.channel.async_call(
                "tool_approval_request",
                args={
                    "approval_id": approval_id,
                    "tool": tool_name,
                    "arguments": formatted_args,
                },
            )

            # Wait for user decision
            fut: asyncio.Future = asyncio.get_event_loop().create_future()
            self._pending_approvals[approval_id] = fut
            return await fut  # -> (approved, always_approve, error_message)

        async def start_run(self, prompt: str, session_id: str | None = None):  # noqa: D401
            sess: Session = sm.get_or_create(session_id)
            if sess.current_task and not sess.current_task.done():
                raise RuntimeError("Run already active in this session")

            # Build (or reuse) agent for this session
            if not hasattr(sess, "agent"):
                # Build a stub *app* object that provides the approval callback
                stub_app = SimpleNamespace(spec=spec, _request_tool_approval=self._request_tool_approval)
                agent = await _default_build_agent({}, None, stub_app)
                sess.agent = agent
            else:
                agent = sess.agent

            async def _worker():
                try:
                    await stream_agent(agent, prompt, sess, self.channel)
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
            sess.history.append({"role": "user", "content": content})
            # No immediate return value; next assistant chunk will arrive via stream.

        # ------------------------------------------------------------------
        #  NEW: expose conversation history to clients ---------------------
        # ------------------------------------------------------------------
        async def get_session_history(self, session_id: str):  # noqa: D401
            """Return JSON-serialisable history for *session_id*."""
            sess: Session = sm.get_or_create(session_id)
            # Serialise to strict JSON so clients can parse reliably
            try:
                return json.dumps(sess.history)
            except TypeError:
                # Fallback: return string repr as last resort
                return str(sess.history)

    endpoint = WebsocketRPCEndpoint(AgentService())
    # Attach route with dependency if token is set
    if auth_token:
        endpoint.register_route(app, "/ws", dependencies=[_token_dependency(auth_token)])
    else:
        endpoint.register_route(app, "/ws")
    return app 