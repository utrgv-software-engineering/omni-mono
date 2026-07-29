from __future__ import annotations

import os
import asyncio
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from typing import Optional, Dict, Any, Callable

from fastapi import FastAPI, WebSocket
from fastapi import WebSocketDisconnect
from omniagents.rpc import JsonRpcEndpoint
from omniagents.rpc.auth import (
    WS_CLOSE_FORBIDDEN,
    ConnectionAuthenticator,
    build_ws_auth_dependency,
    register_ticket_route,
)
from omniagents.rpc.server import GUI_RPC_METHODS

from omniagents.core.config.loader import load_agent_spec_from_yaml
from omniagents.core.agents.specs import AgentSpec
from omniagents.core.agents.service import AgentService
from omniagents.rpc.realtime_service import RealtimeService

__all__ = ["build_app"]

#: Query parameters that carry connection credentials. Never forwarded to
#: an upstream server and never expected by new-style clients.
_CREDENTIAL_QUERY_PARAMS = ("token", "ticket")


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
) -> str:
    """Build the upstream URL, stripping every credential query param.

    Upstream authentication travels in the ``Authorization`` header (see
    :func:`_remote_auth_headers`), never in the URL.
    """
    target = _derive_remote_ws_url(base_url, suffix=suffix)
    parsed = urlparse(target)

    query: dict[str, str] = {}
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key in _CREDENTIAL_QUERY_PARAMS:
            continue
        query[key] = value
    for key, value in websocket.query_params.multi_items():
        if key in _CREDENTIAL_QUERY_PARAMS:
            continue
        query[key] = value

    return urlunparse(parsed._replace(query=urlencode(query)))


def _remote_auth_headers(remote_token: str | None) -> Dict[str, str]:
    if not remote_token:
        return {}
    return {"Authorization": f"Bearer {remote_token}"}


async def _connect_remote(url: str, headers: Dict[str, str]):
    """``websockets.connect`` across library versions (header kwarg renamed)."""
    import websockets

    try:
        return await websockets.connect(url, additional_headers=headers or None)
    except TypeError:
        return await websockets.connect(url, extra_headers=headers or None)


async def _proxy_websocket(
    websocket: WebSocket,
    *,
    remote_url: str,
    remote_headers: Dict[str, str] | None = None,
) -> None:
    try:
        from websockets.exceptions import ConnectionClosed
    except Exception:
        await websocket.close(code=4404)
        return

    await websocket.accept()

    try:
        remote = await _connect_remote(remote_url, remote_headers or {})
        async with remote:

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


