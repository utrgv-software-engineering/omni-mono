# RPC Protocol

The Remote Agent Protocol enables JSON-RPC 2.0 communication with AI agents over WebSocket connections.

## Overview

The protocol allows agents to be deployed on remote servers while providing the same interface as local agents. It supports:

- Stateful sessions with conversation history
- Real-time streaming events
- Tool approval (Safe Agent mode)
- Session management
- Rich metadata for UI rendering
- Token usage tracking

## Quick Start

### Client Connection

```python
from omniagents.rpc.agents import RemoteAgent

# Connect to remote agent
agent = RemoteAgent(
    "ws://localhost:8000/ws",
    token="optional-auth-token",
    variables={"user_id": "123"}
)

# Stream agent responses
result = agent.run_streamed([{"role": "user", "content": "Hello"}])
async for event in result.stream_events():
    print(event)
```

### Server Setup

```bash
# Start server
omniagents run -P agent.yml --mode server --host 0.0.0.0 --port 8000
```

## Core RPC Methods

### start_run

Start an agent run:

```python
{
  "method": "start_run",
  "params": {
    "prompt": "What time is it?",
    "session_id": "optional-session-id",
    "variables": {"user_id": "123"}
  }
}
```

**Returns:**
```json
{
  "run_id": "unique-run-id",
  "session_id": "session-id"
}
```

### list_sessions

Get all conversation sessions:

```python
{
  "method": "list_sessions",
  "params": {}
}
```

Returns session list with message previews.

### get_session_history

Get full message history for a session:

```python
{
  "method": "get_session_history",
  "params": {
    "session_id": "session-id"
  }
}
```

## Event Notifications

The server streams events during agent runs:

### message_output

Agent text response:

```json
{
  "method": "message_output",
  "params": {
    "content": "Based on my search..."
  }
}
```

### tool_called

Tool execution started:

```json
{
  "method": "tool_called",
  "params": {
    "tool": "search_web",
    "input": "{\"query\": \"...\"}"  ,
    "call_id": "call-123"
  }
}
```

### tool_result

Tool execution completed:

```json
{
  "method": "tool_result",
  "params": {
    "tool": "search_web",
    "output": "Results...",
    "call_id": "call-123",
    "metadata": {
      "chart_data": {...}
    }
  }
}
```

The `metadata` field contains UI-specific data for rich rendering.

### run_end

Run completed:

```json
{
  "method": "run_end",
  "params": {
    "run_id": "run-123",
    "end_reason": "completed",
    "usage": {
      "input_tokens": 1423,
      "output_tokens": 812,
      "total_tokens": 2235
    }
  }
}
```

**end_reason values:**
- `completed` - Success
- `cancelled` - User cancelled
- `max_turns` - Turn limit reached
- `guardrail_violation` - Guardrail triggered
- `error` - Error occurred

### token

Token usage update (streamed during run):

```json
{
  "method": "token",
  "params": {
    "run_id": "run-123",
    "turn": 2,
    "model": "gpt-4o-mini",
    "delta": {
      "input_tokens": 512,
      "output_tokens": 210
    },
    "totals": {
      "input_tokens": 970,
      "output_tokens": 390
    }
  }
}
```

## Client Functions

The protocol supports server-initiated calls to client capabilities.

> Tool approvals now flow exclusively through `client_request` + `client_response`. The legacy `tool_approval_response` RPC is removed.

### ui.request_tool_approval

Server requests tool approval (Safe Agent mode):

```json
{
  "method": "client_request",
  "params": {
    "request_id": "req-123",
    "function": "ui.request_tool_approval",
    "args": {
      "tool": "delete_file",
      "arguments": "{\"path\": \"/file.txt\"}",
      "metadata": {
        "display_type": "file_delete",
        "summary": "Delete /file.txt"
      }
    }
  }
}
```

Client responds:

```json
{
  "method": "client_response",
  "params": {
    "request_id": "req-123",
    "ok": true,
    "result": {
      "approved": true,
      "always_approve": false
    }
  }
}
```

### ui.add_artifact

Server sends rich content/artifacts:

```json
{
  "method": "client_request",
  "params": {
    "request_id": "req-124",
    "function": "ui.add_artifact",
    "args": {
      "title": "Report",
      "content": "# Summary\n...",
      "mode": "markdown",
      "artifact_id": "rep_001"
    }
  }
}
```

## Variables vs Context

The protocol distinguishes between:

- **Variables**: Untyped dictionary sent by clients (transport layer)
- **Context**: Typed server-side state constructed from variables

This allows:
- Flexible, untyped client data
- Type-safe server-side context
- Graceful handling of extra fields

## Authentication

Optional token-based authentication. Tokens travel in the WebSocket upgrade
headers, never in the URL:

```
Authorization: Bearer your-auth-token
```

Browser clients that cannot set upgrade headers exchange the token for a
short-lived, single-use connect ticket first:

```
POST /auth/ws-ticket           (with Authorization: Bearer your-auth-token)
→ {"ticket": "...", "expires_in": 60}

ws://server:8000/ws?ticket=...
```

The same mechanism guards `/ws`, `/ws/realtime`, and `/ws/terminal`, and all
three resolve the same authenticated principal. The legacy
`ws://server:8000/ws?token=...` query form is deprecated and only accepted when
the server sets `OMNIAGENTS_LEGACY_QUERY_AUTH=1` (or
`security.legacy_query_auth: true`) during migration. Proxy identity assertions
remain header-only and are never accepted through this legacy query transport.

Authentication failures close the socket with code `4401` (or `4403` for
authorization failures); both are terminal — do not auto-reconnect without new
credentials. Cross-user access to sessions, runs, terminals, or approvals on an
established connection returns JSON-RPC error `-32014`
(`data.kind = "authorization_denied"`).

Authorization fails closed: an unresolvable owner denies, requests that name no
resource must be explicitly classified as globally safe (process-wide mutators
such as `disable_tool` are refused in multi-user mode), and pre-auth sessions
with no owner are inaccessible until assigned with
`python -m omniagents.core.session.backfill --user-id <principal>`. Deployments
with no authentication provider are unaffected. See
`omniagents/rpc/protocol.md` for the full rules.

## Error Handling

Standard JSON-RPC 2.0 errors:

```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32603,
    "message": "Internal error",
    "data": "Additional details"
  }
}
```

## Connection Lifecycle

All maintained clients share one lifecycle policy — connect/RPC timeouts,
ping/pong cadence, exponential reconnect backoff with jitter and a retry
budget, permanent-versus-retryable failure classification, and guaranteed
rejection of pending calls on disconnect. The policy is defined as data in
`omniagents/rpc/lifecycle.py` (Python) and `src/rpc/lifecycle.ts` (web UI
and Ink TUI, byte-identical). See the "Connection Lifecycle" section of
`omniagents/rpc/protocol.md` for the normative rules and the idempotency
guidance describing which methods are safe to auto-retry.

## Full Protocol Specification

For complete protocol details, see the full specification at:
`omniagents/rpc/protocol.md`

The generated GUI OpenRPC contract and its maintainer policies are described in
[GUI Protocol Governance](../development/gui-protocol-governance.md). Protocol
changes must follow its compatibility, regeneration, Desktop sync, and review
requirements.

## Examples

Complete examples available in `examples/remote_agent/`.

## Next Steps

- [Server Backend](../backends/server.md) - Set up a server
- [Core API](core.md) - Agent creation
- [Examples](../examples.md) - Remote agent examples
- [GUI Protocol Governance](../development/gui-protocol-governance.md) - Maintain the versioned GUI contract
