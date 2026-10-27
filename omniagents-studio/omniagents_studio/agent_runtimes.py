"""The project's agents, each served by a real omniagents runtime.

Studio hosts, per agent, the same server ``omniagents run`` starts
(``build_app``): the full GUI protocol over ``ws``, terminals, connect
tickets and the canonical conversation store. The Playground talks to it
with the shared chat UI, and the Agents screen asks its service what a
run of the agent is given. Nothing here is a Studio-specific copy of the
runtime, so neither can drift from what runs.

Each runtime is mounted at ``/api/agents/<key>/runtime`` and built on first
use, with the project directory as its workspace, as running the agent
from that directory would. Editing an agent reloads the project's agents:
the next connection gets a runtime built from the saved files (one already
open keeps the runtime it has until it reconnects). An agent that does not
load is listed with the reason, so it can be opened and fixed.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from omniagents.backends.server.app import build_app
from omniagents.core.agents.service import AgentService
from omniagents.core.project.agent_files import agent_paths
from omniagents.core.project.runtime import build_spec_from_project, load_project

logger = logging.getLogger(__name__)


class AgentRuntimes:
    """ASGI app dispatching ``/api/agents/{agent_key}/runtime/...`` to that
    agent's runtime."""

    def __init__(
        self,
        manifest_path: Optional[Path],
        *,
        auth_token: Optional[str],
    ) -> None:
        self._manifest = manifest_path
        self._workspace_root = str(manifest_path.parent) if manifest_path else None
        self._auth_token = auth_token
        self._specs: Dict[str, Any] = {}
        self._errors: Dict[str, str] = {}
        self._entrypoint: Optional[str] = None
        self._apps: Dict[str, FastAPI] = {}
        self._lock = asyncio.Lock()
        self.reload()

    def reload(self) -> None:
        """Read the project's agents again; runtimes are rebuilt on next use."""
        specs: Dict[str, Any] = {}
        errors: Dict[str, str] = {}
        entrypoint = None
        if self._manifest is not None:
            try:
                project = load_project(str(self._manifest))
            except Exception as exc:  # one agent's file can stop the project loading
                logger.warning("Project does not load: %s", exc)
                errors = {key: str(exc) for key in agent_paths(self._manifest)}
                self._specs, self._errors, self._entrypoint = {}, errors, None
                self._apps = {}
                return
            entrypoint = project.entrypoint
            for key in sorted(project.agents_map):
                try:
                    specs[key] = build_spec_from_project(project, key)
                except Exception as exc:  # shown beside the agent, to be fixed
                    logger.warning("Agent '%s' does not load: %s", key, exc)
                    errors[key] = str(exc)
        self._specs, self._errors, self._entrypoint = specs, errors, entrypoint
        self._apps = {}

    def agents(self) -> list[dict[str, Any]]:
        """Every agent in the project, the entrypoint first."""
        listed = []
        for key in sorted(set(self._specs) | set(self._errors)):
            spec = self._specs.get(key)
            listed.append(
                {
                    "key": key,
                    "label": (
                        getattr(spec, "label", None)
                        or getattr(spec, "name", None)
                        or key
                    ),
                    "description": getattr(spec, "description", None) or "",
                    "is_entry": key == self._entrypoint,
                    "error": self._errors.get(key),
                }
            )
        return sorted(listed, key=lambda a: (not a["is_entry"], a["label"].lower()))

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

    def load_error(self, agent_key: str) -> Optional[str]:
        return self._errors.get(agent_key)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        agent_key = (scope.get("path_params") or {}).get("agent_key", "")
        app = await self.app(agent_key)
        if app is None:
            if scope["type"] == "websocket":
                # Accepted, then refused: a socket closed before acceptance
                # reaches a browser as a failed handshake, not as 4404.
                await receive()
                await send({"type": "websocket.accept"})
                await send({"type": "websocket.close", "code": 4404})
                return
            response = JSONResponse(
                {"detail": f"Agent '{agent_key}' not found"}, status_code=404
            )
            await response(scope, receive, send)
            return
        await app(scope, receive, send)
