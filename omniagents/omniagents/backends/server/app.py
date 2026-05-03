from __future__ import annotations

import os
import asyncio
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from typing import Optional, Dict, Any

from fastapi import FastAPI, WebSocket, Depends, HTTPException, status
from fastapi import WebSocketDisconnect
from omniagents.rpc import JsonRpcEndpoint

from omniagents.core.config.loader import load_agent_spec_from_yaml
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.agents.service import AgentService
from omniagents.rpc.realtime_service import RealtimeService

__all__ = ["build_app"]


def _derive_remote_ws_url(remote_ws_url: str, *, suffix: str) -> str:
    parsed = urlparse(remote_ws_url)
    path = parsed.path or ""

    if path.endswith("/ws"):
        new_path = path + suffix
    else:
        trimmed = path.rstrip("/")
        if not trimmed:
            new_path = "/ws" + suffix
        else:
            new_path = trimmed + "/ws" + suffix

    return urlunparse(parsed._replace(path=new_path))


def _compose_remote_ws_url(
    base_url: str,
    *,
    suffix: str,
    websocket: WebSocket,
    remote_token: str | None,
) -> str:
    target = _derive_remote_ws_url(base_url, suffix=suffix)
    parsed = urlparse(target)

    query: dict[str, str] = {}
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key == "token" and remote_token:
            continue
        query[key] = value
    for key, value in websocket.query_params.multi_items():
        if key == "token":
            continue
        query[key] = value
    if remote_token:
        query["token"] = remote_token

    return urlunparse(parsed._replace(query=urlencode(query)))


