"""The chat UI's RPC surface, bound to a server-registered generalist session.

No browser-selected agent, workspace, unregistered session, approval policy, or factory token.
Normal chat messages use the runtime protocol; stop and decision IDs are checked
against the same authoritative snapshot as the HTTP facade.
"""

from copy import deepcopy

from fastapi import HTTPException
from .factory_models import FactoryRpcError

_FIELDS = {
    "ping": set(),
    "capabilities": set(),
    "get_agent_info": set(),
    "resume_session": {"session_id", "stream_id", "after_seq"},
    "ack_events": {"session_id", "stream_id", "seq"},
    "queue_status": {"session_id", "include_snapshot", "submission_id"},
    "list_queue": {"session_id"},
    "cancel_queued_message": {"session_id", "item_id"},
    "get_session_history": {"session_id"},
    "get_thread": {"thread_id"},
    "list_turns": {"thread_id", "limit", "cursor", "order"},
    "list_items": {"thread_id", "turn_id", "kinds", "limit", "cursor", "order"},
    "get_item": {"thread_id", "item_id"},
    "get_plan": {"thread_id", "scope"},
    "get_run_diff": {"thread_id", "turn_id"},
    "list_models": {"session_id", "include_hidden", "modality"},
    "get_model": {"model"},
    "set_session_model": {"session_id", "model"},
    "set_session_reasoning": {"session_id", "effort"},
}
_EXPERIMENTAL = {
    "queue_status",
    "get_plan",
    "get_run_diff",
    "item_updated",
    "tool_approval_reviewed",
    "plan_completion_reviewed",
}
_READ_FUNCTIONS = {
    "workers.list",
    "bash_jobs.list",
    "goal.status",
    "wakeup.status",
    "loop.status",
}


