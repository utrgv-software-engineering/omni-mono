# Remote Agent Protocol

## Overview

The Remote Agent Protocol is a JSON-RPC 2.0 based protocol for communicating with AI agents over WebSocket connections. This protocol allows agents to be deployed on remote servers while providing the same interface as local agents.

## Protocol Features

- **Stateful Sessions**: Maintain conversation history across multiple interactions
- **Streaming Events**: Real-time updates during agent execution
- **Tool Approval**: Support for human-in-the-loop tool execution with rich metadata
- **Session Management**: List, archive, and manage conversation sessions
- **Variables Management**: Initial variables sent once per session, server constructs and maintains typed context
- **Rich Tool Output**: Tools can return metadata for enhanced UI rendering alongside LLM-intended text
- **Usage Tracking**: Optional per-turn and aggregate token usage updates during and after runs
- **Model Info with Usage**: The current model metadata (e.g., model id, context window, truncation mode, max output tokens) is included with usage updates so clients always see the active model per turn

## Variables vs Context

The protocol distinguishes between **variables** (sent by clients) and **context** (managed by server):

- **Variables**: Untyped dictionary of initial parameters sent by remote clients. These are transport-level data that may include extra fields for flexibility.
- **Context**: The typed, server-side state constructed from variables using a factory function. This is what the agent actually uses during execution.

This separation allows:
1. Remote clients to send flexible, untyped data
2. Servers to maintain type-safe context objects
3. Graceful handling of extra fields from clients
4. Clear distinction between transport format and runtime state

## Metadata Support

The protocol supports optional metadata fields for enhanced UI experiences:

### Tool Approval Metadata
Tool approval requests are delivered via the `tool_approval_requested` event. The `metadata` field can contain structured information for rich approval dialogs (diffs, previews, file info, etc.) instead of displaying raw JSON arguments.

### Tool Result Metadata  
The `tool_result` event may include a `metadata` field containing UI-specific data (charts, tables, visualizations) separate from the text output sent to the agent.

## Transport

The protocol uses JSON-RPC 2.0 over WebSocket for bidirectional communication:
- **Client → Server**: RPC calls for agent operations
- **Server → Client**: Notifications for streaming events

### Authentication

All WebSocket surfaces (`/ws`, `/ws/realtime`, `/ws/terminal`) authenticate through
one configured `AuthProvider` and resolve the same principal. Two supported
credential transports:

1. **`Authorization: Bearer <token>` header** — native clients (Python
   `RemoteAgent`, the Ink TUI, server-to-server proxies) attach the token as a
   WebSocket upgrade header.
2. **One-time connect ticket** — browsers cannot set WebSocket headers, so they
   `POST /auth/ws-ticket` with the `Authorization` header over HTTP and receive
   `{"ticket": "...", "expires_in": 60}`. The ticket is single-use, short-lived,
   bound to the minting identity, and is the only credential that may appear in a
   dial URL (`?ticket=...`). Access tokens themselves must never appear in URLs.

The legacy `?token=<bearer>` query parameter is **deprecated and disabled by
default**. During migration it can be re-enabled with
`OMNIAGENTS_LEGACY_QUERY_AUTH=1` (or `security.legacy_query_auth: true`); the
server logs a deprecation warning (never containing the token) when it is used.

**Failure semantics** — authentication failures close the socket with code
`4401`; authorization failures on attach-style sockets close with `4403`. Both
codes are terminal: clients MUST NOT auto-reconnect without new credentials
(retrying with the same credentials would loop forever). Per-request
authorization failures on established connections surface as JSON-RPC error
`-32014` (`data.kind = "authorization_denied"`).

`-32014` is specified here rather than in the frozen v1.0.0 OpenRPC document:
`protocol/openrpc/baseline/1.0.0/` is immutable release evidence (see
`docs/development/gui-protocol-governance.md`), so the error metadata joins the
generated contract with the next protocol version. Clients must treat unknown
negative error codes as errors, which this relies on.

**Ownership** — sessions, runs, artifacts, terminals, approvals, and
session-scoped MCP operations are bound to the principal that created them, and
authorization **fails closed**:

* A request naming another user's resource is rejected with `-32014`.
* An ownership lookup that cannot answer (e.g. a storage fault) denies for
  everyone, including the real owner, until it succeeds — an absent owner is
  never consent.
* A request naming *no* resource must be explicitly classified as globally safe
  (handshake, introspection, self-scoped listings, per-connection state, or
  creating a new session). Anything else — including process-wide mutators like
  `disable_tool` / `disable_mcp_server`, and any newly added unclassified
  method — is refused.
* Sessions created before authentication was enabled (owner `NULL`) are **not**
  accessible and are **not** auto-claimed on access. Assign them deliberately:
  `python -m omniagents.core.session.backfill --user-id <principal>`
  (`--dry-run` to preview).
* Principals with the `admin` role bypass ownership checks.

All of the above applies only when the deployment identifies principals. With
the default `NoneAuthProvider` (or a token provider with no token configured)
every connection is the same anonymous identity, so ownership and
global-effect rules are skipped and single-user behavior is unchanged.

### Terminal WebSocket

Agents that enable interactive terminals expose an additional WebSocket endpoint at `/ws/terminal`. Clients obtain a `terminal_id` and `terminal_token` by invoking the `terminal.create` server function, then connect (authenticating like any other surface) and send an attach frame as the **first message**:

- `{"type": "attach", "session_id": "...", "terminal_id": "...", "terminal_token": "..."}`

The attach token is single-use and the terminal is bound to the identity that created it — a leaked `terminal_token` is useless without the owner's connection credentials. The legacy query-parameter attach (`?session_id=...&terminal_id=...&terminal_token=...`) is honored only while `OMNIAGENTS_LEGACY_QUERY_AUTH` is enabled.

Terminal frames are JSON objects:
- `{"type": "output", "terminal_id": "...", "data": "<base64>"}` — PTY output encoded as base64.
- `{"type": "exit", "terminal_id": "...", "code": 0}` — PTY process terminated with exit status.

Clients send frames to control the PTY:
- `{"type": "input", "data": "<base64>"}` — write bytes to stdin.
- `{"type": "resize", "cols": 120, "rows": 40}` — adjust terminal size.
- `{"type": "close"}` — request shutdown.

## Reference Implementations

OmniAgents provides two reference implementations:

### RemoteAgent (Client)
```python
from omniagents.rpc.agents import RemoteAgent

# Connect to remote agent server
agent = RemoteAgent(
    "ws://localhost:8000/ws", 
    token="optional-auth-token",
    variables={"user_id": "123", "role": "admin"}  # Optional initial variables
)

# Use like any other agent
result = agent.run_streamed([{"role": "user", "content": "Hello"}])
async for event in result.stream_events():
    print(event)
```

