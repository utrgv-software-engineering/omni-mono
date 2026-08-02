from __future__ import annotations

import os
import asyncio
import secrets
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from typing import TYPE_CHECKING, Optional, Dict, Any, Callable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from omniagents.core.providers.auth.base import UserIdentity
    from omniagents.execution import AgentHostResources

from fastapi import FastAPI, WebSocket
from fastapi import WebSocketDisconnect
from omniagents.rpc import JsonRpcEndpoint
from omniagents.rpc.auth import (
    WS_CLOSE_FORBIDDEN,
    WS_CLOSE_TRY_AGAIN_LATER,
    WS_CLOSE_UNAUTHENTICATED,
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
    :class:`UpstreamCredential`), never in the URL.
    """
    target = _derive_remote_ws_url(base_url, suffix=suffix)
    parsed = urlparse(target)

    # Client-supplied routing hints are allowed only where the operator has
    # not pinned a value in ``remote_agent.ws_url``. Applying the configured
    # query last makes that policy explicit and prevents a caller from
    # overriding upstream deployment controls (model, tenant, api-version,
    # etc.).
    query: dict[str, str] = {}
    for key, value in websocket.query_params.multi_items():
        if key in _CREDENTIAL_QUERY_PARAMS:
            continue
        query[key] = value
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key in _CREDENTIAL_QUERY_PARAMS:
            continue
        query[key] = value

    return urlunparse(parsed._replace(query=urlencode(query)))


class UpstreamCredential:
    """How the proxy hop authenticates *one* connection upstream.

    The proxy terminates the client's WebSocket, so the upstream never
    sees the original credential. Whatever this returns is the only thing
    the upstream can authorize against — which is why it takes the
    locally authenticated principal rather than being computed once at
    startup.
    """

    #: Whether the upstream can tell proxied users apart.
    preserves_identity: bool = False

    def headers_for(self, principal: "UserIdentity | None") -> Dict[str, str]:
        raise NotImplementedError


class PerUserIdentityAssertion(UpstreamCredential):
    """Mint a fresh signed assertion naming the local principal.

    This is what makes per-user authorization survive the hop: the
    upstream resolves ``alice`` and ``bob`` as distinct principals and
    runs its ordinary ``AgentService.authorize_rpc`` ownership checks
    against them, so a cross-user attempt is refused with -32014 upstream
    and the error travels back down the proxy like any other frame.
    """

    preserves_identity = True

    def __init__(
        self, *, signing_secret: str, audience: str, allowed_roles: Any = None
    ) -> None:
        from omniagents.rpc.identity_assertion import (
            normalize_role_allowlist,
            validate_signing_secret,
        )

        self._secret = validate_signing_secret(signing_secret)
        self._audience = audience
        self._allowed_roles = normalize_role_allowlist(
            allowed_roles, field_name="remote_agent.identity_allowed_roles"
        )

    def headers_for(self, principal: "UserIdentity | None") -> Dict[str, str]:
        from omniagents.rpc.identity_assertion import mint_identity_assertion

        if principal is None:
            # Unreachable while the auth dependency guards the route; if
            # it ever becomes reachable, refuse rather than assert a
            # principal we did not authenticate.
            raise ValueError("No authenticated principal to assert upstream")
        assertion = mint_identity_assertion(
            principal,
            secret=self._secret,
            audience=self._audience,
            allowed_roles=self._allowed_roles,
        )
        return {"Authorization": f"Bearer {assertion}"}


class SharedUpstreamToken(UpstreamCredential):
    """One shared credential for every proxied connection.

    **Single-tenant only.** Every local user reaches the upstream as the
    same principal, so upstream ownership rules cannot separate them.
    :func:`_resolve_upstream_credential` refuses to build this when the
    proxy's own authenticator identifies distinct principals, so it can
    only be selected where there is exactly one principal to begin with
    (the desktop/launcher topology it exists for).
    """

    preserves_identity = False

    def __init__(self, token: str | None) -> None:
        self._headers: Dict[str, str] = (
            {"Authorization": f"Bearer {token}"} if token else {}
        )

    def headers_for(self, principal: "UserIdentity | None") -> Dict[str, str]:
        return dict(self._headers)


def _configured_client_credentials(spec: Any, auth_token: str | None = None) -> set:
    """Every credential a *client* of this proxy legitimately holds.

    Used to refuse a signing secret that doubles as one of them. Bob has
    to hold his own bearer token to reach the proxy at all, so if that
    token is also the assertion signing key he can mint
    ``sub=alice, roles=["admin"]`` and dial the upstream himself. The
    signing key must be a secret no client ever sees.
    """
    security = getattr(spec, "security_config", None) or {}
    auth_cfg = (security.get("providers") or {}).get("auth") or {}
    found = _credentials_in_auth_config(auth_cfg)
    # ``build_app(auth_token=...)`` — the ``serve --token X`` path — is a
    # client credential too, and it does not appear anywhere in
    # ``security_config``. Reading only the spec missed the exact shape
    # the migration notes recommend.
    if auth_token:
        found.add(auth_token)
    return found


def _credentials_in_auth_config(auth_cfg: Any) -> set:
    """Recursively collect literal/`${VAR}`-resolved credentials."""
    from omniagents.core.providers.auth.token import _expand_env

    found: set = set()
    if not isinstance(auth_cfg, dict):
        return found
    kind = str(auth_cfg.get("type", "")).lower()
    if kind == "composite":
        for entry in auth_cfg.get("accept") or []:
            found |= _credentials_in_auth_config(entry)
        return found
    if kind == "token":
        single, _ = _expand_env(auth_cfg.get("token", ""))
        if single:
            found.add(single)
        for raw in auth_cfg.get("tokens") or {}:
            value, _ = _expand_env(raw)
            if value:
                found.add(value)
    return found


def _resolve_upstream_credential(
    spec: Any,
    authenticator: ConnectionAuthenticator,
    *,
    auth_token: str | None = None,
) -> UpstreamCredential:
    """Pick — and validate — how this proxy authenticates upstream.

    Fails closed at startup rather than silently degrading, the
    ``TokenAuthProvider`` precedent. The rule, applied to **both**
    branches: what the upstream sees must match what this hop can
    actually distinguish. A hop that separates its users may not present
    one shared identity, and a hop that separates nobody may not claim to
    assert per-user identity.
    """
    from omniagents.rpc.identity_assertion import DEFAULT_ASSERTION_AUDIENCE

    secret_key = getattr(spec, "remote_identity_secret_key", None)
    token_key = getattr(spec, "remote_auth_token_key", None)
    remote_token = os.getenv(token_key) or None if token_key else None

    if secret_key:
        if remote_token:
            raise ValueError(
                "Proxy mode has both `remote_agent.identity_secret_env` and a "
                "resolved `remote_agent.auth_token_env`. These are two "
                "different upstream authentication schemes and there is no "
                "precedence rule between them — configure exactly one."
            )
        if not authenticator.identifies_principals:
            # Otherwise every assertion names ``anonymous``. Upstream that
            # is enforced mode with a single owner, so ``anonymous`` can
            # read every session ``anonymous`` created — total cross-user
            # access, under a credential that advertises the opposite.
            raise ValueError(
                "Proxy mode has `remote_agent.identity_secret_env` set, but "
                "this hop authenticates no distinct principals, so every "
                "assertion would name the same `anonymous` user and the "
                "upstream would treat all of them as one owner with full "
                "access to each other's sessions. Configure authentication "
                "on this hop (`security.providers.auth`), or drop the "
                "identity secret and use the single-tenant "
                "`remote_agent.auth_token_env` credential."
            )
        secret = os.getenv(secret_key) or None
        if not secret:
            raise ValueError(
                f"`remote_agent.identity_secret_env` names `{secret_key}`, but "
                "that environment variable is unset or empty. Refusing to fall "
                "back to a shared upstream identity."
            )
        if secret in _configured_client_credentials(spec, auth_token):
            raise ValueError(
                f"The identity-assertion signing secret (`{secret_key}`) is "
                "also a credential this proxy hands to its own clients. Any "
                "client holding it could mint an assertion for any user — "
                "including one with the `admin` role — and present it to the "
                "upstream directly. Use a separate secret that no client "
                "ever sees."
            )
        audience = (
            getattr(spec, "remote_identity_audience", None)
            or DEFAULT_ASSERTION_AUDIENCE
        )
        return PerUserIdentityAssertion(
            signing_secret=secret,
            audience=audience,
            allowed_roles=getattr(spec, "remote_identity_allowed_roles", None),
        )

    if authenticator.identifies_principals:
        raise ValueError(
            "Proxy mode (`remote_agent.ws_url`) is configured with per-user "
            "authentication on this hop but no upstream identity secret, so "
            "every local principal would reach the upstream as the same "
            "identity and upstream ownership checks could not separate them. "
            "Set `remote_agent.identity_secret_env` and configure the upstream "
            "with `security.providers.auth.type: proxy_assertion`. The shared "
            "`auth_token_env` credential is supported for single-tenant "
            "deployments only (no authentication configured on this hop)."
        )

    # Single-tenant: this hop identifies no principals, so there is one
    # user and one upstream identity is the honest representation.
    return SharedUpstreamToken(remote_token)


async def _connect_remote(url: str, headers: Dict[str, str]):
    """``websockets.connect`` across library versions (header kwarg renamed)."""
    import websockets

    try:
        return await websockets.connect(url, additional_headers=headers or None)
    except TypeError:
        return await websockets.connect(url, extra_headers=headers or None)


def _upstream_handshake_close_code(exc: BaseException) -> int:
    """Map a failed upstream WebSocket handshake to a close code.

    An upstream that refuses the credential answers the HTTP upgrade with
    401/403. Reporting that to the client as a generic 1011 would turn a
    terminal authentication failure into a retryable transport error.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if status in (401, 403, 407):
        return WS_CLOSE_UNAUTHENTICATED
    if status in (429, 503):
        # Transient upstream capacity — retryable, unlike 4401.
        return WS_CLOSE_TRY_AGAIN_LATER
    return 1011