class PersonalRpcPolicy:
    def __init__(self, connection, username, name=None):
        self.connection = connection
        self.username = username
        self.name = name or username

    def sender_line(self):
        from .personal import sender_line

        return sender_line(self.name, self.username)

    async def __call__(self, message):
        """Return (upstream request, local reply); either may be None."""
        request_id = message.get("id") if isinstance(message, dict) else None
        try:
            if not isinstance(message, dict) or not isinstance(
                message.get("method"), str
            ):
                raise ValueError("Expected an RPC request")
            method = message["method"]
            params = deepcopy(message.get("params") or {})
            if not isinstance(params, dict):
                raise ValueError("RPC parameters must be an object")
            if request_id is None and method != "initialized":
                return None, None
            result, forward = await self.prepare(method, params)
            if forward:
                return {**message, "params": params}, None
            return None, {"jsonrpc": "2.0", "id": request_id, "result": result}
        except (ValueError, HTTPException, FactoryRpcError) as exc:
            if request_id is None:
                return None, None
            return None, {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": str(getattr(exc, "detail", exc))},
            }

    def fields(self, params, allowed):
        if params.keys() - allowed:
            raise ValueError(
                "This operation includes parameters not available in Personal"
            )
        for key in {"session_id", "thread_id"} & allowed:
            if params.get(key) != self.connection.session_id:
                raise ValueError("Only the selected chat is available")

    async def prepare(self, method, params):
        if method == "initialize":
            capabilities = params.setdefault("capabilities", {})
            if not isinstance(capabilities, dict) or not isinstance(
                capabilities.get("experimental_operations", []), list
            ):
                raise ValueError("Invalid capabilities")
            for name in ("realtime", "terminal", "client_functions", "mcp_apps"):
                capabilities[name] = False
            capabilities["experimental_operations"] = [
                name
                for name in capabilities.get("experimental_operations", [])
                if isinstance(name, str) and name in _EXPERIMENTAL
            ]
            return None, True
        if method == "initialized":
            self.fields(params, set())
            return None, True
        if method == "client_functions":
            return None, False  # This host does not execute client functions.
        if method == "list_server_functions":
            self.fields(params, set())
            functions = await self.connection.call(method, {})
            return [f for f in functions if f.get("name") in _READ_FUNCTIONS], False
        if method == "slash.list":
            return {"commands": []}, False
        if method == "server_call":
            if params.get("function") == "slash.list":
                return {"commands": []}, False
            self.fields(params, {"function", "args", "session_id"})
            function = params.get("function")
            args = params.get("args") or {}
            if not isinstance(args, dict):
                raise ValueError("Invalid server function arguments")
            if function == "slash.list":
                return {"commands": []}, False
            if function == "session.ensure":
                self.fields(args, {"session_id"})
            elif function not in _READ_FUNCTIONS or args:
                raise ValueError("This server function is not available in Personal")
            return None, True
        if method in {"start_run", "enqueue_message"}:
            self.fields(
                params,
                {
                    "session_id",
                    "prompt",
                    "content",
                    "input_content",
                    "submission_id",
                    "environment_selection",
                    "variables",
                    "source",
                    "role",
                },
            )
            if params.get("variables") or params.get("role", "user") != "user":
                raise ValueError(
                    "Personal messages cannot change runtime policy or role"
                )
            if params.get("environment_selection", {"mode": "inherit"}) != {
                "mode": "inherit"
            }:
                raise ValueError("Personal uses the factory's workspace")
            text_key = "prompt" if method == "start_run" else "content"
            text = params.get(text_key)
            if not isinstance(text, str):
                raise ValueError("Message text must be a string")
            content_key = "content" if method == "start_run" else "input_content"
            content = params.get(content_key)
            if content is not None and not isinstance(content, (str, list)):
                raise ValueError("Invalid message content")
            submission = params.get("submission_id")
            if not isinstance(submission, str) or not submission:
                raise ValueError("A stable submission ID is required")
            result = await self.connection.send(
                text,
                submission,
                author=self.username,
                author_name=self.name,
                input_content=content,
            )
            if method == "start_run":
                return {**result, "run_id": result.get("run_id", "")}, False
            return result, False
        if method == "stop_run":
            self.fields(params, {"run_id"})
            return await self.connection.stop(params.get("run_id")), False
        if method in {
            "tool_approval_response",
            "mcp_approval_response",
            "elicitation_response",
        }:
            key = {
                "tool_approval_response": "call_id",
                "mcp_approval_response": "request_id",
                "elicitation_response": "elicitation_id",
            }[method]
            self.fields(
                params,
                {
                    key,
                    "decision",
                    "action",
                    "value",
                    "always_approve",
                    "rejection_message",
                },
            )
            if params.get("always_approve"):
                raise ValueError(
                    "Personal approvals apply once; factory policy cannot be changed here"
                )
            state = await self.connection.snapshot()
            item = next(
                (
                    item
                    for item in state["attention"]
                    if item.get("content", {}).get(key) == params.get(key)
                    and params.get(key)
                ),
                None,
            )
            if item is None:
                raise ValueError("This request is not pending in the selected chat")
            return (
                await self.connection.decide(
                    item["item_id"],
                    params.get("action", params.get("decision")),
                    params.get("value"),
                ),
                False,
            )
        if (
            method == "list_items"
            and params.get("thread_id") != self.connection.session_id
        ):
            # Workers belong to this selected conversation. Permit only a
            # read of their retained transcript, never session setup or writes.
            self.fields(
                {k: v for k, v in params.items() if k != "thread_id"},
                _FIELDS[method] - {"thread_id"},
            )
            workers = await self.connection.call(
                "server_call",
                {
                    "function": "workers.list",
                    "args": {},
                    "session_id": self.connection.session_id,
                },
            )
            if not any(
                w.get("session_id") == params.get("thread_id")
                and w.get("parent_session_id") == self.connection.session_id
                for w in workers.get("snapshot", [])
            ):
                raise ValueError(
                    "Only workers belonging to the selected chat are available"
                )
            return await self.connection.call(method, params), False
        if method in _FIELDS:
            self.fields(params, _FIELDS[method])
            return None, True
        raise ValueError(f"{method} is not available in Personal")


class PersonalVoicePolicy(PersonalRpcPolicy):
    """Only signalling and delegation for this browser's selected-chat call."""

    async def prepare(self, method, params):
        if method == "capabilities":
            self.fields(params, set())
        elif method == "start_session":
            self.fields(params, {"session_id", "sdp"})
        elif method == "stop_session":
            self.fields(params, {"session_id"})
        elif method == "delegate":
            self.fields(params, {"session_id", "delegation_id", "text"})
            if not isinstance(params.get("text"), str):
                raise ValueError("Invalid voice request")
            params["text"] = f"{self.sender_line()}\n\n{params['text']}"
        elif method == "sync_context":
            self.fields(params, {"session_id", "turns"})
            turns = params.get("turns", [])
            if not isinstance(turns, list) or len(turns) > 80:
                raise ValueError("Invalid transcript batch")
            for turn in turns:
                if not isinstance(turn, dict) or not isinstance(turn.get("text"), str):
                    raise ValueError("Invalid transcript turn")
                if turn.get("role") == "user":
                    turn["text"] = f"{self.sender_line()}\n\n{turn['text']}"
        else:
            raise ValueError("This voice operation is not available in Personal")
        return None, True
