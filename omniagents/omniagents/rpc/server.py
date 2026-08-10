"""Server-side JSON-RPC implementation for FastAPI.

Adds debug logging for all calls and notifications when OMNIAGENTS_SERVER_DEBUG
is set or omniagents.core.debug.Debug is enabled.
"""

import asyncio
import inspect
import json
import os
import platform
import time
from collections.abc import Collection
from typing import TYPE_CHECKING, Any, Dict, Mapping, Optional, cast

if TYPE_CHECKING:
    from omniagents.rpc.transport import Transport

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from omniagents import __version__
from omniagents.core.debug import Debug

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
        "elicitation_resolved",
        "session_forked",
        "session_variables_changed",
        "account_changed",
        "mcp_server_status_changed",
        "fs_events",
        "fs_rescan_required",
        "fs_transfer_progress",
        "git_operation_progress",
        # Item revisions are a convenience push: everything they carry is
        # readable via get_plan / get_run_diff / get_item, so a client that
        # would rather refetch may switch them off.
        "item_updated",
    }
)
# Request params that carry credential material. Redacted from every
# debug/log line the server writes — a secret a client sends the server
# must never be reproducible from process output.
SENSITIVE_PARAM_KEYS = frozenset({"api_key", "code"})

# Dict-valued params whose VALUES are credential channels (MCP server
# ``headers``/``env`` — the write-only secret model): the whole mapping
# is masked, matching the ``{name: {is_set}}`` read-path redaction.
SENSITIVE_CONTAINER_KEYS = frozenset({"headers", "env"})


def _trace_agent_host_control(
    stage: str, method: str, call_id: Any, started_at: float
) -> None:
    """Emit a correlation-safe trace for AgentHost control-plane requests."""
    if not method.startswith("agent_host_"):
        return
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    print(
        "[agent-host-control-server] "
        f"stage={stage} id={call_id} method={method} elapsed_ms={elapsed_ms:.1f}",
        flush=True,
    )


