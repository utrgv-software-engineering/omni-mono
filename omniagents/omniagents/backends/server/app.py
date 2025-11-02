from __future__ import annotations

import os
import asyncio
from typing import Optional

from fastapi import FastAPI, WebSocket, Depends, HTTPException, status
from fastapi import WebSocketDisconnect
from omniagents.rpc import JsonRpcEndpoint

from omniagents.core.config.loader import load_agent_spec_from_yaml
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.agents.service import AgentService
from omniagents.rpc.realtime_service import RealtimeService

__all__ = ["build_app"]


def _token_dependency(required_token: Optional[str] = None):  # noqa: D401
    """Return a FastAPI dependency that validates the ?token param."""

    async def _verify_token(websocket: WebSocket):  # noqa: D401
        if required_token:
            if websocket.query_params.get("token") != required_token:
                # Close immediately with 4403 (custom) if invalid
                await websocket.close(code=4403)
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail="Invalid token"
                )

    return Depends(_verify_token)


def build_app(
    config_path: str | None = None,
    spec: "AgentSpec" | None = None,
    auth_token: str | None = None,
) -> FastAPI:  # noqa: D401
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
            raise ValueError(
                "Either `config_path` or `spec` must be provided to build_app()."
            )
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
        endpoint.register_route(
            app, "/ws", dependencies=[_token_dependency(auth_token)]
        )
    else:
        endpoint.register_route(app, "/ws")

    # ------------------------------------------------------------------
    # Register realtime endpoint if realtime mode is enabled
    # ------------------------------------------------------------------
    if spec.realtime_mode:
        # Build settings for realtime service
        realtime_settings = {}
        for field in getattr(spec, "settings_fields", []) or []:
            key = getattr(field, "key", None)
            if not key:
                continue
            value = os.getenv(key.upper())
            if value in (None, ""):
                value = getattr(field, "default", None)
            realtime_settings[key] = value

        realtime_service = RealtimeService(spec, realtime_settings)
        realtime_endpoint = JsonRpcEndpoint(realtime_service)

        if auth_token:
            realtime_endpoint.register_route(
                app, "/ws/realtime", dependencies=[_token_dependency(auth_token)]
            )
        else:
            realtime_endpoint.register_route(app, "/ws/realtime")

    if getattr(service, "terminal_manager", None) is not None:

        async def _terminal_route(websocket: WebSocket):
            terminal_manager = getattr(service, "terminal_manager", None)
            if terminal_manager is None:
                await websocket.close(code=4403)
                return
            if auth_token:
                if websocket.query_params.get("token") != auth_token:
                    await websocket.close(code=4403)
                    return
            session_id = websocket.query_params.get("session_id") or ""
            terminal_id = websocket.query_params.get("terminal_id") or ""
            term_token = websocket.query_params.get("terminal_token") or ""
            if not session_id or not terminal_id or not term_token:
                await websocket.close(code=4403)
                return
            try:
                terminal = await terminal_manager.authorize(
                    session_id, terminal_id, term_token
                )
            except Exception:
                await websocket.close(code=4403)
                return
            await websocket.accept()

            async def _forward_output():
                try:
                    while True:
                        chunk = await terminal_manager.read_output(terminal)
                        if chunk is None:
                            await websocket.send_json(
                                {
                                    "type": "exit",
                                    "terminal_id": terminal_id,
                                    "code": terminal.exit_code,
                                }
                            )
                            break
                        await websocket.send_json(
                            {
                                "type": "output",
                                "terminal_id": terminal_id,
                                "data": terminal_manager.encode_output(chunk),
                            }
                        )
                except Exception:
                    pass

            output_task = asyncio.create_task(_forward_output())
            try:
                while True:
                    try:
                        message = await websocket.receive_json()
                    except WebSocketDisconnect:
                        break
                    except Exception:
                        break
                    if not isinstance(message, dict):
                        continue
                    msg_type = message.get("type")
                    if msg_type == "input":
                        data = message.get("data")
                        if isinstance(data, str):
                            try:
                                payload = terminal_manager.decode_input(data)
                            except Exception:
                                payload = b""
                            if payload:
                                await terminal_manager.write_input(terminal, payload)
                    elif msg_type == "resize":
                        cols = message.get("cols")
                        rows = message.get("rows")
                        try:
                            c = int(cols)
                            r = int(rows)
                        except Exception:
                            continue
                        await terminal_manager.resize(terminal, c, r)
                    elif msg_type == "close":
                        break
            finally:
                try:
                    await terminal_manager.close_terminal(session_id, terminal_id)
                except Exception:
                    pass
                try:
                    await output_task
                except Exception:
                    pass
                try:
                    await websocket.close()
                except Exception:
                    pass

        app.add_api_websocket_route("/ws/terminal", _terminal_route)
    return app