### LocalAgent (Local with RPC Interface)
```python
from omniagents.rpc.agents import LocalAgent
from omniagents.core.specs import AgentSpec

# Create local agent with RPC interface
spec = AgentSpec(name="MyAgent", ...)
agent = LocalAgent(spec, settings={})

# Same interface as RemoteAgent
result = agent.run_streamed([{"role": "user", "content": "Hello"}])
```

## RPC Methods

### start_run(prompt: str, session_id?: str, variables?: dict, content?: array, safe_tool_overrides?: dict)
Start a new agent run with the given prompt and optional initial variables.

**Parameters:**
- `prompt` (string): User message to send to the agent
- `session_id` (string, optional): ID of existing session to continue
- `variables` (dict, optional): Initial variables for context construction. Only sent on first run (when session_id is not provided). The server uses these variables to construct the typed context and maintains state after initialization
- `content` (array, optional): A list of input content parts for multimodal messages. When provided, the server uses this as the user's message content instead of `prompt`. Supported parts include `{"type":"input_text","text":"..."}`, `{"type":"input_image","image_url":"data:..."}`, `{"type":"input_file","file_data":"<base64>","filename":"name"}`, and `{"type":"input_audio",...}` supported by the model.
- `safe_tool_overrides` (dict, optional): Per-session override for tool approval policy. Merges into the session's allow list on each call. Contains:
  - `safe_tool_names` (array of string): Tool names to auto-approve without user confirmation
  - `safe_tool_patterns` (array of string): Regex patterns matching tool names to auto-approve

**Returns:**
```json
{
  "run_id": "unique-run-identifier",
  "session_id": "session-identifier"
}
```

**Events Streamed:**
- `tool_called`: Agent is calling a tool
- `tool_result`: Tool execution completed
- `message_output`: Agent generated text response
- `run_started`: Run has been scheduled and is now active (contains run_id and session_id)
- `run_end`: Agent run completed
- `tool_approval_requested`: A tool call needs human approval before it runs (see Tool Approval Flow below)
- `tool_approval_resolved`: A pending approval request has been resolved (broadcast to all channels for multi-tab UI dismissal)

### stop_run(run_id: string)
Cancel an active agent run.

**Parameters:**
- `run_id` (string): ID of the run to cancel

**Returns:**
```json
true
```

Note: Model/capability metadata is provided inline with usage events (see below) to account for cases where a server may switch models mid-conversation or between resumptions.

### list_sessions()
Get list of all conversation sessions with message previews.

**Returns:**
```json
[{
  "id": "session-id",
  "created_at": "2024-01-01T00:00:00Z",
  "archived": false,
  "message_count": 10,
  "first_message": {
    "role": "user",
    "content": "Help me refactor this Python script that processes CSV files",
    "timestamp": "2024-01-01T00:00:00Z"
  },
  "last_message": {
    "role": "assistant",
    "content": "I've completed the refactoring. The script now uses pandas for CSV processing, has proper error handling, follows PEP 8 conventions, and includes comprehensive logging. All tests are passing.",
    "timestamp": "2024-01-01T00:30:00Z"
  }
}]
```

**Note:** `first_message.content` and `last_message.content` are plain-text previews truncated to ~200 characters server-side. Tool calls, reasoning blocks, and other structured fields are stripped — clients that need the full message payload should call `get_session_history()`. The `role` and `timestamp` fields are preserved for display purposes.

### get_session_history(session_id: string)
Return the full message history for a given session.

**Parameters:**
- `session_id` (string): ID of the session

**Returns:**
```json
[
  { "role": "user", "content": "...", "timestamp": "2024-01-01T00:00:00Z" },
  { "role": "assistant", "content": "...", "timestamp": "2024-01-01T00:00:02Z" }
]
```

### get_user_history(limit?: number, include_archived?: boolean)
Return recent user messages across all sessions for history traversal.

Defaults: `limit=200`, `include_archived=true`.

**Parameters:**
- `limit` (number, optional): Max items to return
- `include_archived` (boolean, optional): Include archived sessions

**Returns:**
```json
[
  { "content": "Deploy to staging", "session_id": "sess_123", "timestamp": "2024-01-01T00:00:00Z" },
  { "content": "How to fix CORS?", "session_id": "sess_456", "timestamp": "2024-01-02T12:34:56Z" }
]
```

### archive_session(session_id: string)
Archive a conversation session.

**Parameters:**
- `session_id` (string): ID of session to archive

**Returns:**
```json
true
```

## Event Notifications

The server sends notifications during agent runs:

### tool_called
```json
{
  "jsonrpc": "2.0",
  "method": "tool_called",
  "params": {
    "tool": "search_web",
    "input": "{\"query\": \"latest news\"}",
    "call_id": "call-123"
  }
}
```

### tool_result
```json
{
  "jsonrpc": "2.0",
  "method": "tool_result",
  "params": {
    "tool": "search_web",
    "output": "Found 10 articles...",
    "call_id": "call-123",
    "metadata": {
      "chart_data": {...},
      "display_type": "table",
      "additional_info": {...}
    }
  }
}
```

**Note:** The `metadata` field is optional and contains UI-specific data for rich client-side rendering, separate from the text output sent to the agent.

### message_output
```json
{
  "jsonrpc": "2.0",
  "method": "message_output",
  "params": {
    "content": "Based on my search, here's what I found..."
  }
}
```

### run_end
```json
{
  "jsonrpc": "2.0",
  "method": "run_end",
  "params": {
    "run_id": "run-123",
    "end_reason": "completed",
    "model": "gpt-4o-mini",
    "max_input_tokens": 128000,
    "max_output_tokens": 16384,
    "truncation": "auto",
    "usage": {
      "requests": 3,
      "input_tokens": 1423,
      "output_tokens": 812,
      "total_tokens": 2235,
      "input_tokens_details": {"cached_tokens": 120},
      "output_tokens_details": {"reasoning_tokens": 64}
    }
  }
}
```

**end_reason values:**
- `"completed"` - Run finished successfully
- `"cancelled"` - Run was cancelled by user
- `"max_turns"` - Agent reached maximum turn limit (not an error)
- `"guardrail_violation"` - A guardrail tripwire was triggered
- `"error"` - Run failed with an error (see error field below)

**error field (optional, present when end_reason is "error" or "guardrail_violation"):**
```json
{
  "error": {
    "message": "Model called tool that doesn't exist",
    "type": "ModelBehaviorError",
    "run_data": {
      "turn_count": 3,
      "agent_name": "assistant"
    }
  }
}
```

