from __future__ import annotations

"""RemoteAgentProxy – minimal Agent-like object that forwards chat
messages to a remote OmniAgents server via fastapi-websocket-rpc.

Design goal: conform *just enough* to the interface expected by
omniagents.cli_runner and AgentApp so we can drop it in anywhere a
local `agents.Agent` instance would normally be used.
"""

from typing import Any, AsyncGenerator
import asyncio
import json
import os
import uuid
import ast

from fastapi_websocket_rpc import RpcMethodsBase, WebSocketRpcClient
from agents import StreamEvent, RunItemStreamEvent, ItemHelpers, Runner, RunConfig  # type: ignore

__all__ = ["RemoteAgentProxy"]


class RemoteAgentProxy:
    """Duck-types as an Agent but never calls OpenAI locally."""

    # Runner compatibility: declare minimal schema hints so that
    # Runner._get_output_schema/_get_input_schema do not raise.
    input_type = str  # Accepts simple chat strings
    output_type = str  # Produces assistant messages as plain text
    name = "RemoteAgentProxy"
    tools: list = []
    instructions: str = ""
    model = "remote"
    model_settings: dict = {}
    mcp_servers = None

    def __init__(self, ws_url: str, token: str | None = None) -> None:
        if token:
            sep = "&" if "?" in ws_url else "?"
            ws_url = f"{ws_url}{sep}token={token}"
        self._url = ws_url
        self._client: WebSocketRpcClient | None = None
        self._run_lock: asyncio.Lock = asyncio.Lock()
        # Event queue populated by Rpc callback methods implemented below
        self._event_q: asyncio.Queue = asyncio.Queue()

        # ------------------------------------------------------------------
        # Session tracking (kept client-side) --------------------------------
        # ------------------------------------------------------------------
        # The first call to *start_run* will return a session_id.  We cache it
        # and pass it back on subsequent runs so the server can append to the
        # same chat history instead of starting a fresh Session every time.
        self._session_id: str | None = None

        # ------------------------------------------------------------------
        # Optional verbose logging (client-side) controlled by env var
        # ------------------------------------------------------------------
        self.DEBUG_CLIENT = os.getenv("OMNIAGENTS_CLIENT_DEBUG", "0") in ("1", "true", "True")

    # ------------------------------------------------------------------
    # Agent-like surface ------------------------------------------------
    # ------------------------------------------------------------------
    async def get_all_tools(self):  # noqa: D401 – Runner may call this
        return []

    def reset_run_state(self):  # noqa: D401 – SafeAgent compatibility
        pass

    # ------------------------------------------------------------------
    # Public helper returning a *Runner-compatible* streaming wrapper
    # ------------------------------------------------------------------
    def run_streamed(self, messages: list[dict[str, str]], *, run_config: RunConfig | None = None):  # noqa: D401
        """Return an object that mimics Runner.run_streamed()."""

        async def _events_gen() -> AsyncGenerator[StreamEvent, None]:
            await self._ensure_client()

            # Prompt to send (last user message)
            prompt = messages[-1]["content"] if messages else ""

            # Use cached session_id (if any) so the server preserves history
            resp = await self._client.other.start_run(prompt=prompt, session_id=self._session_id)

            if self.DEBUG_CLIENT and run_config is None:  # first call heuristic
                print(f"[cli] ⇢ raw start_run resp={resp!r}")
                try:
                    print(f"[cli] ⇢ resp.result={getattr(resp, 'result', None)!r}")
                except Exception:
                    pass

            # Cache session_id returned by server (first call only)
            new_session_id = _extract_session_id(resp)
            if new_session_id:
                if self.DEBUG_CLIENT and self._session_id is None:
                    print(f"[cli] ⎆ obtained session_id={new_session_id[:8]}")
                self._session_id = new_session_id

            run_id = _extract_run_id(resp)
            if self.DEBUG_CLIENT:
                print(f"[cli] ▶ run_id={run_id}  prompt={prompt[:60]!r}")

            while True:
                msg = await self._event_q.get()
                if msg["method"] == "run_end" and (
                    run_id is None or msg["params"].get("run_id") == run_id
                ):
                    if self.DEBUG_CLIENT:
                        print(f"[cli] ◀ run_end  run_id={run_id}")
                    break
                event = _map_notification_to_event(msg["method"], msg["params"])
                if event:
                    if self.DEBUG_CLIENT:
                        print(f"[cli]   ← {msg['method']} details={msg['params']}")
                    yield event

        class _Result:
            def __init__(self, gen):
                self._gen = gen

            def stream_events(self):
                return self._gen

        return _Result(_events_gen())

    async def list_sessions(self):  # noqa: D401
        """Fetch list of active session IDs from the server."""
        await self._ensure_client()
        resp = await self._client.other.list_sessions()
        return getattr(resp, "result", resp)

    # ------------------------------------------------------------------
    # Internals ---------------------------------------------------------
    # ------------------------------------------------------------------
    async def _ensure_client(self):
        if self._client is None:
            # Build methods object that forwards events into _event_q
            self._methods = _ClientEventMethods(self._event_q)
            self._client = await WebSocketRpcClient(self._url, self._methods).__aenter__()


# ----------------------------------------------------------------------
# Mapping helpers – build synthetic StreamEvents so the UI/CLI pipeline
# can stay unchanged.
# ----------------------------------------------------------------------