def redact_params(params: Any) -> Any:
    """Copy of *params* with credential-bearing values masked for logging."""
    if not isinstance(params, dict):
        return params
    return {
        key: (
            "[REDACTED]"
            if (key in SENSITIVE_PARAM_KEYS and value)
            or (key in SENSITIVE_CONTAINER_KEYS and isinstance(value, dict) and value)
            else redact_params(value) if isinstance(value, dict) else value
        )
        for key, value in params.items()
    }


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

    def accepts_notification(self, method: str) -> bool:
        """Per-connection notification policy for *method*.

        The single definition of "may this connection see this
        notification": handshake state, ``disabled_notifications``, and
        experimental capability grants. ``async_call`` consults it on the
        live path; the replay paths consult it through
        ``omniagents.core.agents.service.channel_accepts_notification`` so
        journaled events returned in an RPC *result* obey the same policy
        (tkt_m3nx8D3Rfngw).
        """
        if not self.enforce_initialization:
            return True
        if self.initialization_state != "ready":
            return False
        if method in self.disabled_notifications:
            return False
        if (
            method in GUI_EXPERIMENTAL_NOTIFICATIONS
            and method not in self.experimental_grants
        ):
            return False
        return True

    async def async_call(
        self, method: str, args: Optional[Dict[str, Any]] = None
    ) -> None:
        if not self.accepts_notification(method):
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
        agent_host_id: str | None = None,
        default_workspace_id: str | None = None,
        default_environment_id: str | None = None,
    ) -> None:
        self.methods = methods
        self.allowed_methods = (
            frozenset(allowed_methods) if allowed_methods is not None else None
        )
        self.gui_handshake = gui_handshake
        self.server_capabilities = dict(server_capabilities or {})
        resources = getattr(methods, "agent_host_resources", None)
        resolved_host_id = agent_host_id or getattr(resources, "agent_host_id", None)
        if gui_handshake and not resolved_host_id:
            raise ValueError("gui_handshake endpoints require an agent_host_id")
        self.agent_host = {
            "agent_host_id": resolved_host_id,
            "default_workspace_id": default_workspace_id,
            "default_environment_id": default_environment_id,
        }

    def register_route(self, app: FastAPI, path: str, dependencies=None) -> None:
        dependencies = dependencies or []

        async def handler(websocket: WebSocket):
            from .transport import (
                TransportClosedError,
                TransportDecodeError,
                TransportError,
            )
            from .transport.websocket import ServerWebSocketTransport

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
            # Method containers that track their connected channels (for
            # connection-wide broadcasts such as ``account_changed``)
            # expose register/unregister hooks; plain containers don't.
            register_channel = getattr(self.methods, "register_channel", None)
            if callable(register_channel):
                register_channel(channel)

            in_flight: set[asyncio.Task[None]] = set()

            def consume_request_task(task: asyncio.Task[None]) -> None:
                in_flight.discard(task)
                if not task.cancelled():
                    # _process_message translates method failures into JSON-RPC
                    # errors.  Consume transport-level failures so detached
                    # tasks never produce "exception was never retrieved".
                    task.exception()

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

                    # Initialization and notifications remain ordered. Once a
                    # GUI connection is ready, requests are independent
                    # JSON-RPC exchanges identified by id and may complete out
                    # of order. Dispatching them concurrently prevents a slow
                    # environment provision from blocking unrelated control or
                    # UI calls on the same connection.
                    is_request = isinstance(data, dict) and data.get("id") is not None
                    handshake_pending = (
                        self.gui_handshake and channel.initialization_state != "ready"
                    )
                    if not is_request or handshake_pending:
                        try:
                            await self._process_message(channel, data)
                        except Exception:
                            break
                        continue

                    task = asyncio.create_task(self._process_message(channel, data))
                    in_flight.add(task)
                    task.add_done_callback(consume_request_task)
                    # Let a fast request finish before another receive, while a
                    # genuinely blocking request yields immediately and allows
                    # the next JSON-RPC envelope to be accepted.
                    await asyncio.sleep(0)
            finally:
                for task in in_flight:
                    task.cancel()
                if in_flight:
                    await asyncio.gather(*in_flight, return_exceptions=True)
                unregister_channel = getattr(self.methods, "unregister_channel", None)
                if callable(unregister_channel):
                    unregister_channel(channel)
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
        from omniagents.core.agents.elicitation import ElicitationError
        from omniagents.core.agents.mcp_admin import McpRpcError
        from omniagents.core.conversation.service import ConversationRpcError
        from omniagents.core.providers.accounts import AccountRpcError
        from omniagents.core.session.event_log import ResyncRequiredError
        from omniagents.execution.errors import (
            RPC_ERROR_EXECUTION_RESOLUTION,
            ExecutionResolutionError,
        )
        from omniagents.rpc.filesystem import FilesystemRpcError
        from omniagents.rpc.git import GitRpcError

        if isinstance(exc, ExecutionResolutionError):
            await channel.send_error(
                call_id,
                RPC_ERROR_EXECUTION_RESOLUTION,
                str(exc),
                exc.data,
            )
            return

        if isinstance(
            exc,
            (
                ResyncRequiredError,
                AccountRpcError,
                ElicitationError,
                McpRpcError,
                FilesystemRpcError,
                ConversationRpcError,
                GitRpcError,
            ),
        ):
            await channel.send_error(call_id, exc.code, str(exc), exc.data)
            return
        if isinstance(exc, AuthorizationDenied):
            denied_data = exc.error_data()
            if method is not None:
                denied_data["method"] = method
            await channel.send_error(
                call_id,
                RPC_ERROR_AUTHORIZATION_DENIED,
                str(exc) or "Not authorized",
                denied_data,
            )
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

        safe_params = redact_params(params)
        Debug.log(f"[srv] ⇐ call id={call_id} method={method} params={safe_params}")
        Debug.log(
            f"[srv] ⇐ message id={call_id} "
            f"data={redact_params(data) if isinstance(data, dict) else data}"
        )

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

        control_started_at = time.perf_counter()
        _trace_agent_host_control("received", method, call_id, control_started_at)

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

        # Resolve resource and execution identity before authorization or
        # handler dispatch. AgentService implements this hook through the
        # central OperationRouter; transports with no execution model may omit
        # it. Resolution failures are typed and no handler is invoked.
        request_context = None
        preparer = getattr(self.methods, "prepare_rpc_context", None)
        if callable(preparer):
            try:
                request_context = preparer(channel.principal, method, params)
                if inspect.isawaitable(request_context):
                    request_context = await request_context
            except Exception as exc:
                await self._handle_internal_error(channel, call_id, exc, method)
                return

        # Bind the resolved lease for the whole request, including
        # authorization, handler dispatch, and result validation. Server
        # functions and tools can therefore consume the same immutable lease
        # without resolving identity a second time.
        context_token = None
        if request_context is not None:
            from omniagents.execution import (
                ADDRESSING_GUARD_PARAMS,
                bind_request_context,
            )

            context_token = bind_request_context(request_context)

            # The router has consumed the addressing guards; forward one to
            # the handler only when its signature names it as a real argument.
            handler_params = inspect.signature(func).parameters
            if not any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in handler_params.values()
            ):
                for guard in ADDRESSING_GUARD_PARAMS:
                    if guard in params and guard not in handler_params:
                        del params[guard]

        try:
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
                _trace_agent_host_control(
                    "handler_start", method, call_id, control_started_at
                )
                result = func(**params)
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:
                _trace_agent_host_control(
                    "handler_error", method, call_id, control_started_at
                )
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
                _trace_agent_host_control(
                    "response_sent", method, call_id, control_started_at
                )
        finally:
            if context_token is not None:
                from omniagents.execution import reset_request_context

                reset_request_context(context_token)

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
                "agent_host": self.agent_host,
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