async def _proxy_websocket(
    websocket: WebSocket,
    *,
    remote_url: str,
    remote_headers: Dict[str, str] | None = None,
) -> None:
    try:
        from websockets.exceptions import ConnectionClosed, InvalidHandshake
    except Exception:
        await websocket.close(code=4404)
        return

    await websocket.accept()

    try:
        try:
            remote = await _connect_remote(remote_url, remote_headers or {})
        except InvalidHandshake as exc:
            # The upstream rejected the upgrade — almost always a refused
            # credential. Relay it as a terminal auth close so the client
            # stops instead of reconnecting forever.
            await websocket.close(code=_upstream_handshake_close_code(exc))
            return
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
            # Relay the upstream's close code instead of flattening it.
            # 4401/4403 are terminal by contract — clients must not
            # reconnect on them — so a proxy that reports 1000 (or 1011)
            # turns an authorization refusal into an infinite retry loop.
            close_code = getattr(remote, "close_code", None)
            close_reason = getattr(remote, "close_reason", None) or ""
        try:
            if isinstance(close_code, int) and close_code != 1005:
                await websocket.close(code=close_code, reason=str(close_reason)[:120])
            else:
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
    agent_host_resources: "AgentHostResources | None" = None,
    default_host_workspace_root: str | None = None,
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
        default_host_workspace_root: Optional explicit host environment composed at
            startup and advertised as the AgentHost's default binding.
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
    # Connect tickets exist because browsers cannot set WebSocket headers.
    # A surface reached only by server-to-server credentials declines them
    # (``proxy_assertion``): minting one there would move a proxy-derived
    # credential into a query string and hand it a fresh TTL, extending
    # the lifetime of the assertion that bought it.
    if authenticator.supports_connect_tickets:
        register_ticket_route(app, authenticator)

    remote_ws_url = getattr(spec, "remote_ws_url", None)
    if remote_ws_url:
        # Validated at startup: a hop that distinguishes its own users
        # cannot present the upstream a single shared identity.
        upstream_credential = _resolve_upstream_credential(
            spec, authenticator, auth_token=auth_token
        )
        app.state.upstream_credential = upstream_credential

        async def _proxy_to(websocket: WebSocket, suffix: str) -> None:
            # The upstream credential is derived per connection from the
            # principal this hop authenticated, so identity — not just
            # bytes — crosses the hop.
            try:
                remote_headers = upstream_credential.headers_for(
                    getattr(websocket.state, "principal", None)
                )
            except Exception:
                await websocket.close(code=WS_CLOSE_UNAUTHENTICATED)
                return
            url = _compose_remote_ws_url(
                remote_ws_url,
                suffix=suffix,
                websocket=websocket,
            )
            await _proxy_websocket(
                websocket, remote_url=url, remote_headers=remote_headers
            )

        async def _ws_proxy(websocket: WebSocket):
            await _proxy_to(websocket, "")

        async def _ws_realtime_proxy(websocket: WebSocket):
            await _proxy_to(websocket, "/realtime")

        async def _ws_terminal_proxy(websocket: WebSocket):
            await _proxy_to(websocket, "/terminal")

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
    if agent_host_resources is None:
        from omniagents.execution import AgentHostResources

        agent_host_resources = AgentHostResources.create(
            f"standalone-{secrets.token_hex(8)}"
        )
    if default_host_workspace_root is not None:
        from omniagents.execution import register_host_environment

        default_environment = register_host_environment(
            agent_host_resources,
            default_host_workspace_root,
            owner_user_id="token_user" if auth_token else None,
        )
        app.state.default_workspace_id = default_environment.workspace_id
        app.state.default_environment_id = default_environment.environment_id
    service = AgentService(spec, agent_host_resources)
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
        default_workspace_id=getattr(app.state, "default_workspace_id", None),
        default_environment_id=getattr(app.state, "default_environment_id", None),
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
        realtime_service: Any
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
            # Dynamic attribute — AgentService doesn't declare it.
            service.realtime_service = realtime_service  # type: ignore[attr-defined]
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
                    if cols is None or rows is None:
                        continue
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
