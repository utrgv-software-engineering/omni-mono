"""Remote agent implementation using WebSocket RPC."""

from typing import Any, AsyncGenerator, List, Dict, Optional

from agents import RunConfig, StreamEvent
import asyncio
import os
from importlib.metadata import version
from pydantic import ValidationError

from omniagents.rpc import RpcMethodsBase, JsonRpcClient, RpcError
from omniagents.core.interfaces.agent import UnifiedAgentBase
from omniagents.rpc.generated.gui_v1 import (
    CLIENT_REQUEST_MODELS,
    SERVER_NOTIFICATION_MODELS,
)
from omniagents.rpc.lifecycle import (
    DEFAULT_LIFECYCLE_POLICY,
    LifecyclePolicy,
    ReconnectController,
    RpcConnectionClosedError,
    RpcTimeoutError,
    classify_rpc_error_code,
)
from omniagents.rpc.transport import TransportError
from ._events import map_notification_to_event
from omniagents.core.debug import Debug

#: Internal queue marker pushed when the RPC connection drops so that
#: ``run_streamed`` consumers fail fast instead of waiting forever for a
#: ``run_end`` that can no longer arrive.
_DISCONNECT_SENTINEL = "__connection_closed__"

REMOTE_AGENT_CAPABILITIES: Dict[str, Any] = {
    "realtime": False,
    "mcp_apps": False,
    "client_functions": True,
    "approvals": True,
    "artifacts": False,
    "replay": True,
    "terminal": False,
    "experimental_operations": [],
    "disabled_notifications": [],
}


def _is_permanent_connect_error(exc: Exception) -> bool:
    """Classify a connect/handshake failure as permanent (non-retryable).

    Permanent failures: typed handshake rejections (protocol/capability
    mismatch, invalid request), a permanent connection close (auth), or an
    HTTP-level auth/not-found rejection during the WebSocket upgrade.
    """
    if isinstance(exc, RpcError):
        return classify_rpc_error_code(exc.code) == "permanent"
    if isinstance(exc, RpcConnectionClosedError):
        return exc.permanent
    # Walk the cause chain looking for an HTTP status from the WS upgrade
    # (websockets raises InvalidStatus/InvalidStatusCode on e.g. 401/403).
    seen: set[int] = set()
    cause: BaseException | None = exc
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        status = getattr(cause, "status_code", None)
        if status is None:
            response = getattr(cause, "response", None)
            status = getattr(response, "status_code", None)
        if isinstance(status, int) and status in (401, 403, 404):
            return True
        cause = cause.__cause__ or cause.__context__
    return False


