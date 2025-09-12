from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, WebSocket, Depends, HTTPException, status
from omniagents.rpc import JsonRpcEndpoint

from omniagents.core.config.loader import load_agent_spec_from_yaml
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.agents.service import AgentService

__all__ = ["build_app"]


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
    # Create AgentService instance and register RPC endpoint
    # ------------------------------------------------------------------
    service = AgentService(spec)
    # Allow examples to register custom server functions without changing core
    try:
        registrar = getattr(spec, "register_server_functions", None)
        if callable(registrar):
            registrar(service)
    except Exception as e:
        # Best-effort; don't crash server startup on example registrar failure
        print(f"Warning: register_server_functions failed: {e}")
    endpoint = JsonRpcEndpoint(service)

    if auth_token:
        endpoint.register_route(app, "/ws", dependencies=[_token_dependency(auth_token)])
    else:
        endpoint.register_route(app, "/ws")
    return app