async def _proxy_websocket(websocket: WebSocket, *, remote_url: str) -> None:
    try:
        import websockets
        from websockets.exceptions import ConnectionClosed
    except Exception:
        await websocket.close(code=4404)
        return

    await websocket.accept()

    try:
        async with websockets.connect(remote_url) as remote:

            async def _client_to_remote() -> None:
                while True:
                    try:
                        message = await websocket.receive()
                    except WebSocketDisconnect:
                        break
                    if message.get("type") == "websocket.disconnect":
                        break
                    text = message.get("text")
                    if text is not None:
                        await remote.send(text)
                        continue
                    data = message.get("bytes")
                    if data is not None:
                        await remote.send(data)

            async def _remote_to_client() -> None:
                while True:
                    try:
                        data = await remote.recv()
                    except ConnectionClosed:
                        break
                    if isinstance(data, bytes):
                        await websocket.send_bytes(data)
                    else:
                        await websocket.send_text(str(data))

            forward_task = asyncio.create_task(_client_to_remote())
            reverse_task = asyncio.create_task(_remote_to_client())
            done, pending = await asyncio.wait(
                {forward_task, reverse_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in done:
                try:
                    await task
                except Exception:
                    pass
        try:
            await websocket.close()
        except Exception:
            pass
    except Exception:
        try:
            await websocket.close(code=1011)
        except Exception:
            pass


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


def _auth_provider_dependency(auth_provider):  # noqa: D401
    """Return a FastAPI dependency using a pluggable AuthProvider."""

    async def _verify_auth(websocket: WebSocket):  # noqa: D401
        result = await auth_provider.authenticate(websocket)
        if not result.authenticated:
            await websocket.close(code=4403)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=result.error or "Authentication failed",
            )
        # Store identity on websocket state for downstream use
        websocket.state.user_identity = result.identity

    return Depends(_verify_auth)


def build_app(
    config_path: str | None = None,
    spec: "AgentSpec" | None = None,
    auth_token: str | None = None,
    *,
    default_workspace_root: str | None = None,
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
        default_workspace_root: Optional fallback path passed through to
            ``AgentService(default_workspace_root=…)``. CLI-style backends
            (``ink``/``web``) pass ``os.getcwd()`` here so a missing
            ``workspace_root`` on a run resolves to the user's terminal dir
            instead of raising. Production server deployments should leave
            this ``None`` so a missing value fails loudly rather than
            silently anchoring to the server process's cwd.
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

    remote_ws_url = getattr(spec, "remote_ws_url", None)
    if remote_ws_url:
        remote_token = None
        remote_token_key = getattr(spec, "remote_auth_token_key", None)
        if remote_token_key:
            remote_token = os.getenv(remote_token_key) or None

        deps = [_token_dependency(auth_token)] if auth_token else None

        async def _ws_proxy(websocket: WebSocket):
            url = _compose_remote_ws_url(
                remote_ws_url,
                suffix="",
                websocket=websocket,
                remote_token=remote_token,
            )
            await _proxy_websocket(websocket, remote_url=url)

        async def _ws_realtime_proxy(websocket: WebSocket):
            url = _compose_remote_ws_url(
                remote_ws_url,
                suffix="/realtime",
                websocket=websocket,
                remote_token=remote_token,
            )
            await _proxy_websocket(websocket, remote_url=url)

        async def _ws_terminal_proxy(websocket: WebSocket):
            url = _compose_remote_ws_url(
                remote_ws_url,
                suffix="/terminal",
                websocket=websocket,
                remote_token=remote_token,
            )
            await _proxy_websocket(websocket, remote_url=url)

        app.add_api_websocket_route("/ws", _ws_proxy, dependencies=deps)
        app.add_api_websocket_route(
            "/ws/realtime", _ws_realtime_proxy, dependencies=deps
        )
        app.add_api_websocket_route(
            "/ws/terminal", _ws_terminal_proxy, dependencies=deps
        )
        return app

    # ------------------------------------------------------------------
    # Create AgentService instance and register RPC endpoint
    # ------------------------------------------------------------------
    service = AgentService(spec, default_workspace_root=default_workspace_root)
    app.state.agent_service = service
    # Allow examples to register custom server functions without changing core
    try:
        registrar = getattr(spec, "register_server_functions", None)
        if callable(registrar):
            registrar(service)
    except Exception as e:
        # Best-effort; don't crash server startup on example registrar failure
        print(f"Warning: register_server_functions failed: {e}")

    try:
        registrar = getattr(spec, "register_runtime_hooks", None)
        if callable(registrar):
            registrar(service)
    except Exception as e:
        print(f"Warning: register_runtime_hooks failed: {e}")
    endpoint = JsonRpcEndpoint(service)

    # Resolve auth dependency: provider-based or legacy token
    _auth_deps = None
    try:
        sec = getattr(spec, "security_config", None) or {}
        auth_cfg = (sec.get("providers") or {}).get("auth")
        if auth_cfg and auth_cfg.get("type", "none") != "none":
            from omniagents.core.providers import resolve_provider

            _auth_provider = resolve_provider("auth", auth_cfg)
            _auth_deps = [_auth_provider_dependency(_auth_provider)]
    except Exception:
        pass
    if _auth_deps is None and auth_token:
        _auth_deps = [_token_dependency(auth_token)]

    if _auth_deps:
        endpoint.register_route(app, "/ws", dependencies=_auth_deps)
    else:
        endpoint.register_route(app, "/ws")

    # ------------------------------------------------------------------
    # Register realtime endpoint if realtime mode is enabled
    # ------------------------------------------------------------------
    def _collect_settings(target_spec: AgentSpec) -> dict:
        settings: Dict[str, Any] = {}
        for field in getattr(target_spec, "settings_fields", []) or []:
            key = getattr(field, "key", None)
            if not key:
                continue
            value = os.getenv(key.upper())
            if value in (None, ""):
                value = getattr(field, "default", None)
            settings[key] = value
        return settings

    realtime_spec = getattr(spec, "voice_spec", None)
    spec_voice_backend = getattr(spec, "voice_backend", "realtime")
    if realtime_spec is None and (
        spec.realtime_mode or spec_voice_backend == "pipeline"
    ):
        realtime_spec = spec

    if realtime_spec:
        realtime_settings = _collect_settings(realtime_spec)
        if not realtime_settings and realtime_spec is not spec:
            fallback_settings = _collect_settings(spec)
            for key, value in fallback_settings.items():
                realtime_settings.setdefault(key, value)

        settings_resolver = getattr(realtime_spec, "resolve_realtime_settings", None)
        voice_backend = getattr(realtime_spec, "voice_backend", "realtime")
        if voice_backend == "pipeline":
            from omniagents.rpc.voice_pipeline_service import VoicePipelineService

            realtime_service = VoicePipelineService(
                realtime_spec,
                realtime_settings,
                settings_resolver=(
                    settings_resolver if callable(settings_resolver) else None
                ),
                agent_service=service,
            )
        else:
            realtime_service = RealtimeService(
                realtime_spec,
                realtime_settings,
                settings_resolver=(
                    settings_resolver if callable(settings_resolver) else None
                ),
            )
        app.state.realtime_service = realtime_service
        try:
            service.realtime_service = realtime_service
        except Exception:
            pass
        realtime_endpoint = JsonRpcEndpoint(realtime_service)

        if auth_token:
            realtime_endpoint.register_route(
                app, "/ws/realtime", dependencies=[_token_dependency(auth_token)]
            )
        else:
            realtime_endpoint.register_route(app, "/ws/realtime")
    else:
        # Add a fallback route that gracefully rejects connections when realtime is disabled
        # This prevents StaticFiles from catching the WebSocket request and crashing
        async def _realtime_disabled(websocket: WebSocket):
            await websocket.close(code=4404)  # Custom code: feature not available

        app.add_api_websocket_route("/ws/realtime", _realtime_disabled)

    async def _terminal_route(websocket: WebSocket):
        terminal_manager = getattr(service, "terminal_manager", None)
        if terminal_manager is None:
            await websocket.close(code=4404)
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
            terminal.consumer_attached = False
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