def build_app(
    config_path: str | None = None,
    spec: "AgentSpec" | None = None,
    auth_token: str | None = None,
    *,
    default_workspace_root: str | None = None,
    workspace_root_resolver: "Callable[[str | None], str | None] | None" = None,
) -> FastAPI:  # noqa: D401
    """Return a fully configured FastAPI app exposing the agent via RPC.

    Args:
        config_path: Path to a YAML spec file. **Ignored if ``spec`` is provided.**
        spec: An already-constructed :class:`~omniagents.core.specs.AgentSpec` instance.  When
            supplied, this allows launching a remote server for programmatically created
            agents (e.g. via ``AgentApp.from_agent``) without the need for an intermediate
            YAML file.
        auth_token: Optional bearer token clients must present via the
            ``Authorization: Bearer`` header or a ``/auth/ws-ticket``
            connect ticket. The deprecated ``?token=`` query transport is
            accepted only when ``OMNIAGENTS_LEGACY_QUERY_AUTH=1`` (or
            ``security.legacy_query_auth: true``) is set.
        default_workspace_root: Optional fallback path passed through to
            ``AgentService(default_workspace_root=…)``. CLI-style backends
            (``ink``/``web``) pass ``os.getcwd()`` here so a missing
            ``workspace_root`` on a run resolves to the user's terminal dir
            instead of raising. Production server deployments should leave
            this ``None`` so a missing value fails loudly rather than
            silently anchoring to the server process's cwd.
        workspace_root_resolver: Maps a requested ``workspace_root`` to the
            effective one, forwarded to ``AgentService``. ``omni serve`` passes
            one that clamps a sandbox cwd to within the manifest root (no host
            path can reach a sandbox run) while allowing navigation among
            container subdirectories.
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

    # One authenticator guards every surface — /ws, /ws/realtime and
    # /ws/terminal all resolve the same principal through it, and
    # /auth/ws-ticket mints browser connect tickets against it.
    authenticator = ConnectionAuthenticator.from_spec(spec, auth_token=auth_token)
    app.state.connection_authenticator = authenticator
    auth_dep = build_ws_auth_dependency(authenticator)
    register_ticket_route(app, authenticator)

    remote_ws_url = getattr(spec, "remote_ws_url", None)
    if remote_ws_url:
        remote_token = None
        remote_token_key = getattr(spec, "remote_auth_token_key", None)
        if remote_token_key:
            remote_token = os.getenv(remote_token_key) or None
        remote_headers = _remote_auth_headers(remote_token)

        async def _ws_proxy(websocket: WebSocket):
            url = _compose_remote_ws_url(
                remote_ws_url,
                suffix="",
                websocket=websocket,
            )
            await _proxy_websocket(
                websocket, remote_url=url, remote_headers=remote_headers
            )

        async def _ws_realtime_proxy(websocket: WebSocket):
            url = _compose_remote_ws_url(
                remote_ws_url,
                suffix="/realtime",
                websocket=websocket,
            )
            await _proxy_websocket(
                websocket, remote_url=url, remote_headers=remote_headers
            )

        async def _ws_terminal_proxy(websocket: WebSocket):
            url = _compose_remote_ws_url(
                remote_ws_url,
                suffix="/terminal",
                websocket=websocket,
            )
            await _proxy_websocket(
                websocket, remote_url=url, remote_headers=remote_headers
            )

        deps = [auth_dep]
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
    service = AgentService(
        spec,
        default_workspace_root=default_workspace_root,
        workspace_root_resolver=workspace_root_resolver,
    )
    app.state.agent_service = service
    # The service authorizes every request against the mode implied by the
    # configured provider: enforced (ownership + global-effect rules) when
    # principals are distinguishable, permissive in single-user/no-auth mode.
    service.authorization_policy = authenticator.authorization_policy
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

    realtime_spec = getattr(spec, "voice_spec", None)
    spec_voice_backend = getattr(spec, "voice_backend", "realtime")
    if realtime_spec is None and (
        spec.realtime_mode or spec_voice_backend == "pipeline"
    ):
        realtime_spec = spec

    endpoint = JsonRpcEndpoint(
        service,
        allowed_methods=GUI_RPC_METHODS,
        gui_handshake=True,
        server_capabilities={
            "realtime": realtime_spec is not None,
            "mcp_apps": True,
            "client_functions": True,
            "approvals": True,
            "artifacts": True,
            "replay": True,
            "terminal": getattr(service, "terminal_manager", None) is not None,
        },
    )

    endpoint.register_route(app, "/ws", dependencies=[auth_dep])

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
        realtime_service.authorization_policy = authenticator.authorization_policy
        app.state.realtime_service = realtime_service
        try:
            service.realtime_service = realtime_service
        except Exception:
            pass
        realtime_endpoint = JsonRpcEndpoint(realtime_service)
        realtime_endpoint.register_route(app, "/ws/realtime", dependencies=[auth_dep])
    else:
        # Add a fallback route that gracefully rejects connections when realtime is disabled
        # This prevents StaticFiles from catching the WebSocket request and crashing
        async def _realtime_disabled(websocket: WebSocket):
            await websocket.close(code=4404)  # Custom code: feature not available

        app.add_api_websocket_route(
            "/ws/realtime", _realtime_disabled, dependencies=[auth_dep]
        )

    async def _terminal_route(websocket: WebSocket):
        terminal_manager = getattr(service, "terminal_manager", None)
        if terminal_manager is None:
            await websocket.close(code=4404)
            return

        # Connection auth already ran via the shared dependency; the
        # attach credentials (session/terminal ids + one-time terminal
        # token) arrive in the FIRST message so they never appear in the
        # URL. The deprecated query-param form is honored only while
        # legacy query auth is enabled.
        principal = getattr(websocket.state, "principal", None)
        # Attach checks run under the same policy as RPC authorization.
        if hasattr(terminal_manager, "authorization_policy"):
            terminal_manager.authorization_policy = authenticator.authorization_policy
        session_id = ""
        terminal_id = ""
        term_token = ""
        if authenticator.allow_query_token:
            session_id = websocket.query_params.get("session_id") or ""
            terminal_id = websocket.query_params.get("terminal_id") or ""
            term_token = websocket.query_params.get("terminal_token") or ""

        await websocket.accept()

        if not (session_id and terminal_id and term_token):
            try:
                attach = await asyncio.wait_for(websocket.receive_json(), timeout=10.0)
            except Exception:
                await websocket.close(code=WS_CLOSE_FORBIDDEN)
                return
            if not isinstance(attach, dict) or attach.get("type") != "attach":
                await websocket.close(code=WS_CLOSE_FORBIDDEN)
                return
            session_id = str(attach.get("session_id") or "")
            terminal_id = str(attach.get("terminal_id") or "")
            term_token = str(attach.get("terminal_token") or attach.get("token") or "")
        if not session_id or not terminal_id or not term_token:
            await websocket.close(code=WS_CLOSE_FORBIDDEN)
            return
        try:
            terminal = await terminal_manager.authorize(
                session_id, terminal_id, term_token, principal=principal
            )
        except Exception:
            await websocket.close(code=WS_CLOSE_FORBIDDEN)
            return

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

    app.add_api_websocket_route(
        "/ws/terminal", _terminal_route, dependencies=[auth_dep]
    )
    return app