**error object fields:**
- `message` (string): Human-readable error message
- `type` (string): Exception class name (e.g., "ModelBehaviorError", "UserError", "InputGuardrailTripwireTriggered")
- `run_data` (object, optional): Context about the run when the error occurred
  - `turn_count` (number): Number of turns completed before the error
  - `agent_name` (string): Name of the agent that was executing
- `guardrail` (object, optional): Present for guardrail violations
  - `name` (string): Name of the guardrail that triggered
  - `result` (string, optional): Details about what violated the guardrail
  - `behavior` (object, optional): Guardrail behavior configuration

### run_status (optional)
Servers may emit transient status updates during a run, for example when automatically retrying after a temporary failure (timeouts, rate limiting, service unavailable). This allows clients to present friendly, non-blocking status lines like "Error: Request timed out. (Retrying 1/3)" while the run remains active.

```json
{
  "jsonrpc": "2.0",
  "method": "run_status",
  "params": {
    "run_id": "run-123",
    "status": "retrying",
    "message": "Request timed out",
    "attempt": 1,
    "total": 3,
    "next_delay": 2.0
  }
}
```
Notes:
- The event is optional and used only for informational UI updates. Clients should continue to wait for the final run_end event.
- "attempt" is 1-based and indicates the retry that is about to be attempted, out of "total" planned retries.
- Servers SHOULD avoid re-appending the user's message to history during retries to prevent duplicate messages.
```

### usage (token utilization)
The server may stream usage updates at the end of each model turn. This allows clients to show costs and token consumption in near real time.

```json
{
  "jsonrpc": "2.0",
  "method": "token",
  "params": {
    "run_id": "run-123",
    "turn": 2,
    "response_id": "resp_abc123",
    "model": "gpt-4o-mini",
    "max_input_tokens": 128000,
    "max_output_tokens": 16384,
    "truncation": "auto",
    "delta": {
      "requests": 1,
      "input_tokens": 512,
      "output_tokens": 210,
      "total_tokens": 722,
      "input_tokens_details": {"cached_tokens": 0},
      "output_tokens_details": {"reasoning_tokens": 0}
    },
    "totals": {
      "requests": 2,
      "input_tokens": 970,
      "output_tokens": 390,
      "total_tokens": 1360,
      "input_tokens_details": {"cached_tokens": 32},
      "output_tokens_details": {"reasoning_tokens": 16}
    }
  }
}
```

Notes:
- The server emits a "token" notification after each model response completes. The "delta" object represents the usage for that single turn; "totals" is the aggregate since the start of the run.
- Model metadata is included with each token event so clients always see the model used for that turn. A server may change models mid-conversation.
- `model` is the provider-reported model id for the turn (which may carry a dated suffix or deployment name). When the serving session has a catalog identity, `token` and `run_end` also carry `model_ref` — the normalized catalog ref from the Model & Capability Catalog (see below) — so usage can be joined to catalog entries even after a mid-session `set_session_model`.
- If present, "max_input_tokens" indicates the maximum input context tokens the model can accept. "max_output_tokens" indicates the maximum tokens the model can generate in a single response. Clients can use these to calculate context utilization and warn when approaching limits.
- The final aggregate usage and last-known model metadata are also included in the run_end event.

## Tool Approval Flow

Tool approvals are first-class events backed by the openai-agents SDK 0.16 interruption model. When a tool's `needs_approval` predicate returns true, the SDK pauses the run and the server emits a `tool_approval_requested` event for each pending tool call. The client responds via the `tool_approval_response` RPC method; the server applies the decision to the SDK `RunState` and resumes the run.

### tool_approval_requested (server → client)

```json
{
  "jsonrpc": "2.0",
  "method": "tool_approval_requested",
  "params": {
    "call_id": "call_abc123",
    "tool_name": "delete_file",
    "arguments": "{\"path\": \"/important/file.txt\"}",
    "metadata": {
      "display_type": "file_delete",
      "summary": "Delete /important/file.txt",
      "preview": "This will permanently delete the file"
    },
    "session_id": "sess_123",
    "run_id": "run_456"
  }
}
```

Notes:
- `call_id` is the tool call identifier minted by the model. It uniquely identifies the pending approval and must be echoed back in `tool_approval_response`.
- `arguments` is the raw JSON-encoded argument string the model produced.
- `metadata` is optional and supplied by tools that registered an `@approval_metadata` generator. Clients should fall back to displaying `arguments` when absent.
- The server queues the event and replays it on reconnect, so a refreshed client sees any still-pending prompt.

### tool_approval_response (client → server)

```json
{
  "jsonrpc": "2.0",
  "id": "1",
  "method": "tool_approval_response",
  "params": {
    "call_id": "call_abc123",
    "decision": "approve",
    "always_approve": false,
    "rejection_message": null
  }
}
```

Fields:
- `call_id` (string, required): identifier from the matching `tool_approval_requested` event.
- `decision` (string, required): `"approve"` or `"reject"`.
- `always_approve` (bool, optional): when approving, also adds the tool name to the session-level always-allow list so subsequent invocations skip the prompt.
- `rejection_message` (string, optional): when rejecting, the message returned to the model in place of the tool result. Defaults to a generic rejection if omitted.

The server returns `true` if the approval was matched to a pending request and `false` otherwise (e.g. expired, duplicate, wrong session).

### tool_approval_resolved (server → client)

```json
{
  "jsonrpc": "2.0",
  "method": "tool_approval_resolved",
  "params": {
    "call_id": "call_abc123",
    "session_id": "sess_123"
  }
}
```

Broadcast to every connected channel for the session as soon as a `tool_approval_response` is accepted. Clients should dismiss any visible approval dialog for the matching `call_id` without taking further action — multiple tabs/voice clients can show the same prompt and the first responder wins.

## MCP Approval Flow

Hosted MCP tools (`HostedMCPTool` from openai-agents 0.16) can request per-call approval through a dedicated event/RPC pair, parallel to the function-tool approval flow above. This keeps the wire format clean: function-tool approvals carry a `call_id` minted by the model, while MCP approvals carry the `request_id` from the `McpApprovalRequest` plus the `server_label` so the UI can attribute the request to the correct hosted MCP server.

When the SDK pauses a run with a `ToolApprovalItem` whose `raw_item` is an `McpApprovalRequest`, the bridge calls `request_mcp_approval` on the server. The server emits `mcp_approval_requested`, awaits an `mcp_approval_response` RPC from the client, applies the decision via `RunState.approve` / `RunState.reject`, and resumes the run.

### mcp_approval_requested (server → client)

```json
{
  "jsonrpc": "2.0",
  "method": "mcp_approval_requested",
  "params": {
    "kind": "mcp",
    "request_id": "mcp_appr_abc123",
    "server_label": "my_mcp",
    "tool_name": "read_file",
    "arguments": "{\"path\": \"/etc/passwd\"}",
    "session_id": "sess_123",
    "run_id": "run_456"
  }
}
```

Notes:
- `request_id` is the `McpApprovalRequest.id` minted by the model — it must be echoed back in `mcp_approval_response` and is unique per approval prompt.
- `server_label` is the hosted MCP server label (matches the `server_label` field of the `HostedMCPTool` config).
- `tool_name` is the MCP tool the model wants to invoke; combined with `server_label` it identifies the operation.
- `arguments` is the raw argument string the model produced.
- `kind: "mcp"` is included as a forward-compatible discriminator for clients that want to share rendering code with `tool_approval_requested`. The presence of `request_id` (vs. `call_id`) is also a reliable discriminator.
- The server queues the event and replays it on reconnect, so a refreshed client sees any still-pending prompt.

### mcp_approval_response (client → server)

```json
{
  "jsonrpc": "2.0",
  "id": "1",
  "method": "mcp_approval_response",
  "params": {
    "request_id": "mcp_appr_abc123",
    "decision": "approve",
    "rejection_message": null
  }
}
```

Fields:
- `request_id` (string, required): identifier from the matching `mcp_approval_requested` event.
- `decision` (string, required): `"approve"` or `"reject"`.
- `rejection_message` (string, optional): when rejecting, the message returned to the model in place of the tool result.

The server returns `true` if the approval was matched to a pending request and `false` otherwise. There is no `always_approve` flag on this path: hosted-MCP approval state is keyed by `(server_label, tool_name)` inside the SDK, and OmniAgents does not currently maintain a session-level always-allow set for hosted MCP tools. Clients that want sticky approvals should re-prompt or implement a client-side cache.

### mcp_approval_resolved (server → client)

```json
{
  "jsonrpc": "2.0",
  "method": "mcp_approval_resolved",
  "params": {
    "request_id": "mcp_appr_abc123",
    "session_id": "sess_123"
  }
}
```

Broadcast to every connected channel for the session as soon as an `mcp_approval_response` is accepted, mirroring `tool_approval_resolved` for function tools. Clients should dismiss any visible MCP approval dialog for the matching `request_id`.

## Client Functions (optional)

Client Functions enable server-initiated calls to client-provided capabilities. This allows richer UIs and local integrations (e.g., rendering artifacts, file pickers, status updates) while keeping the server stateless.

Tool approvals are NOT delivered through `client_request` — they have a dedicated event/RPC pair (see "Tool Approval Flow" above) so they survive reconnection and don't interleave with generic UI requests.

### client_functions (client → server)
Optional capability handshake sent by the client after connecting. Declares supported functions.

```json
{
  "jsonrpc": "2.0",
  "method": "client_functions",
  "params": {
    "version": 1,
    "functions": [
      { "name": "ui.add_artifact", "description": "Render or update an artifact" }
    ]
  }
}
```

Servers may store this information per-connection for diagnostics or routing but should not rely on it strictly; requests are best-effort.

### client_request (server → client)
Server asks the client to run a function. Clients should execute and respond with client_response.

```json
{
  "jsonrpc": "2.0",
  "method": "client_request",
  "params": {
    "request_id": "req_abc123",
    "function": "ui.add_artifact",
    "args": { "title": "Report", "content": "# Summary", "mode": "markdown", "artifact_id": "rep_001" },
    "session_id": "sess_123",
    "run_id": "run_456",
    "timeout_ms": 5000,
    "idempotency_key": "optional-dedupe-key"
  }
}
```

Notes:
- Servers may broadcast to all active channels for the session. Clients should ignore unknown functions or return an error via client_response.
- Use idempotency_key if the server may replay requests on reconnect.

### client_response (client → server)
Client responds to a client_request. If multiple clients are connected, any may reply.

```json
{
  "jsonrpc": "2.0",
  "method": "client_response",
  "params": {
    "request_id": "req_abc123",
    "ok": true,
    "result": { "ack": true }
  }
}
```

If the client fails to execute the function, it should return:

```json
{
  "jsonrpc": "2.0",
  "method": "client_response",
  "params": {
    "request_id": "req_abc123",
    "ok": false,
    "error": { "message": "Unsupported client function: ui.open_file", "type": "UnsupportedFunction" }
  }
}
```

Server-side behavior:
- Servers may optionally await the response with a timeout. If no response arrives, the server should continue the run without blocking.
- The server may queue client_request events and replay them on reconnect like other events.

## Server Functions (optional)

Server Functions let clients invoke server-provided capabilities via RPC. This is the default direction JSON-RPC already supports, but the protocol defines a capability listing and a generic dispatch method for consistency with Client Functions.

### list_server_functions (client → server)
Returns metadata for available server functions.

```json
{
  "jsonrpc": "2.0",
  "id": "1",
  "method": "list_server_functions",
  "params": {}
}
```

Response:
```json
{
  "jsonrpc": "2.0",
  "id": "1",
  "result": [
    { "name": "session.get_history", "description": "Get conversation history" }
  ]
}
```

### server_call(function: string, args?: object, session_id?: string)
Invoke a named server function with arguments. Returns the function result or a JSON-RPC error if the function is unknown/fails.

```json
{
  "jsonrpc": "2.0",
  "id": "2",
  "method": "server_call",
  "params": {
    "function": "session.get_history",
    "args": {},
    "session_id": "sess_123"
  }
}
```

Notes:
- Functions are registered server-side and may accept optional session context.
- Most use cases should leverage these generic methods rather than inventing many ad-hoc RPC names.

## Layered Configuration (experimental)

Three experimental requests let GUI settings surfaces inspect and modify the
server's layered project configuration through one transactional API:
`get_config`, `validate_config`, and `write_config`. Clients opt in via
`capabilities.experimental_operations` at `initialize`, like every other
experimental request.

**Layer model** — the provenance layers mirror exactly what the project loader
applies, lowest to highest precedence (dicts merge recursively, lists and
scalars replace):

1. `defaults` — framework built-in defaults.
2. `project` — the base `project.yml` manifest (read-only through this API;
   packaged read-only for installed products).
3. `user` — `<product-config>/project.d/*.yml` overlays in lexical order. The
   **only writable layer**: writes go to the API-owned
   `project.d/90-gui.yml`, never to hand-written files. Desktop clients
   persist their settings through this layer.
4. `managed` — overlays named by `OMNIAGENTS_PROJECT_OVERLAYS`
   (operator/enterprise policy). A managed value makes the field read-only.
5. `environment` — environment variables that override config keys at read
   time (e.g. `OMNIAGENTS_LEGACY_QUERY_AUTH`). Read-only.

Only registered fields are exposed — never the raw merged manifest.

### get_config()

Returns `{layers, fields}`. Each layer descriptor carries its `sources`
(resolved file paths), `writable`, and the user layer's `write_target`. Each
field descriptor carries:

- `key`, `type`, `label`, `description`
- `value` + `is_set` + `effective_layer` — the effective value and where it
  came from (`value` is omitted entirely for secret fields)
- `layers` — every layer that sets the field, in precedence order, with its
  source path, so clients can explain *why* a value is what it is and what
  is being overridden
- `read_only` + `read_only_reason` (`structural` | `managed_policy` |
  `environment`) — why a write would be refused
- constraints: `allowed_values`, `minimum`, `maximum`
- `reload` — `hot` (applies immediately), `session` (applies on the next
  agent build in loaded sessions), or `restart` (read once at process boot;
  requires a server restart)
- `secret` — write-only field; **no response ever includes its value**, only
  `is_set` flags per layer

### validate_config(updates: object)

Dry-run validation of a batch. Returns `{valid, errors, reload}` without
writing anything. Errors are structured per field:
`{key, code, message, ...}` with `code` one of `unknown_key`, `read_only`
(plus `reason`), `invalid_type` (plus `expected_type`), `invalid_value` (plus
`allowed_values`), `out_of_range` (plus `minimum`/`maximum`). Error payloads
never echo submitted values, so a mistyped secret cannot leak back.

### write_config(updates: object)

Atomic batch write to the user layer (a single-value write is a batch of
one). All-or-nothing: any validation error aborts the whole batch before
anything is written, and the overlay file is replaced atomically, so a failed
write never leaves partial state. A `null` value clears the key from the user
layer, reverting to the lower layers.

Returns `{ok, errors, written, cleared, reload, restart_required, fields}` —
`reload` groups the touched keys by classification, and `fields` carries the
post-write descriptors (secrets redacted). Keys classified `session` are also
applied to the live spec (the `disable_tool` precedent), so loaded sessions
pick them up on their next run without a restart.

**Authorization** — `get_config` and `validate_config` are globally safe:
read-only, secret-redacted, and side-effect free. `write_config` mutates
server-level configuration for every connected user and is classified
global-mutating: allowed in single-user deployments, refused with `-32014`
for individual principals when the deployment identifies distinct users —
the same posture as `disable_tool`.

## Model & Capability Catalog

A first-class, provider-neutral model catalog behind every GUI model picker. The catalog view is built by `omniagents.core.models.catalog_api` over the process-wide `ModelConfigStore` (product defaults → governance → user `models.json` → live provider discovery), so all maintained clients consume the same contract. Reference client bindings live in `src/protocol/catalog.ts` (identical in the web and Ink clients).

Failure isolation is part of the contract: one provider's bad configuration or failing discovery hook never hides another provider's models — failures degrade to typed `errors` entries and an `"error"` provider health status. Catalog results never contain resolved secrets.

### list_models(include_hidden?: boolean, modality?: string, session_id?: string)

Returns every catalog model as a full descriptor, deterministically ordered (providers lexicographically, then model names within each provider). `hidden: true` models are excluded unless `include_hidden` is set (they remain resolvable via `get_model` and selectable via `set_session_model`). `modality` filters to models supporting it (`"text"`, `"audio"`). With `session_id`, the result also reports that session's active selection (and the request becomes session-scoped for authorization).

```json
{
  "models": [
    {
      "id": "codex/gpt-5.5-codex",
      "model": "gpt-5.5-codex",
      "label": "GPT-5.5 Codex",
      "description": "Agentic coding model",
      "provider": {"name": "codex", "type": "openai-oauth"},
      "modalities": ["text"],
      "realtime": false,
      "limits": {"max_input_tokens": 272000, "max_output_tokens": 128000},
      "reasoning": {"default": "medium", "options": ["low", "medium", "high", "xhigh"]},
      "tiers": {"service": null, "speed": null},
      "personality": {"supported": false, "options": [], "default": null},
      "availability": {
        "available": false,
        "reasons": [
          {
            "code": "oauth_signin_required",
            "message": "The provider account is not signed in.",
            "action": "Sign in to the provider account to use this model."
          }
        ]
      },
      "entitlement": {"entitled": false, "credential": "oauth"},
      "deprecation": {"deprecated": false, "message": null, "replace_with": null},
      "hidden": false,
      "is_default": false,
      "is_voice_default": false,
      "is_user_defined": false
    }
  ],
  "default_model": "gpt-5.1",
  "voice_default_model": null,
  "errors": [],
  "reasons": [],
  "session": {"session_id": "sess-1", "active_model": "gpt-5.1", "reasoning_effort": "medium"}
}
```

Descriptor field notes:
- `id` — the canonical catalog identity (normalized model ref: `<name>` for the default provider, `<provider>/<name>` otherwise). Mid-session model changes and usage events (`model_ref`) reference this identity.
- `model` — the provider-side id (deployment name, slug); what the provider reports in usage `model` fields.
- `reasoning.options` — the selectable efforts for `set_session_reasoning`; empty when the model does not support reasoning selection. Populated from provider discovery (`reasoning_options`) or the standard vocabulary when a default is declared.
- `tiers`, `personality`, `description`, `deprecation` — optional provider/config metadata (`service_tier`, `speed_tier`, `personality`, `description`, `deprecated`/`deprecation_message`/`replace_with` keys on provider or model entries), passed through provider-neutrally.
- `availability` — a configuration read (credentials resolvable, endpoint known), never a network probe. Reason codes: `oauth_signin_required`, `missing_credentials`, `missing_base_url` (all carry `message` and an actionable `action`).
- `entitlement.credential` — where the model's credential comes from: `platform` (management-plane `credential_ref`), `oauth`, `api_key` (configured), `env` (environment fallback), `none`.
- `errors` — per-provider failures (`{provider, code: "provider_error", message}`) surfaced without hiding other providers.
- `reasons` — non-empty only when the whole catalog cannot be served (code `no_model_store`).

### get_model(model: string)

One model's descriptor by ref (normalized before lookup), including hidden models.

```json
{"found": true, "model": { ...descriptor... }, "reasons": []}
{"found": false, "model": null, "reasons": [{"code": "unknown_model", "message": "Model 'x' is not in the catalog.", "action": "Call list_models for the available catalog."}]}
```

### list_providers()

Per-provider capability and health reads.

```json
{
  "providers": [
    {
      "name": "codex",
      "type": "openai-oauth",
      "base_url": null,
      "is_default_provider": false,
      "is_user_defined": false,
      "model_count": 3,
      "hidden_model_count": 1,
      "capabilities": {"realtime": false, "reasoning": true, "modalities": ["text"]},
      "health": {"status": "needs_auth", "detail": "The provider account is not signed in."}
    }
  ],
  "errors": [],
  "reasons": []
}
```

`health.status` is `"ok"` (at least one model available, or no models configured), `"needs_auth"` (all models blocked on credentials/sign-in), or `"error"` (the provider's models could not be enumerated; `detail` carries the failure). Health is config-derived — cheap enough to poll from a picker.

### set_session_model(session_id: string, model: string)

Switches a session to a catalog model — the typed promotion of the `/model` server function (both share one implementation, so the chat and RPC surfaces cannot drift). On success the session's subsequent runs (and their `token`/`run_end` usage events) carry the new catalog identity in `model_ref`, and connected clients receive the updated `ui.set_session_state` client-function call.

```json
{"ok": true, "session_id": "sess-1", "model": "codex/gpt-5.5-codex", "label": "GPT-5.5 Codex", "provider": "openai-oauth", "max_input_tokens": 272000, "max_output_tokens": 128000, "reasoning_effort": "medium", "warnings": []}
{"ok": false, "session_id": "sess-1", "reasons": [{"code": "unknown_model", "message": "...", "action": "..."}]}
```

- Refusal codes: `unknown_model`, `realtime_only` (realtime voice models cannot serve text sessions — use `set_voice_model`).
- Switching to a currently-unentitled model succeeds (the failure would surface at run time) but returns its availability reasons as `warnings` so pickers can flag it.
- Authorization: session-scoped — only the session's owner may switch its model.

### set_session_reasoning(session_id: string, effort: string)

Sets the session's reasoning effort (`low` | `medium` | `high` | `xhigh`) — the typed promotion of `/reasoning`. Marks the effort as an explicit override, so later model switches keep it instead of applying the model's default. Invalid efforts return `{"ok": false, "reasons": [{"code": "invalid_reasoning_effort", ...}]}`. Session-scoped.

### set_voice_model(model: string)

Sets the process-wide realtime voice default (`models.json` `voice_default`) — the typed promotion of `/voice-model`. The model must have `realtime` support (`{"code": "not_realtime"}` otherwise). Reconnect voice mode to apply. **Global-mutating**: like `disable_tool`, it changes state for every connected principal, so it is refused per-principal in multi-user deployments and available in single-user mode.

## MCP Resources

MCP servers configured under the agent's YAML `mcp_servers:` (and exposed as `Agent.mcp_servers` to the SDK) can also surface *resources* in addition to tools. Resources are read-only blobs (files, prompts, structured data) addressed by URI. OmniAgents exposes the SDK's `MCPServer.list_resources()` / `MCPServer.read_resource()` over RPC as the following server functions, dispatched through `server_call`.

Both functions look the server up by the YAML `name` field (the same identifier used in `mcp_servers:`). They require an active session because MCP servers are session-scoped.

### mcp.list_resources

Request:
```json
{
  "jsonrpc": "2.0",
  "id": "1",
  "method": "server_call",
  "params": {
    "function": "mcp.list_resources",
    "session_id": "sess_123",
    "args": {
      "server_name": "my_mcp",
      "cursor": null
    }
  }
}
```

Response:
```json
{
  "jsonrpc": "2.0",
  "id": "1",
  "result": {
    "server_name": "my_mcp",
    "resources": [
      { "uri": "file:///workspace/notes.md", "name": "notes.md", "mimeType": "text/markdown" }
    ],
    "next_cursor": null
  }
}
```

Notes:
- `server_name` (string, required): matches the `name` of an entry in the agent's `mcp_servers:` config.
- `cursor` (string, optional): opaque pagination cursor returned by a previous call.
- Resource entries are forwarded from the underlying MCP `ListResourcesResult` via `model_dump`. Shape follows the MCP spec (`uri`, `name`, `mimeType`, optional `description`, etc.); clients should treat unknown fields as opaque.
- Throws (returns a JSON-RPC error from `server_call`) if the server is unknown, inactive, or the underlying MCP server does not implement `resources/list`.

### mcp.read_resource

Request:
```json
{
  "jsonrpc": "2.0",
  "id": "2",
  "method": "server_call",
  "params": {
    "function": "mcp.read_resource",
    "session_id": "sess_123",
    "args": {
      "server_name": "my_mcp",
      "uri": "file:///workspace/notes.md"
    }
  }
}
```

Response:
```json
{
  "jsonrpc": "2.0",
  "id": "2",
  "result": {
    "server_name": "my_mcp",
    "uri": "file:///workspace/notes.md",
    "contents": [
      { "uri": "file:///workspace/notes.md", "mimeType": "text/markdown", "text": "# Notes\n..." }
    ]
  }
}
```

Notes:
- `server_name` (string, required) and `uri` (string, required): identify the resource on the named server.
- `contents` mirrors the MCP `ReadResourceResult.contents` array (`text` for textual content, `blob` for base64 binary, plus `uri` and `mimeType`).
- Throws if the server is unknown / inactive, the URI is not addressable on that server, or the underlying MCP server does not implement `resources/read`.

## Authentication

Optional token-based authentication via URL query parameter:
```
ws://server:8000/ws?token=your-auth-token
```

## Error Handling

Standard JSON-RPC 2.0 error responses:
```json
{
  "jsonrpc": "2.0",
  "id": "request-id",
  "error": {
    "code": -32603,
    "message": "Internal error",
    "data": "Additional error details"
  }
}
```

Error classes and where they appear:

- **Class A (pre-run errors)**: Errors that occur before a run can start (e.g., bad credentials, invalid spec). These are returned as JSON-RPC errors on the `start_run` call and there will be no `run_end` event for that attempt. Clients should surface the error and keep the UI interactive.

- **Class B (transient errors)**: Temporary failures (timeouts, rate limits, network issues). The server MAY internally retry and will emit `run_status` notifications while doing so. If the run ultimately fails after exhausting retries, the server emits `run_end` with `end_reason: "error"` and an `error` object.

- **Class C (agent errors)**: Permanent failures from the agents SDK (model behavior errors, SDK usage errors, guardrail violations). These are NOT retried. The server emits `run_end` with:
  - `end_reason: "error"` for `ModelBehaviorError`, `UserError`, or other `AgentsException` types
  - `end_reason: "guardrail_violation"` for guardrail tripwire exceptions
  - `end_reason: "max_turns"` when the agent reaches its turn limit (not an error condition)

  The `error` object includes the exception type, message, and additional context like `run_data` (turn count, agent name) and `guardrail` details (for guardrail violations).

Run lifecycle notifications:

- `run_started`: Emitted immediately after a run is scheduled; includes `{ run_id, session_id }`. This allows clients to correlate subsequent `run_status`, `token`, and `run_end` messages even if they arrive before the JSON-RPC response to `start_run` is processed.

## Durable Sequenced Event Replay (`replay` capability)

GUI clients can disconnect and reconnect without losing, duplicating, or
misordering run activity. The server stamps every *replayable*
server→client notification with a replay envelope and journals it
durably; clients resume from their last applied cursor.

### Replay envelope

Every replayable notification carries three additional fields:

- `session_id` — the session the event belongs to
- `stream_id` — the event-stream *epoch*. Stable across reconnects and
  (with the default durable journal) across server restarts; it changes
  when the journal is missing or destroyed, or when the server rotates
  the epoch after losing journal durability (see "Server restart
  recovery" below).
- `seq` — per-session monotonically increasing integer, never reused

Replayable methods: `run_started`, `run_status`, `message_output`,
`tool_called`, `tool_result`, `run_end`, `tool_approval_requested`,
`tool_approval_resolved`, `mcp_approval_requested`,
`mcp_approval_resolved`, `client_request`, `client_request_resolved`,
`queue_changed`, `session_forked`, `session_variables_changed`.

`token` is deliberately **not** sequenced or journaled: usage deltas are
transient, totals are cumulative, and `message_output`/`run_end`
supersede them. Clients must ignore events without an integer `seq` in
their dedup/gap logic.

### Client cursor protocol

- **Dedup**: track `(stream_id, last applied seq)` per session. An event
  with `seq <= last_seq` is a duplicate delivery (e.g. the legacy
  pending-event replay after reconnect) and MUST be dropped so no
  duplicate UI items appear.
- **Gap detection**: `seq > last_seq + 1` means events were missed
  (disconnect window, transport shedding under backpressure). Hold the
  event and backfill:

```json
{"method": "resume_session", "params": {"session_id": "…", "stream_id": "…", "after_seq": 41}}
```

Result:

```json
{
  "session_id": "…",
  "stream_id": "…",
  "first_seq": 40,
  "last_seq": 45,
  "active_run_id": "task-…",
  "events": [{"method": "tool_result", "params": {"seq": 42, "…": "…"}}, …]
}
```

Apply `events` in order (skipping any `seq` already applied), then adopt
`last_seq` as the new cursor — the replay window is allowed to be
non-contiguous because the journal compacts superseded snapshots (see
below). `resume_session` also registers the calling channel for live
events, so a bare reconnect flow is: connect → `initialize` →
`resume_session` → process live notifications.

- **Acknowledgement**: periodically acknowledge applied events so the
  server can compact its journal:

```json
{"method": "ack_events", "params": {"session_id": "…", "stream_id": "…", "seq": 45}}
```

Acks are per-connection; the journal only compacts below the *minimum*
cursor across live consumers, so multi-tab clients each receive every
event and converge on the same authoritative state.

### Resync required (error `-32030`)

When a cursor cannot be served — the stream epoch changed, the cursor is
ahead of the stream, it fell below the compaction floor, or a nonzero
`after_seq` was sent without a `stream_id` to verify it against —
`resume_session`/`ack_events` fail with a structured error:

```json
{
  "code": -32030,
  "message": "Resync required",
  "data": {
    "kind": "resync_required",
    "session_id": "…",
    "stream_id": "…",
    "retained_first_seq": 120,
    "retained_last_seq": 158
  }
}
```

The client MUST refetch authoritative state (`get_session_history`, which
also replays live pending prompts) and restart its cursor from the
advertised stream.

A `resume_session` with `after_seq > 0` MUST include the `stream_id` the
cursor was observed on: a cursor without an epoch is unverifiable (it may
belong to a previous epoch) and is answered with `-32030` instead of
potentially wrong-epoch events. Omitting `stream_id` with `after_seq: 0`
remains the fresh-resume path by which clients adopt a stream. The
reference clients always send `stream_id` once they hold a cursor, so
conforming clients are unaffected.

### Retention and compaction

- **Bounded retention**: at most `OMNIAGENTS_EVENT_LOG_MAX_EVENTS`
  (default 1024) events are retained per session. Trimming raises the
  compaction floor; cursors below the floor get `-32030`.
- **Snapshot coalescing**: `queue_changed` is a full-replace snapshot, so
  a newer one supersedes retained older ones. This never raises the
  compaction floor (nothing is lost), which is why replay windows may be
  non-contiguous.
- **Ack-driven compaction**: events acknowledged by every live consumer
  are dropped from the journal.

### Server restart recovery

The journal is durable by default (`OMNIAGENTS_EVENT_LOG_BACKEND=sqlite`,
stored next to the session-history shard; `memory` keeps it
process-local). After a restart the stream epoch and sequence numbering
continue, so clients resume normally. Runs and approval/`client_request`
prompts interrupted by the restart are closed out with synthetic events
appended when the session reloads: `run_end` with
`end_reason: "server_restart"` and the matching `*_resolved` events with
`reason: "server_restart"`. Reconnecting clients therefore converge on a
terminated-run state instead of waiting on prompts that no live future
backs. Deployments using the `memory` backend lose the journal on
restart: clients then observe a new `stream_id` (or `-32030`) and perform
a full resync.

**Durability loss.** If a durable journal append fails while the server
keeps running, live clients continue to receive a complete, correctly
ordered stream from the in-memory window — but the durable journal is
now behind the live sequence, so resuming the same epoch after a restart
would reuse sequence ids for genuinely new events (which clients would
silently drop as duplicates). The server therefore marks the epoch
durability-lost on the first failed append and **rotates the stream
epoch at the next reload**: new `stream_id`, sequence reset, retained
events discarded. Every cursor from the lost epoch then gets an explicit
`-32030` and performs a full resync. Rotation is deferred to reload
(rather than applied immediately) so a transient disk blip never forces
connected clients into a resync storm.

## Connection Lifecycle

All maintained clients (Python `JsonRpcClient`/`RemoteAgent`, the web UI,
the Ink TUI, and downstream application transports such as Omni Desktop)
follow one lifecycle policy, defined as data in:

- `omniagents/rpc/lifecycle.py` (Python)
- `omniagents/backends/web/ui/src/rpc/lifecycle.ts` and
  `omniagents/backends/ink/tui/src/rpc/lifecycle.ts` (byte-identical
  TypeScript twins)

Cross-language consistency is enforced by
`tests/unit/rpc/test_lifecycle.py`.

### Standard policy defaults

| Parameter | Default | Meaning |
| --- | --- | --- |
| connect timeout | 10 s | transport connect **plus** `initialize` handshake |
| RPC timeout | 60 s | per-call deadline (overridable per call; disable for known-long calls) |
| ping interval | 20 s | WebSocket ping cadence |
| ping timeout | 10 s | missing pong ⇒ the link is dead |
| reconnect initial delay | 0.5 s | first backoff step |
| reconnect multiplier | 2× | exponential growth per attempt |
| reconnect max delay | 30 s | backoff ceiling |
| reconnect jitter | ±10% | symmetric jitter on every delay |
| reconnect max attempts | 10 | consecutive failures before giving up |

A successful handshake resets the failure counter; the budget applies to
*consecutive* failed attempts.

### Failure classification

Failures are classified as **retryable**, **permanent**, or
**indeterminate**:

- **Permanent JSON-RPC error codes** (never retried — resending the same
  request fails identically): `-32700`, `-32600`, `-32601`, `-32602`, and
  the typed handshake errors `-32010` (InitializationRequired), `-32011`
  (AlreadyInitialized), `-32012` (ProtocolVersionMismatch), `-32013`
  (CapabilityMismatch). A structured `initialize` rejection is permanent:
  clients must not loop on the handshake.
- **Retryable JSON-RPC error codes**: `-32020` (TransportOverloaded).
  Honor `data.retry_after_ms` when present, and only auto-retry idempotent
  methods (see below).
- **Indeterminate codes** (e.g. `-32603` InternalError, application
  codes): surface to the caller; never auto-retry.
- **Permanent WebSocket close codes** (no reconnect): `1002`, `1003`,
  `1008`, and the whole `44xx` application range — notably `4403`
  (authentication/authorization rejected) and `4404` (feature not
  available). These are deterministic server rejections; retrying loops
  forever. Everything else (`1000`, `1001`, `1006`, `1011`–`1013`, or no
  code) is retryable.

### Required client behavior

1. **No unresolved promise after disconnect.** When the connection closes
   — gracefully, abnormally, or deliberately — every pending call must be
   rejected with a connection-closed error carrying the close code/reason
   and a `permanent` flag.
2. **Deadlines on every call.** Calls use the policy RPC timeout by
   default; long-running RPCs may opt out explicitly and must then be
   cancellable (`AbortSignal` in TypeScript, task cancellation in Python).
   Cancellation must remove the pending entry.
3. **Structured errors are preserved.** JSON-RPC `code`, `message`, and
   `data` travel intact to the caller (`RpcError` in Python,
   `JSONRPCError` in TypeScript).
4. **Reconnects follow the shared backoff schedule** and stop immediately
   on permanent failures or when the budget is exhausted. UIs must
   distinguish the transient "reconnecting" state from the terminal
   "closed permanently" state.
5. **Resume is out of scope for the transport.** After a reconnect,
   clients re-run the `initialize` handshake and resume sessions by
   `session_id`. Fine-grained event replay (cursor-based resume) is a
   separate, pluggable concern layered on the disconnect/reconnect hooks
   (see "Durable Sequenced Event Replay" above).

### Idempotency guidance for retries

Only auto-retry requests whose repetition cannot duplicate side effects:

- **Safe to auto-retry** (read-only or naturally idempotent):
  `initialize`, `list_sessions`, `get_session_history`,
  `get_user_history`, `list_server_functions`, `list_queue`,
  `get_agent_info`, `stop_run`, `archive_session`, `delete_session`.
- **Safe to retry by key** (server dedupes on the identifier):
  `tool_approval_response` / `mcp_approval_response` (keyed by
  `call_id`/`request_id`; the server resolves each approval once) and
  `client_response` (keyed by `request_id`).
- **Never auto-retry**: `start_run`, `enqueue_message`, `send_audio`, and
  `server_call` (server functions may have arbitrary side effects). If
  the response was lost, the request may still have executed — surface
  the failure and let the user (or a state refresh) decide.

## Implementation Guidelines

### For Servers
1. Implement all RPC methods listed above
2. Send appropriate event notifications during runs
3. Handle WebSocket connection lifecycle properly
4. Support session persistence across connections
5. Reject unauthenticated/unauthorized connections with close code `4403`
   and unavailable features with `4404` so clients classify them as
   permanent

### For Clients
1. Handle all event notification types
2. Follow the standard connection lifecycle policy (see above): shared
   timeouts, ping/pong, exponential backoff with jitter, permanent-error
   classification, and pending-call rejection on disconnect
3. Manage session state appropriately
4. Provide user-friendly interfaces for tool approvals, and surface
   retryable versus terminal connection failures distinctly
5. Deduplicate notifications by `seq` and backfill gaps via
   `resume_session` (see "Durable Sequenced Event Replay")

## Example Server Implementation

```python
from omniagents.rpc import JsonRpcEndpoint, JsonRpcClient
from omniagents.core.agent_service import AgentService

# Create agent service
agent_service = AgentService(your_agent_spec)

# Create JSON-RPC endpoint
endpoint = JsonRpcEndpoint(agent_service)

# Register with FastAPI
app = FastAPI()
endpoint.register_route(app, "/ws")
```

## Example Client Implementation

```python
from omniagents.rpc.agents import RemoteAgent

# Connect to server with optional initial variables
agent = RemoteAgent(
    "ws://server:8000/ws",
    variables={"user_id": "user123", "preferences": {}}
)

# Stream agent responses
async def chat():
    messages = [{"role": "user", "content": "Hello!"}]
    result = agent.run_streamed(messages)
    
    async for event in result.stream_events():
        if event.type == "message_output_created":
            print(f"Agent: {event.item.raw_item.content[0].text}")
        elif event.type == "tool_called":
            print(f"Calling tool: {event.item.name}")

# Cancel a running agent
async def cancel_example():
    # Start a long-running task
    messages = [{"role": "user", "content": "Perform a complex analysis"}]
    task = asyncio.create_task(chat())
    
    # Cancel after some time
    await asyncio.sleep(5)
    success = await agent.stop_run()
    print(f"Cancellation {'succeeded' if success else 'failed'}")
```

This protocol enables seamless integration of remote AI agents while maintaining the same interface as local agents.
