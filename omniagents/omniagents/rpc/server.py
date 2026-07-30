"""Server-side JSON-RPC implementation for FastAPI.

Adds debug logging for all calls and notifications when OMNIAGENTS_SERVER_DEBUG
is set or omniagents.core.debug.Debug is enabled.
"""

from collections.abc import Collection
from typing import Any, Dict, Mapping, Optional, TYPE_CHECKING, cast

if TYPE_CHECKING:
    from omniagents.rpc.transport import Transport
import inspect
import json
import os
import platform
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from .auth import RPC_ERROR_AUTHORIZATION_DENIED, AuthorizationDenied
from .base import RpcMethodsBase
from .generated.gui_v1.models import (
    CLIENT_REQUEST_MODELS,
    CLIENT_RESULT_MODELS,
    SERVER_NOTIFICATION_MODELS,
    InitializeParams,
    InitializeResult,
)
from .protocol_catalog import EXPERIMENTAL_NOTIFICATIONS, EXPERIMENTAL_REQUESTS
from omniagents import __version__
from omniagents.core.debug import Debug

DEBUG_SERVER = os.getenv("OMNIAGENTS_SERVER_DEBUG", "0").lower() in ("1", "true")
GUI_RPC_METHODS = frozenset(CLIENT_REQUEST_MODELS)
GUI_PROTOCOL_VERSION = "1.0.0"
GUI_EXPERIMENTAL_REQUESTS = frozenset(EXPERIMENTAL_REQUESTS)
GUI_EXPERIMENTAL_NOTIFICATIONS = frozenset(EXPERIMENTAL_NOTIFICATIONS)
GUI_OPTIONAL_NOTIFICATIONS = frozenset(
    {
        "token",
        "run_status",
        "tool_called",
        "tool_result",
        "queue_changed",
        "client_request_resolved",
        "tool_approval_resolved",
        "mcp_approval_resolved",
        "session_forked",
        "session_variables_changed",
    }
)
GUI_CAPABILITY_NAMES = frozenset(
    {
        "realtime",
        "mcp_apps",
        "client_functions",
        "approvals",
        "artifacts",
        "replay",
        "terminal",
    }
)
# The generated model maps collapse to an untyped join under mypy; pin the
# value type so class-level pydantic API (model_validate/model_fields) checks.
_REQUEST_MODELS = cast("Mapping[str, type[BaseModel]]", CLIENT_REQUEST_MODELS)
_RESULT_MODELS = cast("Mapping[str, type[BaseModel]]", CLIENT_RESULT_MODELS)


def _parse_semver(value: Any) -> Optional[tuple[int, int, int]]:
    if not isinstance(value, str):
        return None
    core = value.split("+", 1)[0].split("-", 1)[0]
    parts = core.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


class JsonRpcChannel:
    """Simple helper used by the server to send notifications."""

    def __init__(
        self,
        transport: "Transport",
        *,
        notification_models: Optional[Mapping[str, Any]] = None,
        enforce_initialization: bool = False,
    ) -> None:
        self.transport = transport
        self.notification_models = notification_models
        self.enforce_initialization = enforce_initialization
        # Authenticated principal (UserIdentity) resolved by the shared
        # connection auth dependency; None means anonymous/unauthenticated
        # surfaces (in-memory transports, tests).
        self.principal = None
        self.initialization_state = "new"
        self.disabled_notifications: frozenset[str] = frozenset()
        self.experimental_grants: frozenset[str] = frozenset()
        self.client_info: Optional[Dict[str, str]] = None

    async def async_call(
        self, method: str, args: Optional[Dict[str, Any]] = None
    ) -> None:
        if self.enforce_initialization:
            if self.initialization_state != "ready":
                return
            if method in self.disabled_notifications:
                return
            if (
                method in GUI_EXPERIMENTAL_NOTIFICATIONS
                and method not in self.experimental_grants
            ):
                return
        model = (
            self.notification_models.get(method)
            if self.notification_models is not None
            else None
        )
        if model is not None:
            known_args = {
                key: value
                for key, value in (args or {}).items()
                if key in model.model_fields
            }
            model.model_validate(known_args, strict=True)
        payload = {"jsonrpc": "2.0", "method": method, "params": args or {}}
        Debug.log(f"[srv]   → {method} payload={args or {}}")
        await self.transport.send_json(payload)

    async def send_result(self, call_id: Any, result: Any) -> None:
        payload = {"jsonrpc": "2.0", "id": call_id, "result": result}
        Debug.log(f"[srv]   ← result id={call_id} result={result}")
        await self.transport.send_json(payload)

    async def send_error(
        self,
        call_id: Any,
        code: int,
        message: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        msg_text = str(message) if message is not None else ""
        if not msg_text:
            msg_text = ""
        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": call_id,
            "error": {"code": int(code), "message": msg_text},
        }
        if data is not None:
            payload["error"]["data"] = data
        Debug.log(
            f"[srv]   ← error id={call_id} code={code} message={message} data={data}"
        )
        await self.transport.send_json(payload)

    # Backward compatibility property
    @property
    def websocket(self) -> Any:
        """Backward compatibility for code that accesses channel.websocket."""
        # Return the underlying websocket if it's a ServerWebSocketTransport
        if hasattr(self.transport, "websocket"):
            return self.transport.websocket
        return None