def _new_item_event(event_name: str, item: Any) -> RunItemStreamEvent:  # type: ignore
    """Wrap *_item* into an Agents RunItemStreamEvent with correct *name*."""
    return RunItemStreamEvent(name=event_name, item=item)


# ------------------------------------------------------------------
# Lightweight item wrappers to satisfy AgentApp expectations
# ------------------------------------------------------------------


class _ToolCallItem:  # noqa: D401
    def __init__(self, name: str, arguments: str, call_id: str | None = None):
        self.type = "tool_call_item"
        self.name = name
        self.arguments = arguments
        self.call_id = call_id

    # AgentApp expects this helper for conversation history updates
    def to_input_item(self):  # noqa: D401
        base = {"name": self.name, "arguments": self.arguments, "role": "assistant", "type": "tool_call"}
        if self.call_id:
            base["call_id"] = self.call_id
        return base


class _ToolCallOutputItem:  # noqa: D401
    def __init__(self, name: str, output: str, call_id: str | None = None):
        self.type = "tool_call_output_item"
        self.name = name
        self.output = output
        self.call_id = call_id

    def to_input_item(self):  # noqa: D401 – rarely used
        base = {"name": self.name, "output": self.output, "role": "assistant", "type": "tool_call_output"}
        if self.call_id:
            base["call_id"] = self.call_id
        return base


class _MessageOutputItem:  # noqa: D401
    def __init__(self, content: str):
        from openai.types.responses import ResponseOutputText, ResponseOutputMessage  # local import to avoid heavy cost at top
        import uuid as _uuid

        # Build minimal valid OpenAI response objects so ItemHelpers works
        text_part = ResponseOutputText(annotations=[], text=content, type="output_text")
        message = ResponseOutputMessage(
            id=_uuid.uuid4().hex,
            content=[text_part],
            role="assistant",
            status="completed",
            type="message",
        )

        self.type = "message_output_item"
        self.raw_item = message  # Field expected by ItemHelpers

    def to_input_item(self):  # noqa: D401
        # Convert to model input format (simple assistant message)
        return {"role": "assistant", "content": ItemHelpers.extract_last_text(self.raw_item) or ""}


# ------------------------------------------------------------------


def _map_notification_to_event(method: str, payload: dict[str, Any]):  # noqa: D401
    if method == "tool_called":
        item = _ToolCallItem(payload.get("tool"), payload.get("input", "{}"), payload.get("call_id"))
        return _new_item_event("tool_called", item)
    elif method == "tool_result":
        item = _ToolCallOutputItem(payload.get("tool"), payload.get("output", ""), payload.get("call_id"))
        return _new_item_event("tool_output", item)
    elif method == "message_output":
        item = _MessageOutputItem(payload.get("content", ""))
        return _new_item_event("message_output_created", item)
    elif method == "tool_approval_request":
        # Pass raw payload; AgentApp will manage UI and response
        return _new_item_event("tool_approval_request", payload)
    elif method == "token":
        # Could map to raw_response_event, but AgentApp ignores tokens for now
        return None
    return None


# ----------------------------------------------------------------------
# Mapping helpers – build synthetic StreamEvents so the UI/CLI pipeline
# can stay unchanged.
# ----------------------------------------------------------------------

class _ClientEventMethods(RpcMethodsBase):
    """Methods exposed to the server; each just pushes the notification into queue."""

    def __init__(self, queue: "asyncio.Queue"):
        super().__init__()
        self._queue = queue

    # generic helper
    async def _push(self, method: str, **params):  # noqa: D401
        await self._queue.put({"method": method, "params": params})
        return True

    # Exposed RPC methods
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

    # ----- new: tool approval request notification from server -----
    async def tool_approval_request(self, **params):
        return await self._push("tool_approval_request", **params)

# ----------------------------------------------------------------------
# Utility to extract run_id from mixed response shapes
# ----------------------------------------------------------------------


def _extract_run_id(resp):  # noqa: D401
    # 1. Direct mapping for dict-like responses
    if isinstance(resp, dict):
        return resp.get("run_id")

    # 2. RpcResponse -> result attribute
    result = getattr(resp, "result", None)
    if result is None:
        return None

    #   a) If already a mapping
    if isinstance(result, dict):
        return result.get("run_id")

    #   b) If it's a JSON / python-dict string, attempt to parse
    if isinstance(result, str):
        parsed_obj = None
        # First try strict JSON
        try:
            parsed_obj = json.loads(result)
        except Exception:
            # Fallback: some rpc servers return str(repr(dict)), which uses single quotes
            try:
                parsed_obj = ast.literal_eval(result)
            except Exception:
                parsed_obj = None
        if isinstance(parsed_obj, dict):
            return parsed_obj.get("run_id")

    return None

def _extract_session_id(resp):  # noqa: D401
    # 1. Direct mapping for dict-like responses
    if isinstance(resp, dict):
        return resp.get("session_id")

    # 2. RpcResponse -> result attribute
    result = getattr(resp, "result", None)
    if result is None:
        return None

    #   a) If already a mapping
    if isinstance(result, dict):
        return result.get("session_id")

    #   b) If it's a JSON / python-dict string, attempt to parse
    if isinstance(result, str):
        parsed_obj = None
        try:
            parsed_obj = json.loads(result)
        except Exception:
            try:
                parsed_obj = ast.literal_eval(result)
            except Exception:
                parsed_obj = None
        if isinstance(parsed_obj, dict):
            return parsed_obj.get("session_id")

    return None 