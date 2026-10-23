"""The project's agents, each served by a real omniagents runtime.

Studio hosts, per agent, the same server ``omniagents run`` starts
(``build_app``): the full GUI protocol over ``ws``, terminals, connect
tickets and the canonical conversation store. The Playground talks to it
with the shared chat UI, and the Agents screen asks its service what a
run of the agent is given. Nothing here is a Studio-specific copy of the
runtime, so neither can drift from what runs.

Each runtime is mounted at ``/api/agents/<key>/runtime`` and built on first
use, with the project directory as its workspace, as running the agent
from that directory would.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from fastapi import FastAPI
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from omniagents.backends.server.app import build_app
from omniagents.core.agents.service import AgentService


class AgentRuntimes:
    """ASGI app dispatching ``/api/agents/{agent_key}/runtime/...`` to that
    agent's runtime."""

    def __init__(
        self,
        specs: Dict[str, Any],
        *,
        workspace_root: Optional[str],
        auth_token: Optional[str],
    ) -> None:
        self._specs = specs
        self._workspace_root = workspace_root
        self._auth_token = auth_token
        self._apps: Dict[str, FastAPI] = {}
        self._lock = asyncio.Lock()

    async def app(self, agent_key: str) -> Optional[FastAPI]:
        spec = self._specs.get(agent_key)
        if spec is None:
            return None
        async with self._lock:
            app = self._apps.get(agent_key)
            if app is None:
                app = build_app(
                    spec=spec,
                    auth_token=self._auth_token,
                    default_host_workspace_root=self._workspace_root,
                )
                self._apps[agent_key] = app
            return app

    async def service(self, agent_key: str) -> Optional[AgentService]:
        app = await self.app(agent_key)
        return app.state.agent_service if app is not None else None

    async def describe(self, agent_key: str) -> Optional[dict]:
        """What a run of the agent is given (``AgentService.describe_agent``)."""
        service = await self.service(agent_key)
        if service is None:
            return None
        return await service.describe_agent(workspace_root=self._workspace_root)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        agent_key = (scope.get("path_params") or {}).get("agent_key", "")
        app = await self.app(agent_key)
        if app is None:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4404})
                return
            response = JSONResponse(
                {"detail": f"Agent '{agent_key}' not found"}, status_code=404
            )
            await response(scope, receive, send)
            return
        await app(scope, receive, send)
