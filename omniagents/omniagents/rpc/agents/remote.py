"""Remote agent implementation using WebSocket RPC."""

from typing import Any, AsyncGenerator, List, Dict, Optional

from agents import RunConfig, StreamEvent
import asyncio
import os

from omniagents.rpc import RpcMethodsBase, JsonRpcClient
from omniagents.core.interfaces.agent import UnifiedAgentBase
from ._events import map_notification_to_event
from omniagents.core.debug import Debug


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

    def __init__(self, url: str, token: str | None = None, context: Dict[str, Any] | None = None) -> None:
        """
        Initialize remote agent.
        
        Args:
            url: WebSocket URL of the remote agent server
            token: Optional authentication token
            context: Optional context dictionary for dynamic instructions
        """
        super().__init__()
        
        # Add token to URL if provided
        if token:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}token={token}"
        
        self._url = url
        self._client: JsonRpcClient | None = None
        self._run_lock: asyncio.Lock = asyncio.Lock()
        self._event_queue: asyncio.Queue = asyncio.Queue()
        self._session_id: str | None = None
        self._context = context or {}

    def get_all_tools(self) -> List[Any]:
        """Get all available tools (empty for remote agents)."""
        return []

    def reset_run_state(self) -> None:
        """Reset agent state (no-op for remote agents)."""
        pass

    def run_streamed(self, messages: List[Dict[str, str]], *, run_config: RunConfig | None = None) -> Any:
        """Return an object that mimics Runner.run_streamed()."""

        async def _events_gen() -> AsyncGenerator[StreamEvent, None]:
            await self._ensure_client()

            # Extract prompt from last user message
            prompt = messages[-1]["content"] if messages else ""

            # Start run on remote server
            resp = await self._client.other.start_run(
                prompt=prompt, 
                session_id=self._session_id,
                context=self._context
            )

            if Debug.enabled() and run_config is None:  # First call heuristic
                print(f"[cli] ⇢ raw start_run resp={resp!r}")
                try:
                    print(f"[cli] ⇢ resp.result={getattr(resp, 'result', None)!r}")
                except Exception:
                    pass

            # Extract and cache session_id if first run
            new_session_id = self._extract_session_id(resp)
            if new_session_id:
                if Debug.enabled() and self._session_id is None:
                    print(f"[cli] ⎆ obtained session_id={new_session_id[:8]}")
                self._session_id = new_session_id

            run_id = self._extract_run_id(resp)
            if Debug.enabled():
                print(f"[cli] ▶ run_id={run_id}  prompt={prompt[:60]!r}")

            # Stream events until run_end
            while True:
                msg = await self._event_queue.get()
                if msg["method"] == "run_end" and (
                    run_id is None or msg["params"].get("run_id") == run_id
                ):
                    if Debug.enabled():
                        print(f"[cli] ◀ run_end  run_id={run_id}")
                    break
                
                event = map_notification_to_event(msg["method"], msg["params"])
                if event:
                    if Debug.enabled():
                        print(f"[cli]   ← {msg['method']} details={msg['params']}")
                    yield event

        class _Result:
            def __init__(self, gen):
                self._gen = gen

            def stream_events(self):
                return self._gen

        return _Result(_events_gen())

    async def list_sessions(self) -> List[Dict[str, Any]]:
        """Fetch list of session metadata from the server."""
        await self._ensure_client()
        resp = await self._client.other.list_sessions()
        return getattr(resp, "result", resp)

    async def archive_session(self, session_id: str) -> None:
        """Archive session on the server."""
        await self._ensure_client()
        resp = await self._client.other.archive_session(session_id=session_id)
        return getattr(resp, "result", resp)

    async def _ensure_client(self):
        """Ensure RPC client is connected."""
        if self._client is None:
            # Build methods object that forwards events into queue
            self._methods = _ClientEventMethods(self._event_queue)
            self._client = await JsonRpcClient.from_url(self._url, self._methods).__aenter__()

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
        await self._queue.put({"method": method, "params": params})
        return True

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

    async def tool_approval_request(self, **params):
        return await self._push("tool_approval_request", **params)