class JsonRpcEndpoint:
    """Minimal JSON-RPC 2.0 WebSocket endpoint."""

    def __init__(
        self,
        methods: RpcMethodsBase,
        allowed_methods: Optional[Collection[str]] = None,
        *,
        gui_handshake: bool = False,
        server_capabilities: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.methods = methods
        self.allowed_methods = (
            frozenset(allowed_methods) if allowed_methods is not None else None
        )
        self.gui_handshake = gui_handshake
        self.server_capabilities = dict(server_capabilities or {})

    def register_route(self, app: FastAPI, path: str, dependencies=None) -> None:
        dependencies = dependencies or []

        async def handler(websocket: WebSocket):
            from .transport.websocket import ServerWebSocketTransport
            from .transport import (
                TransportClosedError,
                TransportDecodeError,
                TransportError,
            )

            transport = ServerWebSocketTransport(websocket)
            await transport.connect()

            channel = JsonRpcChannel(
                transport,
                notification_models=(
                    SERVER_NOTIFICATION_MODELS if self.gui_handshake else None
                ),
                enforce_initialization=self.gui_handshake,
            )
            channel.principal = getattr(websocket.state, "principal", None)
            self.methods.channel = channel

            try:
                while transport.is_connected():
                    try:
                        data = await transport.receive_json()
                    except TransportClosedError:
                        break
                    except WebSocketDisconnect:
                        break
                    except TransportDecodeError as exc:
                        await self._handle_parse_error(channel, exc)
                        continue
                    except TransportError:
                        break
                    except Exception as exc:
                        await self._handle_internal_error(channel, None, exc)
                        continue

                    try:
                        await self._process_message(channel, data)
                    except Exception:
                        break
            finally:
                try:
                    if getattr(self.methods, "channel", None) is channel:
                        self.methods.channel = None
                except AttributeError:
                    pass
                await transport.disconnect()

        app.add_api_websocket_route(path, handler, dependencies=dependencies)

    async def _handle_parse_error(
        self, channel: JsonRpcChannel, exc: Exception
    ) -> None:
        await channel.send_error(
            None,
            -32700,
            "Parse error",
            {"message": str(exc)},
        )

    async def _handle_internal_error(
        self,
        channel: JsonRpcChannel,
        call_id: Any,
        exc: Exception,
        method: Optional[str] = None,
    ) -> None:
        if call_id is None:
            return
        from omniagents.core.session.event_log import ResyncRequiredError

        if isinstance(exc, ResyncRequiredError):
            await channel.send_error(call_id, exc.code, str(exc), exc.data)
            return
        data: Dict[str, Any] = {"type": exc.__class__.__name__, "message": str(exc)}
        if method is not None:
            data["method"] = method
        message_text = str(exc).strip()
        if not message_text:
            message_text = "Internal error"
        await channel.send_error(
            call_id,
            -32603,
            message_text,
            data,
        )

    async def _process_message(self, channel: JsonRpcChannel, data: Any) -> None:
        if not isinstance(data, dict):
            await channel.send_error(
                None,
                -32600,
                "Invalid Request",
                {"message": "Request must be an object"},
            )
            return

        call_id = data.get("id")
        version = data.get("jsonrpc")
        method = data.get("method")
        params = data.get("params") if "params" in data else data.get("args")

        Debug.log(f"[srv] ⇐ call id={call_id} method={method} params={params}")
        Debug.log(f"[srv] ⇐ message id={call_id} data={data}")

        if version != "2.0":
            await channel.send_error(
                call_id, -32600, "Invalid Request", {"message": "jsonrpc must be '2.0'"}
            )
            return

        if not isinstance(method, str) or not method:
            await channel.send_error(
                call_id,
                -32600,
                "Invalid Request",
                {"message": "method must be a string"},
            )
            return

        if params is None:
            params = {}

        if not isinstance(params, dict):
            if call_id is not None:
                await channel.send_error(
                    call_id,
                    -32602,
                    "Invalid params",
                    {"message": "params must be an object"},
                )
            return

        if self.gui_handshake:
            if method == "initialize":
                await self._initialize(channel, call_id, params)
                return
            if method == "initialized":
                await self._mark_initialized(channel, call_id, params)
                return
            if channel.initialization_state != "ready":
                if call_id is not None:
                    await channel.send_error(
                        call_id,
                        -32010,
                        "Connection is not initialized",
                        {
                            "kind": "initialization_required",
                            "state": channel.initialization_state,
                        },
                    )
                return
            if (
                method in GUI_EXPERIMENTAL_REQUESTS
                and method not in channel.experimental_grants
            ):
                if call_id is not None:
                    await channel.send_error(
                        call_id,
                        -32013,
                        "Experimental capability was not granted",
                        {
                            "kind": "capability_not_negotiated",
                            "capability": method,
                        },
                    )
                return

        if self.allowed_methods is not None and method not in self.allowed_methods:
            func = None
        else:
            func = getattr(self.methods, method, None)
        if not callable(func):
            if call_id is not None:
                await channel.send_error(
                    call_id,
                    -32601,
                    "Method not found",
                    {"method": method},
                )
            return

        request_model = (
            _REQUEST_MODELS.get(method)
            if self.gui_handshake or self.allowed_methods is not None
            else None
        )
        if request_model is not None:
            known_params = {
                key: value
                for key, value in params.items()
                if key in request_model.model_fields
            }
            try:
                params = request_model.model_validate(
                    known_params, strict=True
                ).model_dump(exclude_unset=True)
            except ValidationError as exc:
                if call_id is not None:
                    await channel.send_error(
                        call_id,
                        -32602,
                        "Invalid params",
                        {
                            "message": str(exc),
                            "method": method,
                            "errors": exc.errors(
                                include_url=False,
                                include_context=False,
                                include_input=False,
                            ),
                        },
                    )
                return

        # Central authorization: method containers that expose
        # ``authorize_rpc`` (AgentService, RealtimeService) get every
        # request checked against the connection's principal BEFORE
        # dispatch, so newly added methods inherit enforcement.
        authorizer = getattr(self.methods, "authorize_rpc", None)
        if callable(authorizer):
            try:
                maybe = authorizer(channel.principal, method, params)
                if inspect.isawaitable(maybe):
                    await maybe
            except AuthorizationDenied as exc:
                if call_id is not None:
                    await channel.send_error(
                        call_id,
                        RPC_ERROR_AUTHORIZATION_DENIED,
                        str(exc) or "Not authorized",
                        {"method": method, **exc.error_data()},
                    )
                return

        try:
            signature = inspect.signature(func)
            signature.bind(**params)
        except (TypeError, ValueError) as exc:
            if call_id is not None:
                await channel.send_error(
                    call_id,
                    -32602,
                    "Invalid params",
                    {"message": str(exc), "method": method},
                )
            return

        t_start = time.perf_counter()
        try:
            try:
                self.methods.channel = channel
            except AttributeError:
                pass
            result = func(**params)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            Debug.log(
                f"[profile] rpc method={method} id={call_id} "
                f"ms={(time.perf_counter() - t_start) * 1000:.1f} error=1"
            )
            await self._handle_internal_error(channel, call_id, exc, method)
            return

        t_handler = time.perf_counter()
        result_bytes = -1
        try:
            result_bytes = len(json.dumps(result, default=str))
        except Exception:
            pass
        Debug.log(
            f"[profile] rpc method={method} id={call_id} "
            f"handler_ms={(t_handler - t_start) * 1000:.1f} "
            f"result_bytes={result_bytes}"
        )

        if call_id is not None:
            result_model = (
                _RESULT_MODELS.get(method)
                if self.gui_handshake or self.allowed_methods is not None
                else None
            )
            if result_model is not None:
                try:
                    result_model.model_validate(result, strict=True)
                except ValidationError as exc:
                    await self._handle_internal_error(channel, call_id, exc, method)
                    return
            await channel.send_result(call_id, result)

    async def _initialize(
        self, channel: JsonRpcChannel, call_id: Any, params: Dict[str, Any]
    ) -> None:
        if call_id is None:
            return
        if channel.initialization_state != "new":
            await channel.send_error(
                call_id,
                -32011,
                "Connection initialization is already in progress or complete",
                {
                    "kind": "invalid_initialization_state",
                    "state": channel.initialization_state,
                },
            )
            return

        try:
            initialize = InitializeParams.model_validate(params, strict=True)
        except ValidationError as exc:
            await channel.send_error(
                call_id,
                -32602,
                "Invalid params",
                {
                    "method": "initialize",
                    "errors": exc.errors(
                        include_url=False,
                        include_context=False,
                        include_input=False,
                    ),
                },
            )
            return

        client_version = initialize.protocol_version
        parsed_client_version = _parse_semver(client_version)
        parsed_server_version = _parse_semver(GUI_PROTOCOL_VERSION)
        if (
            parsed_client_version is None
            or parsed_server_version is None
            or parsed_client_version[0] != parsed_server_version[0]
            or parsed_client_version > parsed_server_version
        ):
            await channel.send_error(
                call_id,
                -32012,
                "Unsupported protocol version",
                {
                    "kind": "protocol_version_mismatch",
                    "client_version": client_version,
                    "server_version": GUI_PROTOCOL_VERSION,
                },
            )
            return

        requested = initialize.capabilities
        disabled = requested.disabled_notifications
        invalid_disabled = sorted(
            name for name in disabled if name not in GUI_OPTIONAL_NOTIFICATIONS
        )
        if invalid_disabled:
            await channel.send_error(
                call_id,
                -32013,
                "A mandatory notification cannot be disabled",
                {
                    "kind": "mandatory_capability_disabled",
                    "disabled": invalid_disabled,
                },
            )
            return

        requested_experimental = requested.experimental_operations
        supported_experimental = (
            GUI_EXPERIMENTAL_REQUESTS | GUI_EXPERIMENTAL_NOTIFICATIONS
        )
        unsupported_experimental = sorted(
            name
            for name in requested_experimental
            if name not in supported_experimental
        )
        if unsupported_experimental:
            await channel.send_error(
                call_id,
                -32013,
                "Unsupported experimental capabilities",
                {
                    "kind": "capability_not_negotiated",
                    "unsupported_capabilities": unsupported_experimental,
                },
            )
            return

        negotiated: Dict[str, Any] = {
            name: bool(getattr(requested, name))
            and bool(self.server_capabilities.get(name, False))
            for name in GUI_CAPABILITY_NAMES
        }
        negotiated["experimental_operations"] = sorted(requested_experimental)
        negotiated["disabled_notifications"] = sorted(disabled)

        channel.client_info = {
            "name": initialize.identity.name,
            "version": initialize.identity.version,
        }
        channel.disabled_notifications = frozenset(disabled)
        channel.experimental_grants = frozenset(requested_experimental)
        channel.initialization_state = "initialize_accepted"
        result = InitializeResult.model_validate(
            {
                "protocol_version": GUI_PROTOCOL_VERSION,
                "identity": {"name": "omniagents", "version": __version__},
                "platform": {
                    "os": platform.system().lower(),
                    "arch": platform.machine().lower(),
                },
                "capabilities": negotiated,
            },
            strict=True,
        )
        await channel.send_result(call_id, result.model_dump())

    async def _mark_initialized(
        self, channel: JsonRpcChannel, call_id: Any, params: Dict[str, Any]
    ) -> None:
        if channel.initialization_state != "initialize_accepted":
            if call_id is not None:
                await channel.send_error(
                    call_id,
                    -32011,
                    "Initialize must be accepted before initialized",
                    {
                        "kind": "invalid_initialization_state",
                        "state": channel.initialization_state,
                    },
                )
            return
        if params:
            if call_id is not None:
                await channel.send_error(
                    call_id,
                    -32013,
                    "initialized does not accept parameters",
                    {"kind": "invalid_capabilities"},
                )
            return
        channel.initialization_state = "ready"
        if call_id is not None:
            await channel.send_result(call_id, None)