def _validate_request_params(method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    model = CLIENT_REQUEST_MODELS[method].model_validate(params)
    return model.model_dump(exclude_unset=True)


def _adapt_notification_params(method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    model = SERVER_NOTIFICATION_MODELS.get(method)
    if model is None:
        return params
    try:
        return model.model_validate(params).model_dump(exclude_unset=True)
    except ValidationError:
        return params


class RemoteAgent(UnifiedAgentBase):
    """Agent that forwards operations to a remote server via WebSocket RPC."""

    # Agent interface properties for compatibility
    input_type = str
    output_type = str
    name = "RemoteAgent"
    tools: List[Any] = []
    instructions: str = ""
    handoffs: List[Any] = []
    model = "remote"
    model_settings: Dict[str, Any] = {}
    mcp_servers = None

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        variables: Dict[str, Any] | None = None,
        context: Any | None = None,
        transport: Any | None = None,
        policy: LifecyclePolicy = DEFAULT_LIFECYCLE_POLICY,
    ) -> None:
        """
        Initialize remote agent.

        Args:
            url: WebSocket URL of the remote agent server (optional if transport provided)
            token: Optional authentication token
            variables: Optional variables dictionary for server-side context construction
            context: Optional pre-built context to use as-is
            transport: Optional custom transport to use instead of WebSocket
            policy: Lifecycle policy (timeouts, backoff, retry limits)
        """
        super().__init__()

        # The token is sent as an Authorization header at connect time —
        # never appended to the URL (see omniagents.rpc.auth).
        self._url = url
        self._token = token
        self._transport = transport
        self._policy = policy
        self._client_generation = 0
        self._client: JsonRpcClient | None = None
        self._run_lock: asyncio.Lock = asyncio.Lock()
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._session_id: str | None = None
        self._current_run_id: str | None = None
        self._variables = variables
        self._context = context
        self._last_run_end: Dict[str, Any] | None = None

    def get_all_tools(self) -> List[Any]:
        """Get all available tools (empty for remote agents)."""
        return []

    def reset_run_state(self) -> None:
        """Reset agent state (no-op for remote agents)."""
        pass

    def run_streamed(
        self, messages: List[Dict[str, str]], *, run_config: RunConfig | None = None
    ) -> Any:
        """Return an object that mimics Runner.run_streamed()."""

        async def _events_gen() -> AsyncGenerator[StreamEvent, None]:
            await self._ensure_client()

            # Extract prompt from last user message
            prompt = messages[-1]["content"] if messages else ""

            # Prepare start_run parameters
            start_params = {"prompt": prompt, "session_id": self._session_id}

            # Only send variables/context on first run (when we don't have a session_id yet)
            if self._session_id is None:
                if self._variables is not None:
                    start_params["variables"] = self._variables
                if self._context is not None:
                    start_params["context"] = self._context

            # Pass workflow_name from run_config if provided
            if run_config is not None:
                if run_config.workflow_name:
                    start_params["workflow_name"] = run_config.workflow_name
                if run_config.group_id:
                    start_params["group_id"] = run_config.group_id

            # Start run on remote server
            start_params = _validate_request_params("start_run", start_params)
            resp = await self._client.other.start_run(**start_params)

            if run_config is None:
                Debug.log(f"[cli] ⇢ raw start_run resp={resp!r}")
                try:
                    print(f"[cli] ⇢ resp.result={getattr(resp, 'result', None)!r}")
                except Exception:
                    pass

            # Extract and cache session_id if first run
            new_session_id = self._extract_session_id(resp)
            if new_session_id:
                if self._session_id is None:
                    Debug.log(f"[cli] ⎆ obtained session_id={new_session_id[:8]}")
                self._session_id = new_session_id

            run_id = self._extract_run_id(resp)
            Debug.log(f"[cli] ▶ run_id={run_id}  prompt={prompt[:60]!r}")

            # Store current run_id for potential cancellation
            self._current_run_id = run_id

            # Stream events until run_end
            while True:
                msg = await self._event_queue.get()
                if msg["method"] == _DISCONNECT_SENTINEL:
                    params = msg.get("params") or {}
                    # Ignore markers from a previous connection generation:
                    # a fresh client is already serving this run.
                    if params.get("generation") != self._client_generation:
                        continue
                    self._current_run_id = None
                    raise RpcConnectionClosedError(
                        params.get("message") or "Connection lost during run",
                        permanent=bool(params.get("permanent")),
                    )
                if msg["method"] == "run_end" and (
                    run_id is None or msg["params"].get("run_id") == run_id
                ):
                    Debug.log(f"[cli] ◀ run_end  run_id={run_id}")
                    # Cache last run_end payload for UI to inspect (errors, usage, model)
                    try:
                        self._last_run_end = dict(msg.get("params") or {})
                    except Exception:
                        self._last_run_end = None
                    # Clear current run_id when run ends
                    self._current_run_id = None
                    break

                event = map_notification_to_event(msg["method"], msg["params"])
                if event:
                    Debug.log(f"[cli]   ← {msg['method']} details={msg['params']}")
                    yield event

        class _Result:
            def __init__(self, gen):
                self._gen = gen

            def stream_events(self):
                return self._gen

        return _Result(_events_gen())

    def get_last_run_end(self) -> Optional[Dict[str, Any]]:
        """Return the payload from the most recent run_end notification.

        Contains fields like end_reason, error (if any), usage, and model info.
        """
        return self._last_run_end

    async def list_sessions(
        self, limit: Optional[int] = None, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Fetch session metadata (newest-first) from the server.

        ``limit=None`` fetches everything and omits the params entirely, so
        the call stays compatible with servers that predate pagination.
        """
        await self._ensure_client()
        if limit is None:
            resp = await self._client.other.list_sessions()
        else:
            params = _validate_request_params(
                "list_sessions", {"limit": limit, "offset": offset}
            )
            resp = await self._client.other.list_sessions(**params)
        return getattr(resp, "result", resp)

    async def archive_session(self, session_id: str) -> None:
        """Archive session on the server."""
        await self._ensure_client()
        resp = await self._client.other.archive_session(session_id=session_id)
        return getattr(resp, "result", resp)

    async def stop_run(self) -> bool:
        """
        Stop the currently running agent execution.

        Returns:
            bool: True if the run was successfully cancelled, False otherwise
        """
        if not self._current_run_id:
            return False

        await self._ensure_client()
        try:
            resp = await self._client.other.stop_run(run_id=self._current_run_id)
            result = getattr(resp, "result", resp)
            if result:
                # Clear the run_id since we've cancelled it
                self._current_run_id = None
            return result
        except Exception as e:
            Debug.log(f"[cli] ✗ stop_run failed: {e}")
            return False

    async def _ensure_client(self):
        """Ensure RPC client is connected, retrying per the lifecycle policy.

        Transient connect failures back off exponentially; permanent
        failures (auth rejection, protocol/capability mismatch) and an
        exhausted retry budget re-raise immediately. Event replay after a
        reconnect is a separate concern — sessions resume server-side via
        ``session_id``, and finer-grained resume hooks belong to the
        durable-replay work.
        """
        if self._client is not None:
            return

        # Build methods object that forwards events into queue
        self._methods = _ClientEventMethods(self._event_queue)
        controller = ReconnectController(self._policy)

        while True:
            try:
                self._client = await self._build_client().__aenter__()
                controller.record_success()
                break
            except (
                TransportError,
                RpcTimeoutError,
                RpcConnectionClosedError,
                RpcError,
            ) as exc:
                self._client = None
                decision = controller.record_failure(
                    permanent=_is_permanent_connect_error(exc)
                )
                if not decision.retry:
                    raise
                Debug.log(
                    f"[cli] ↻ connect attempt {decision.attempt} failed ({exc}); "
                    f"retrying in {decision.delay:.2f}s"
                )
                await asyncio.sleep(decision.delay)

        self._client_generation += 1
        generation = self._client_generation
        self._client.on_disconnect(
            lambda error: self._on_client_disconnect(generation, error)
        )

    def _build_client(self) -> JsonRpcClient:
        """Construct an unconnected JsonRpcClient for the configured target."""
        client_info = {
            "name": "omniagents-python",
            "version": version("omniagents"),
        }
        if self._transport:
            return JsonRpcClient(
                self._transport,
                self._methods,
                client_info=client_info,
                capabilities=REMOTE_AGENT_CAPABILITIES,
                policy=self._policy,
            )
        if self._url:
            return JsonRpcClient.from_url(
                self._url,
                self._methods,
                client_info=client_info,
                capabilities=REMOTE_AGENT_CAPABILITIES,
                token=self._token,
                policy=self._policy,
            )
        raise ValueError("Either url or transport must be provided")

    def _on_client_disconnect(
        self, generation: int, error: RpcConnectionClosedError
    ) -> None:
        """Wake any streaming consumer when the connection drops."""
        if generation != self._client_generation:
            return
        self._client = None
        try:
            self._event_queue.put_nowait(
                {
                    "method": _DISCONNECT_SENTINEL,
                    "params": {
                        "generation": generation,
                        "message": str(error),
                        "permanent": error.permanent,
                    },
                }
            )
        except asyncio.QueueFull:  # pragma: no cover - unbounded by default
            pass

    def _extract_run_id(self, resp) -> Optional[str]:
        """Extract run_id from various response formats."""
        # Direct dict response
        if isinstance(resp, dict):
            return resp.get("run_id")

        # RpcResponse with result attribute
        result = getattr(resp, "result", None)
        if result is None:
            return None

        # Result is dict
        if isinstance(result, dict):
            return result.get("run_id")

        # Result is JSON string
        if isinstance(result, str):
            try:
                import json

                parsed = json.loads(result)
                if isinstance(parsed, dict):
                    return parsed.get("run_id")
            except Exception:
                # Try Python dict string format
                try:
                    import ast

                    parsed = ast.literal_eval(result)
                    if isinstance(parsed, dict):
                        return parsed.get("run_id")
                except Exception:
                    pass

        return None

    def _extract_session_id(self, resp) -> Optional[str]:
        """Extract session_id from various response formats."""
        # Same logic as run_id extraction
        if isinstance(resp, dict):
            return resp.get("session_id")

        result = getattr(resp, "result", None)
        if result is None:
            return None

        if isinstance(result, dict):
            return result.get("session_id")

        if isinstance(result, str):
            try:
                import json

                parsed = json.loads(result)
                if isinstance(parsed, dict):
                    return parsed.get("session_id")
            except Exception:
                try:
                    import ast

                    parsed = ast.literal_eval(result)
                    if isinstance(parsed, dict):
                        return parsed.get("session_id")
                except Exception:
                    pass

        return None


class _ClientEventMethods(RpcMethodsBase):
    """RPC methods for receiving events from the server."""

    def __init__(self, queue: asyncio.Queue):
        super().__init__()
        self._queue = queue

    async def _push(self, method: str, **params):
        """Push event to queue."""
        params = _adapt_notification_params(method, params)
        await self._queue.put({"method": method, "params": params})
        return True

    def __getattr__(self, method: str):
        if method.startswith("_"):
            raise AttributeError(method)

        async def receive_notification(**params):
            return await self._push(method, **params)

        return receive_notification

    # Event notification methods
    async def tool_called(self, **params):
        return await self._push("tool_called", **params)

    async def tool_result(self, **params):
        return await self._push("tool_result", **params)

    async def message_output(self, **params):
        return await self._push("message_output", **params)

    async def token(self, **params):
        return await self._push("token", **params)

    async def run_end(self, **params):
        return await self._push("run_end", **params)

    async def run_status(self, **params):
        return await self._push("run_status", **params)

    async def run_started(self, **params):
        return await self._push("run_started", **params)

    async def client_request(self, **params):
        return await self._push("client_request", **params)
