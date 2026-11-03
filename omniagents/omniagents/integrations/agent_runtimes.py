"""Each of the project's agents, served for reading its sessions: ``/agents/<key>/``.

A factory's server runs its entry agent at ``/ws``; its automations run any
agent, and each agent keeps its sessions in its own store. To show any run's
sessions with the shared chat UI (the workplace's Factory page opens a run's
transcript, and those of the agents it called), the server mounts, per agent,
the same runtime ``omniagents run`` serves (``build_app``), built on first use,
behind the server's own token. These runtimes receive no events: the server
itself does (``integrations=False``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

__all__ = ["AGENTS_PATH", "AgentRuntimes"]

logger = logging.getLogger(__name__)

AGENTS_PATH = "/agents"


class AgentRuntimes:
    """ASGI app: ``/<agent key>/...`` to that agent's runtime."""

    def __init__(
        self,
        project: Any,
        *,
        auth_token: Optional[str],
        primary_app=None,
        primary_agent=None,
        host_resources=None,
    ) -> None:
        self.host_resources = host_resources
        self.project = project
        self.primary_app = primary_app
        self.primary_agent = primary_agent or getattr(project, "entrypoint", None)
        self.auth_token = auth_token
        self._apps: Dict[str, Any] = {}
        self._lock = asyncio.Lock()

    def services(self):
        apps = ([self.primary_app] if self.primary_app is not None else []) + list(
            self._apps.values()
        )
        return [app.state.agent_service for app in apps]

    def busy(self):
        return sum(
            len(service.active_run_ids())
            + sum(
                item.state == "pending"
                for session in service.active_sessions()
                for item in session.message_queue
            )
            for service in self.services()
        )

    async def app(self, key: str) -> Optional[Any]:
        if self.primary_app is not None and key == self.primary_agent:
            return self.primary_app
        if key not in self.project.agents_map:
            return None
        async with self._lock:
            if key not in self._apps:
                from omniagents.backends.server.app import build_app
                from omniagents.core.project.runtime import build_spec_from_project

                spec = build_spec_from_project(self.project, key)
                options = (
                    {"agent_host_resources": self.host_resources}
                    if self.host_resources is not None
                    else {}
                )
                self._apps[key] = build_app(
                    spec=spec, auth_token=self.auth_token, integrations=False, **options
                )
            result = self._apps[key]
            if self.primary_app is not None:
                result.state.agent_service._release_gate = getattr(
                    self.primary_app.state.agent_service, "_release_gate", None
                )
            return result

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            # Each runtime starts with its first request; nothing to start here.
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            return
        # Mounted, ``path`` is the full path and ``root_path`` ends with the mount's.
        root = scope.get("root_path", "")
        path = scope.get("path", "")
        if root and path.startswith(root):
            path = path[len(root) :]
        key = path.lstrip("/").partition("/")[0]
        app = await self.app(key)
        if app is None:
            if scope["type"] == "http":
                await JSONResponse({"error": f"no agent {key!r}"}, status_code=404)(
                    scope, receive, send
                )
            else:
                await send({"type": "websocket.close", "code": 4404})
            return
        inner = dict(scope)
        inner["root_path"] = root + "/" + key
        await app(inner, receive, send)
