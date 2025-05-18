# Remote Agent Protocol

This document describes the JSON-RPC interface implemented by the OmniAgents server.

Remote clients use a WebSocket connection (provided by [`fastapi_websocket_rpc`](https://github.com/authorizon/fastapi-websocket-rpc)) to send RPC methods and receive real-time notifications.

The reference implementation lives in [`omniagents/server/app.py`](../omniagents/server/app.py) and [`omniagents/remote_proxy.py`](../omniagents/remote_proxy.py).

## RPC Methods

### `start_run`
Start a new assistant run. The server preserves history across runs using a `session_id` returned on the first call.

**Request**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "start_run",
  "params": {"prompt": "Hello", "session_id": null}
}
```

**Response**
```json
{
  "run_id": "run_xxx",
  "session_id": "session_xxx"
}
```

### `stop_run`
Cancel an active run.

**Request**
```json
{"jsonrpc": "2.0", "id": 2, "method": "stop_run", "params": {"run_id": "run_xxx"}}
```

**Response**
`true` if the run was cancelled.

### `send_user_message`
Append a message to the current run's conversation. The next assistant output will include it.

**Request**
```json
{"jsonrpc": "2.0", "id": 3, "method": "send_user_message", "params": {"run_id": "run_xxx", "content": "next prompt"}}
```

**Response**
No payload (the server returns `null`).

### `get_session_history`
Fetch the full conversation history for a session as JSON text.

**Request**
```json
{"jsonrpc": "2.0", "id": 4, "method": "get_session_history", "params": {"session_id": "session_xxx"}}
```

**Response**
A JSON string representing the history list.

### `list_sessions`
Return a list of all sessions with their archive state.

**Request**
```json
{"jsonrpc": "2.0", "id": 5, "method": "list_sessions", "params": {}}
```

**Response**
```json
[
  {"id": "session_1", "archived": false},
  {"id": "session_2", "archived": true}
]
```

### `archive_session`
Mark a session as archived so clients can hide it.

The archived status is stored in the server history database so it
persists across restarts.

**Request**
```json
{"jsonrpc": "2.0", "id": 6, "method": "archive_session", "params": {"session_id": "session_xxx"}}
```

**Response**
`true` once the session was archived.

### `tool_approval_response`
Answer a pending tool approval request. The `approval_id` matches the identifier sent in the notification.

**Request**
```json
{
  "method": "tool_approval_response",
  "params": {
    "approval_id": "abc",
    "approved": true,
    "always_approve": false,
    "error_message": null
  }
}
```

**Response**
`true` once the decision has been processed.

## Notification Events

The server pushes asynchronous events over the same WebSocket. Payload schemas are shown below.

### `tool_called`
```json
{
  "run_id": "run_xxx",
  "tool": "weather",
  "input": "{\"location\": \"Paris\"}",
  "call_id": "id1"
}
```

### `tool_result`
```json
{
  "run_id": "run_xxx",
  "tool": "weather",
  "output": "15C",
  "call_id": "id1"
}
```

### `message_output`
```json
{
  "run_id": "run_xxx",
  "content": "Assistant reply ...",
  "is_final": false
}
```

### `tool_approval_request`
```json
{
  "approval_id": "abc",
  "tool": "search",
  "arguments": "{\"query\": \"...\"}"
}
```

### `run_end`
```json
{
  "run_id": "run_xxx",
  "end_reason": "completed"
}
```

The server may also emit a `token` event containing partial tokens during streaming. Clients can ignore it if not required.
