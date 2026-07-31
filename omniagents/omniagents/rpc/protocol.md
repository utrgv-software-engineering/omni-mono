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
    "run_id": "run-123",
    "session_id": "sess-123",
    "content": "Based on my search, here's what I found...",
    "is_final": false,
    "message_id": "msg_0a1b2c"
  }
}
```

`message_id` is the model's own id for the message, present when the
provider mints one. It is optional, and it is what clients key the
canonical `agent_message` item on — see § Conversation Domain Objects,
"Ordering and canonical ids". `is_final` is optional and reflects whether
the runtime considers this the concluding message of the run.

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

The server returns `true` if the approval was matched to a pending request and `false` otherwise. There is no `always_approve` flag on this path; sticky grants for hosted MCP tools are installed via `safe_tool_overrides.safe_mcp_tools` (entries of `{ server_label, tool_name }`) on `start_run` / `enqueue_message`.

#### Approval identity: hosted vs. connected servers

Both MCP approval lanes resolve trust from the **same identity namespace and the same session-level allow-set**, deliberately:

- **Hosted** (`HostedMCPTool`, executed by the model provider): prompts arrive on this `mcp_approval_requested`/`mcp_approval_response` lane keyed by `request_id`; identity is `(server_label, tool_name)` from the hosted tool config.
- **Connected** (servers from `mcp_servers:` config, executed by this process): MCP-derived function tools go through the *function-tool* lane (`tool_approval_requested` with `call_id`); their payload carries `server_label`/`tool_label` from the stamped `(server name, original tool name)` identity, and an `always_approve` response records that tuple.

Grants from either lane land in one tuple-keyed session allow-set (`(server identity, original tool name)`, never parsed out of flat public names), so "always allow" for `("github", "create_issue")` covers the operation regardless of which execution mode carries it. The `server_label` of a hosted server and the `name` of a connected server share one namespace — give them the same value only when they are genuinely the same server.

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

## User Elicitation Flow

Structured user elicitation is the general, non-security input lane: agents, tools, and server-side integrations request a decision or piece of information from the user through a consistent GUI contract. It is deliberately parallel to — never a substitute for — the approval flows above: approvals gate execution and feed the audit trail; elicitations gather ordinary input. Nothing answered through an elicitation can approve a tool call, and approvals are never rerouted through this lane. The lanes share plumbing where honest: correlation ids, the durable replay journal, first-response-wins across tabs, and run-scoped clearing.

Server-side callers use `AgentService.request_elicitation(session=..., kind=..., message=..., ...)`; agents get the `ask_user` builtin tool (question / confirm / select). Consumers that need structured forms or URL actions (e.g. MCP-server management) call `request_elicitation` with `kind="form"` / `kind="url"`.

### elicitation_requested (server → client, correlated notification)

```json
{
  "jsonrpc": "2.0",
  "method": "elicitation_requested",
  "params": {
    "elicitation_id": "elc_abc123",
    "kind": "form",
    "title": "Database connection",
    "message": "Enter the connection details for the staging database.",
    "input_schema": {
      "type": "object",
      "required": ["host"],
      "properties": {
        "host": { "type": "string" },
        "port": { "type": "integer" }
      }
    },
    "session_id": "sess_123",
    "run_id": "run_456",
    "item_id": "call_789",
    "source": "tool",
    "timeout_ms": 300000,
    "expires_at": "2026-07-30T21:00:00Z",
    "persist_response": false,
    "seq": 17,
    "stream_id": "d41d8cd9"
  }
}
```

Fields:
- `elicitation_id` (string, required): correlation id, echoed in `elicitation_response` and `elicitation_resolved`.
- `kind` (string, required): one of `question` (free text), `confirm` (yes/no), `select` (choose among `options`), `form` (structured fields described by `input_schema`), `url` (complete an action at an external URL).
- `message` (string, required): the human-readable prompt. `title` (string, optional) is a short heading.
- `input_schema` (object, optional): JSON Schema (draft 2020-12) the accepted value must satisfy. Primarily used by `form`; clients validate before submitting and the server re-validates.
- `options` (array, optional): the choices for `select`. Entries are open records, conventionally `{ "value": ..., "label": "...", "description": "..." }`; bare strings are also accepted. Values submitted back must come from the offered `value`s.
- `url` (string, optional): the action URL for `kind: "url"`.
- `session_id` / `run_id` / `item_id` (optional): correlation to the session, the run that asked, and the originating conversation item (e.g. the tool `call_id`).
- `source` (string, optional): who is asking — `"agent"`, `"tool"`, or an integration-defined label.
- `timeout_ms` / `expires_at` (optional): expiry. When the deadline passes unanswered the server resolves the prompt itself with `status: "expired"`.
- `persist_response` (bool, optional, default false): whether the accepted value may enter durable, replayable history (see `elicitation_resolved`).
- `seq` / `stream_id`: the standard replay envelope. Elicitation prompts and resolutions are journaled like every replayable event: a client that reconnects mid-prompt receives the pending `elicitation_requested` via `resume_session` (or the live-state replay in `get_session_history`), and a prompt whose backing process died is closed out with a synthetic `elicitation_resolved` (`status: "server_restart"`).

Value conventions for `accept` responses, by kind:

| kind | accepted `value` |
| --- | --- |
| `question` | `{ "text": "..." }` (non-empty) |
| `confirm` | `{ "confirmed": true \| false }` |
| `select` | `{ "selected": [<option value>, ...] }` |
| `form` | object satisfying `input_schema` |
| `url` | optional acknowledgement object (e.g. `{ "completed": true }`) |

Clients that cannot render a kind or schema MUST decline (`action: "decline"`, ideally with a `reason` like `"unsupported_by_client"`) rather than crash or ignore the prompt.

### elicitation_response (client → server)

```json
{
  "jsonrpc": "2.0",
  "id": "1",
  "method": "elicitation_response",
  "params": {
    "elicitation_id": "elc_abc123",
    "action": "accept",
    "value": { "host": "db.staging.internal", "port": 5432 }
  }
}
```

Fields:
- `elicitation_id` (string, required): from the matching `elicitation_requested`.
- `action` (string, required): `"accept"` (with `value`), `"decline"` (user or client refused), or `"cancel"` (dismissed without answering).
- `value` (object, optional): the submitted content, per the kind conventions above.
- `reason` (string, optional): free-text context for declines/cancels (`"unsupported_by_client"`, `"not now"`, ...).

Result: `{ "elicitation_id": "...", "status": "accepted" | "declined" | "cancelled" }`.

The first response from any connected tab wins. Errors are typed:
- `-32050` `elicitation_not_found` — unknown id (never issued, or evicted long after resolution).
- `-32051` `elicitation_already_resolved` — duplicate response; `data.status` carries the winning status. Late answers after expiry or cancellation land here too.
- `-32052` `elicitation_invalid_response` — the response violates the request contract (`data.errors` lists the problems, including `input_schema` validation failures). The prompt stays pending and answerable.
- `-32014` `authorization_denied` — in multi-user deployments, answering another user's elicitation (or probing unknown ids) is denied by session ownership, exactly like `tool_approval_response`.

### elicitation_resolved (server → client)

```json
{
  "jsonrpc": "2.0",
  "method": "elicitation_resolved",
  "params": {
    "elicitation_id": "elc_abc123",
    "session_id": "sess_123",
    "run_id": "run_456",
    "status": "accepted",
    "action": "accept",
    "value": { "host": "db.staging.internal", "port": 5432 },
    "seq": 18,
    "stream_id": "d41d8cd9"
  }
}
```

Broadcast (and journaled) for every terminal transition so all tabs converge:
- `status` (required): `accepted`, `declined`, `cancelled` (run ended / dismissed), `expired` (deadline passed), or `server_restart` (synthetic closure after process death).
- `action` / `reason` (optional): the responder's action and context, when there was one.
- `value` (optional): present **only when the request set `persist_response: true`**. Sensitive answers (flagged `false`, the default) are returned to the in-process requester but never appear in the journal, in replayed history, or in this broadcast.

Pending elicitations always clear on run cancellation/termination: the run-end path terminal-resolves every open prompt for the session with `status: "cancelled"` before clearing live state, so replaying clients see a resolution rather than a dangling prompt.

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

## Provider Accounts & Usage (`account_*` methods)

Stable, provider-agnostic RPCs for managing model-provider
authentication and account state without CLI-only flows. Providers are
identified by stable ids (`openai-chatgpt` for the ChatGPT/Codex OAuth
account; `openai`, `anthropic`, `google`, `azure-openai` for API-key
credentials). Capability differences are normalized: every provider
snapshot carries a `capabilities` object (`login_modes`, `logout`,
`refresh`, `usage`), and calling an operation a provider does not
support fails with a **typed** error, never a generic `-32603`.

### Typed errors

| Code | Name | `data.kind` | Meaning |
|------|------|-------------|---------|
| `-32040` | AccountCapabilityUnsupported | `account_capability_unsupported` | The provider never supports this operation (`data.operation`, `data.supported`). |
| `-32041` | AccountStateInvalid | `account_state_invalid` | Supported operation, wrong state (`data.reason`: `not_signed_in`, `unknown_login`, `unknown_provider`, `code_required`, `state_mismatch`, `api_key_required`, `externally_managed`). |
| `-32042` | AccountAuthFailed | `account_auth_failed` | The upstream identity provider rejected the credential (`data.status` when known). |

### Methods

- `account_status()` → `{ providers: [AccountSnapshot], selected_provider }` —
  redacted state of every provider: `state`
  (`signed_in`/`signed_out`/`error`), `source` (`oauth`/`rpc`/`env`),
  `identity` (`account_id`, `plan`, `email` when derivable), typed
  `capabilities`, and the last stored auth error. **Never contains
  credential material.**
- `account_usage(provider?)` → `{ providers: [...] }` — plan, rate-limit
  windows (`used_percent`, `window_minutes`, `resets_at` epoch seconds),
  capture timestamp, and workspace `notices` when available. For the
  ChatGPT provider this reflects the latest `x-codex-*` rate-limit
  headers captured from live model traffic, so it updates without
  restarting the server. Requesting usage from a provider without the
  capability → `-32040`; signed-out provider → `-32041`.
- `account_login_start(provider, mode, api_key?, redirect_uri?)` — begins a
  login. Modes:
  - `device_code` (ChatGPT): returns `{ state: "pending", login_id,
    user_code, verification_url, interval }`.
  - `browser` (ChatGPT): returns `{ state: "pending", login_id, auth_url,
    redirect_uri }` — the URL-handoff point. The PKCE verifier never
    leaves the server; the client opens `auth_url` and later posts the
    authorization code back.
  - `api_key` (API-key providers): completes immediately —
    `{ state: "completed", account }`. The key is installed into the
    server process environment for the model runtime; it is not echoed
    back and is redacted from server debug logs.
- `account_login_complete(login_id, code?)` — advances a pending login.
  Device-code: performs one authorization poll (`state: "pending"` until
  the user approves; clients poll on `interval`). Browser: exchanges the
  authorization `code` (raw code or full redirect URL). Completing an
  unknown/expired/cancelled login → `-32041 unknown_login`.
- `account_login_cancel(login_id)` → `true` when a pending login was
  dropped. Cancellation is immediate; a poll already in flight cannot
  complete afterwards.
- `account_logout(provider)` — clears the stored OAuth tokens or an
  RPC-installed API key. Keys injected by the host environment are
  `externally_managed` (`-32041`) — sign out where they were configured.
- `account_refresh(provider)` — forces an OAuth token refresh now.
  Upstream rejection → `-32042` with the HTTP status; the failure is also
  stamped into `account_status` (`state: "error"`).
- `account_select(provider)` — records the preferred account provider
  (surfaced as `selected` in snapshots and `selected_provider` in
  status) so clients agree on the active account.

### `account_changed` notification (server → client)

```json
{ "provider": "openai-chatgpt", "reason": "login", "account": { …snapshot } }
```

Broadcast to **every connected client** after login completion, logout,
refresh, and selection (`reason` ∈ `login`/`logout`/`refresh`/`select`).
Optional (may be disabled at `initialize`) and **not journaled**: account
state is server-global, so reconnecting clients refetch via
`account_status` instead of replaying stale credential states.

### Authorization

`account_status` and `account_usage` are read-only and globally safe.
Every mutation (`account_login_*`, `account_logout`, `account_refresh`,
`account_select`) rewrites process-wide provider credentials, so in
enforced multi-user deployments they are refused for individual
principals (the `disable_tool` precedent); they are available in
single-user/no-auth deployments, which is where they are needed.

### Secrets

Tokens and API keys never appear in RPC results, notifications, server
logs, or persisted history. Snapshots are built from an identity/quota
allowlist; `api_key`/`code` request params are masked in server debug
logging; account events bypass the session journal entirely.

## MCP Server Management, Status & OAuth (`mcp_*` methods)

Stable lifecycle contract for configured MCP servers — the surface
Desktop connector settings and runtime status consume. It *promotes*
the existing machinery (the loader's `mcp_servers:` merge, the
Claude-Code-format user `mcp.json` files, the session MCP lifecycle,
the `mcp.*` server functions) behind typed RPCs; nothing here creates a
second configuration system.

Server identity is the config `name` (`server_name` on the wire) — the
same identifier used by `mcp_servers:` YAML, `disable_mcp_server`,
hosted `server_label`s, and approval grants.

### Config sources and provenance

Every server snapshot carries a `source`:

| `source` | Where it comes from | Writable via this API |
|----------|--------------------|-----------------------|
| `project` | `mcp_servers:` in the agent/project YAML | No (`read_only_reason: "project_manifest"`) |
| `user` | user-global `mcp.json` (`~/.config/<product>/mcp.json`) | **Yes** — the one file this API owns |
| `project_local` | `<project>/.<product>/mcp.json` (hand-managed) | No (`read_only_reason: "project_local_file"`) |
| `session` | dynamic app-scoped servers passed in `variables.mcp_servers` | No (`read_only_reason: "session_scoped"`) |

Merge precedence mirrors the loader: `project` < `user` <
`project_local`. The layered-config keys `allow_user_mcp_servers`,
`security.mcp_require_allowlist`, and `security.mcp_allowed_servers`
gate the user layer; gated-out servers still list with
`enabled: false` and a `disabled_reason`
(`user_mcp_disabled` / `allowlist_blocked` / `kill_switch`).

### Secrets (write-only header/env model)

Following the layered-config secret model: HTTP `params.headers` values
and stdio `params.env` values are **write-only**. Every read path
(`mcp_list_servers`, `mcp_get_server`, status events, validation
errors) replaces them with `{ "<name>": { "is_set": true } }` markers —
names visible, values never. On `mcp_update_server`, the two channels
merge write-only: a string sets a value, `null` deletes one, and an
omitted name keeps the stored (unreadable) value; all other params
replace wholesale.

### Typed errors

| Code | Name | `data.kind` | Meaning |
|------|------|-------------|---------|
| `-32070` | McpServerNotFound | `mcp_server_not_found` | No configured server has `data.server_name`. |
| `-32071` | McpConfigInvalid | `mcp_config_invalid` | Create/update failed validation; `data.errors` = `[{code, field, message}]`, never echoing submitted values. |
| `-32072` | McpServerReadOnly | `mcp_server_read_only` | The server's source is not writable here (`data.reason`: `project_manifest`, `project_local_file`, `user_mcp_disabled`). |
| `-32073` | McpOperationUnsupported | `mcp_operation_unsupported` | The server/transport lacks the capability (`data.operation`: `oauth`, `resources`, `prompts`, ...). |
| `-32074` | McpServerUnavailable | `mcp_server_unavailable` | Connect/start failed; `data.reason_code` as below. |
| `-32075` | McpAuthStateInvalid | `mcp_auth_state_invalid` | Auth operation in the wrong state (`data.reason`: `unknown_auth`, `code_required`). |
| `-32076` | McpAuthFailed | `mcp_auth_failed` | The authorization server rejected/failed the flow (`data.status` when known). |

### Management & discovery methods

- `mcp_list_servers(session_id?)` → `{ servers: [McpServerSnapshot],
  user_mcp_allowed, write_target }`. Each snapshot:
  `server_name`, `source`, `transport`
  (`stdio`/`http`/`sse`/`streamable_http`), redacted `params`,
  `server_options`, `enabled` (+`disabled_reason`), `read_only`
  (+`read_only_reason`), `status` (below), `auth` (below), and — once a
  probe has run — `server_info`, `capabilities`, discovery `counts`,
  `captured_at`. With `session_id`, that session's dynamic servers are
  appended (`source: "session"`) and the request is authorized by
  session ownership.
- `mcp_get_server(server_name, refresh?)` → the full snapshot plus
  discovery detail: `tools`, `resources`, `resource_templates`,
  `prompts` (MCP-spec shapes via `model_dump`; treat unknown fields as
  opaque) and `supported` flags per area. `refresh: true` performs a
  **management probe** — a short-lived out-of-session connection that
  captures the MCP `initialize` result (`server_info`, `capabilities`)
  and lists each supported area; without it the cached snapshot is
  returned.
- `mcp_create_server(server_name, type, params, server_options?)` /
  `mcp_update_server(server_name, type?, params?, server_options?)` /
  `mcp_delete_server(server_name)` — validated CRUD against the
  user-global `mcp.json` (atomic 0600 writes) **and** the live spec, so
  the next session build uses the result. Delete also clears cached
  discovery, status, and any stored OAuth tokens for the server.
- `mcp_reload_server(server_name?)` → `{ ok, reloaded_sessions }` — the
  pick-up-changes chokepoint: re-reads the `mcp.json` files into the
  live spec and tears down active per-session instances of the named
  server (or all), so the next run reconnects from current config.
  Status returns to `configured`.

Session/OOB operation methods (experimental until a client depends on
them; promoted from the `mcp.*` server functions, which remain
available through `server_call`):

- `mcp_read_resource(server_name, uri, session_id?)` — with
  `session_id`, reads through the session's live instance
  (session-owner authorized); without it, through a management probe
  connection.
- `mcp_call_tool(server_name, tool_name, session_id, args?)` —
  `session_id` **required**: OOB tool calls act on behalf of a session
  (MCP-Apps postMessage routing), and its ownership authorizes the
  call. The agent does not observe these calls.
- `mcp_get_prompt(server_name, prompt_name, args?, session_id?)` —
  prompt fetch where the server supports prompts (discovery lists come
  from `mcp_get_server`).

### Startup status (`configured → starting → ready | failed`)

Session builds and management probes report through one transition
contract per server:

- `configured` — known, not started (also after `mcp_reload_server`).
- `starting` — a connection attempt is in flight.
- `ready` — connected and initialized.
- `failed` — with an actionable typed reason: `reason_code` ∈
  `auth_required`, `auth_expired`, `command_not_found`, `timeout`,
  `connect_failed`, `invalid_config`, `start_failed` (+ human-readable
  `reason`).
- `disabled` — kill-switched via `disable_mcp_server`.

`status.origin` records what drove the last transition
(`session:<id8>`, `probe`, `reload`, `auth`, `kill_switch`).

### `mcp_server_status_changed` notification (server → client)

```json
{
  "server_name": "github",
  "status": "failed",
  "previous_status": "starting",
  "reason_code": "auth_expired",
  "reason": "Stored credentials are expired; reauthentication required",
  "auth_state": "expired",
  "at": "2026-07-30T12:00:00Z"
}
```

Broadcast to **every connected client** on each transition. Optional
(may be disabled at `initialize`) and **not journaled** — the
`account_changed` decision, made explicitly here: server status is
process-global, not session history, and replaying stale statuses after
a reconnect would be wrong. Reconnecting clients **refetch** via
`mcp_list_servers`; live sockets receive pushes. Status is held in the
manager across reconnects, so the refetched snapshot always reflects
the latest transition.

### OAuth (`mcp_auth_*`)

HTTP-family servers that require authorization use the MCP OAuth
protocol (RFC 9728 protected-resource discovery, RFC 8414 AS metadata,
dynamic client registration, PKCE) — the account-flow patterns applied
to MCP:

- `mcp_auth_start(server_name, redirect_uri?, session_id?)` → either
  `{ state: "completed", ... }` (stored/refreshed credentials already
  satisfy the server) or `{ state: "pending", auth_id, auth_url,
  redirect_uri }` — the URL-handoff point. The PKCE verifier and all
  tokens stay server-side (a 0600 store under the product config dir).
  With `session_id` (single-user topology only), the URL is also
  surfaced to that session as a `url`-kind elicitation
  (`source: "mcp:<server>"`); accepting it with `{ "code": ... }` or
  `{ "url": "<full redirect URL>" }` completes the flow with no
  separate `mcp_auth_complete` call.
- `mcp_auth_complete(auth_id, code)` — finishes a pending flow; `code`
  may be the raw authorization code or the full redirect URL. Unknown/
  expired flow → `-32075 unknown_auth`; AS rejection → `-32076`.
- `mcp_auth_cancel(auth_id)` → `true` when a pending flow was dropped.
- `stdio` servers authenticate via `params.env`, not OAuth —
  `mcp_auth_start` on one fails with `-32073`.

Snapshots expose `auth`: `{ kind: "oauth" | "headers" | "env" | "none",
state }` with `state` ∈ `authenticated`, `expired`, `pending`,
`required`, `configured`, `not_authenticated`, `not_required`. **Expired
credentials trigger reauthentication**: when a session build or probe
fails because stored tokens no longer refresh, the server broadcasts
`mcp_server_status_changed` with `reason_code: "auth_expired"` /
`auth_state: "expired"` — the client's cue to call `mcp_auth_start`.

### MCP elicitations

Server-initiated `elicitation/create` requests from session-scoped MCP
servers route through the structured user-elicitation protocol (see
"User Elicitation Flow") — never a parallel lane: the request surfaces
as a `form`-kind `elicitation_requested` with
`source: "mcp:<server_name>"` and the MCP server's `requestedSchema` as
`input_schema`; the first `elicitation_response` maps back to the MCP
`ElicitResult` (`accept` + validated content / `decline` / `cancel`).
The elicitation capability is advertised to MCP servers only for
session-scoped connections (management probes decline elicitations).

### Authorization

Reads (`mcp_list_servers`, `mcp_get_server`, `mcp_read_resource`,
`mcp_get_prompt`) are globally safe — redacted and read-only; supplying
the optional `session_id` re-scopes them to the session owner.
`mcp_call_tool` is session-scoped by contract. Config mutations
(`mcp_create_server`/`mcp_update_server`/`mcp_delete_server`/
`mcp_reload_server`) and OAuth flows (`mcp_auth_*`) change process-wide
state every principal shares, so in enforced multi-user deployments
they are refused for individual principals (the
`disable_tool`/`account_login_*` precedent). `mcp_auth_start`
additionally refuses its `session_id` parameter in enforced mode so the
parameter cannot re-scope a credential mutation.

### Secrets recap

Header/env values are write-only markers on every read path; OAuth
tokens, verifiers, and client secrets never appear in RPC results,
notifications, logs (`code` is masked in server debug logging), or the
session journal (status events are not journaled at all).
## Conversation Domain Objects (`get_thread`, `list_turns`, `list_items`, `get_item`)

The canonical, persisted structure of a conversation: **Thread → Turn →
Item**. It exists so a client can render a conversation *deterministically*
— paginating a long history, showing a branch point, resuming after a
restart — without replaying the transient event stream.

The guarantee: **a completed conversation reconstructs from these reads
alone.** Every item is stored in exactly one authoritative final state, so
`list_items` returns everything needed to paint the transcript. The
`replay` capability (§ Durable Sequenced Event Replay) remains the
mechanism for catching a *live* run up mid-flight; the two are
complementary, not alternatives.

Nothing here is a second source of truth. Each field is a promotion of
state the runtime already produces, mapped as follows.

### Mapping from existing concepts

| Existing concept | Promoted to | Notes |
| --- | --- | --- |
| session (`sessions` row) | **Thread** | `thread_id == session_id`; every existing session id keeps working as an address |
| run (`run_id`) | **Turn** | `turn_id == run_id` for recorded turns |
| `run_started.prompt` / `prompt_role` | Turn `prompt`/`prompt_role` + a `user_message`/`agent_message` item | `prompt_role: "assistant"` (notification wakeups) records as an agent message |
| `run_status.attempt` | Turn `attempts` | retry bookkeeping |
| `run_end.end_reason` / `error` / `usage` / `model` | Turn `status`/`error`/`usage`/`model` | `end_reason` maps to one terminal status |
| `token.totals` | Turn `usage` | cumulative totals **replace**, never accumulate |
| `tool_called` + `tool_result` | ONE `tool_call` item | correlated by `call_id`; started → completed/failed |
| `tool_approval_requested` / `_resolved` | `approval` item (`approval_kind: "tool"`) | the decision is attached by the `tool_approval_response` handler before the item goes terminal |
| `mcp_approval_requested` / `_resolved` | `approval` item (`approval_kind: "mcp"`) | carries `server_label` |
| `elicitation_requested` / `_resolved` | `elicitation` item | `cancelled`/`expired` land as `cancelled` |
| `client_request` with `function: "ui.add_artifact"` | `artifact` item | keyed on `artifact_id` when present, so re-display updates in place |
| `message_output` | `agent_message` item | |
| history `reasoning` rows | `reasoning` item | projection only (live reasoning is not broadcast) |
| `context_summary` checkpoint (`core/runtime/compaction`) | `compaction` item + Thread `compaction` floor | |
| `fork_session` / `session_forked` | Thread `parent_thread_id` + `branched_from_item_id` | branching is a relationship, not a copy |
| session archive flag | Thread `status` | `active` / `archived`; kept in step by `archive_session` |
| durable structured plans | `plan` item (**reserved**) | the kind and its lifecycle rule are declared; plan *recording* lands with tkt_FqUypyxF2tzC |
| run-level aggregated file diff | `run_diff` item (**reserved**) | individual edits are already `tool_call` items with `tool_kind: "file"`; the per-run *aggregate* has no home on `Turn`, which carries typed metadata rather than free-form content. Turn-scoped, so no `long_lived` exemption. Also tkt_FqUypyxF2tzC |

Tool items additionally carry a `tool_kind` taxonomy so clients can render
without a tool registry: `command`, `file`, `search`, `web`, `mcp`, `plan`,
`artifact`, `worker`, `other`. An MCP server's `write_file` classifies as
`mcp`, not `file` — the `server_label` wins.

### Object shapes

`Thread`:

```json
{
  "thread_id": "sess-1", "user_id": "u1", "status": "active",
  "title": null, "created_at": 0.0, "updated_at": 0.0,
  "last_seq": 8, "turn_count": 2, "item_count": 8,
  "parent_thread_id": null, "branched_from_item_id": null,
  "usage": {"input_tokens": 60}, "compaction": {},
  "source": "recorded", "projected_at": 1785479178.6, "schema_version": 1
}
```

`Turn`:

```json
{
  "turn_id": "run_abc", "thread_id": "sess-1", "ordinal": 1,
  "status": "completed", "created_at": 0.0, "updated_at": 0.0,
  "completed_at": 0.0, "prompt": "add a test", "prompt_role": "user",
  "end_reason": "completed", "error": null, "usage": {"input_tokens": 40},
  "model": "gpt-x", "model_ref": "catalog/gpt-x",
  "item_count": 5, "first_seq": 1, "last_seq": 5, "attempts": 0,
  "source": "recorded", "schema_version": 1
}
```

`Item`:

```json
{
  "item_id": "itm_1f0c…", "thread_id": "sess-1", "turn_id": "run_abc",
  "seq": 2, "kind": "tool_call", "status": "completed", "role": "assistant",
  "created_at": 0.0, "updated_at": 0.0, "completed_at": 0.0, "revision": 1,
  "content": {"tool": "read_file", "tool_kind": "file", "input": "{…}",
              "output": "def f(): …"},
  "source_ref": {"event": "tool_called", "call_id": "c1"},
  "long_lived": false, "source": "recorded", "schema_version": 1
}
```

`content` is an **open record** whose fields follow `kind`. That is
deliberate: a closed schema would make every new tool field a protocol
break. Clients probe the fields they understand.

**`Thread.item_count` / `Thread.turn_count` / `Turn.item_count` are counted
at read time**, under the same predicate the corresponding listing uses.
The stored counters are maintained by non-transactional read-modify-write
and can drift under concurrent turns, so they are treated as bookkeeping
and the wire value is recomputed per request. A client sizing a scrollbar
from `item_count` gets a true number.

**`kind` values `plan` and `run_diff` are RESERVED and not yet produced.**
They are declared so the durable-plan and run-diff work (tkt_FqUypyxF2tzC)
is additive against this `stable` surface rather than a breaking widening
of a closed client union. No server today emits an item of either kind; a
client should tolerate them but will not receive them yet.

### Ordering and canonical ids

`seq` is a thread-monotonic ordinal and is **the only ordering clients
should rely on**. Wall-clock timestamps are not monotonic under concurrent
tool completions.

`item_id` is `"itm_" + sha256(thread_id \0 natural_key)[:32]`, where the
natural key is built **only from fields the originating payload carries** —
never from a server-side position:

| Kind | Natural key |
| --- | --- |
| `tool_call`, `approval`, `elicitation` | the correlation id (`tool:<call_id>`, `approval:<kind>:<id>`, `elicitation:<id>`) |
| turn prompt | `prompt:<run_id>` |
| `agent_message` | `message:<message_id>` when the provider minted one (the optional `message_output.message_id` field), else `message:<turn_id>:<sha256(content)[:16]>:<n>` |
| mid-run `user_message` | `user:<run_id>:<sha256(content)[:16]>:<n>` |
| `reasoning` | `reasoning:<rs_id>`, else `reasoning:<turn_id>:<sha256(summary)[:16]>:<n>` |
| `artifact` | `artifact:<artifact_id>`, else `artifact:req:<request_id>` |
| `compaction` | `compaction:<sha256(summary)[:16]>:<n>` |

`<n>` is a zero-based **occurrence ordinal**: the number of items already
recorded under the same digest key. It exists so repeated content is not
silently collapsed — a user sending "continue" twice in one run gets two
items, not one — and it stays client-derivable because a client counts its
own repetitions of the same content in the same run. Keys built on a real
id (`call_id`, `message_id`, `elicitation_id`, `artifact_id`) carry no
ordinal and are strictly idempotent: re-delivering such an event updates
the existing item rather than appending.

Three independent producers — the live recorder, the compatibility
projection, and a client folding the notification stream — therefore agree
on the same id for the same logical thing without a shared allocator, and a
client can compute the id itself to merge live events against a stored
page. Ids are stable across restarts and across migration. This is also why
no item-update notification exists: the live stream already carries the
deltas, and the ids to correlate them are derivable from payloads clients
already receive.

**The trade-off, stated plainly.** Digest-plus-ordinal keys preserve
repeated content — the transcript is a record — but they are only
idempotent against re-delivery for the id-keyed kinds. A digest-keyed event
delivered twice through a path that bypasses the emission chokepoint would
append a second item; the chokepoint guarantees one call per original
emission, so that does not arise in practice. Compaction checkpoints are
never broadcast, so their ids are not client-derivable — but they also
never appear in the live stream, so there is no merge boundary for them.

### Item lifecycle

```
started ──(0..n updates: revision++, updated_at)──► completed | failed | cancelled
```

- An item is born `started` and reaches **exactly one** terminal state.
- Updates before the terminal state bump `revision`, so a client can tell a
  stale cached copy from a current one.
- **The first terminal state wins.** A duplicate `tool_result` — replayed,
  double-delivered, or a retried run reusing a `call_id` — cannot rewrite a
  settled item.
- At `run_end` the server finalizes every item still open on the thread that
  belongs to the ending turn *or carries no `turn_id` at all* (an
  elicitation or `client_request` raised outside a run): `cancelled` when
  the run was cancelled, otherwise `failed`, with
  `content.finalized_by = "run_end"` recorded so a reader can distinguish
  an inferred terminal state from an observed one. **After `run_end` the
  only non-terminal items that remain are those explicitly marked
  `long_lived`** — which is what makes store-only reconstruction sound.
- **`long_lived: true`** exempts an item from that sweep. It is for objects
  that genuinely outlive their turn — a durable plan accumulates steps
  across many runs — and it is a property of the row, not an inference from
  `kind`, so the exemption is always deliberate and inspectable. The owning
  subsystem is then responsible for landing the item terminally. No
  first-party producer sets it yet; the flag and `ItemKind.plan` are
  declared now so durable plans (tkt_FqUypyxF2tzC) are an additive change
  to a `stable` surface rather than a breaking one.
- **Terminal governs status, not content.** One kind revises after
  completion: re-issuing `ui.add_artifact` with the same `artifact_id`
  means "here is the new version", so the item's `content` is replaced and
  `revision` is bumped while `status`/`completed_at` stay put. Every other
  kind is immutable once terminal.

Turns follow the same shape: `running` → `completed | failed | cancelled`,
terminal once.

### Pagination

Keyset, not offset. `list_items` walks `seq`; `list_turns` walks `ordinal`.
The `cursor` is an opaque token carrying that position, so a thread being
written while a client pages through it neither skips nor repeats rows.

```
list_items(thread_id, turn_id?, kinds?, limit?, cursor?, order?)
  → { thread_id, turn_id, items[], next_cursor, has_more, total }

list_turns(thread_id, limit?, cursor?, order?)
  → { thread_id, turns[], next_cursor, has_more, total }
```

- `limit` defaults to 50 and is **clamped** into `[1, 500]` — `limit=0` or a
  negative value returns one row rather than erroring, so a client
  computing a window size mid-scroll is not interrupted.
- `total` is counted under the **same predicate the page was drawn with**,
  `turn_id` and `kinds` included.
- A cursor that is malformed, carries the wrong field, or carries a
  negative position is rejected with `-32082`; a cursor past the end
  returns an empty final page.
- `list_items(turn_id=…)` returns only items tied to that turn. A
  compaction checkpoint triggered *outside* any run has `turn_id: null` and
  is reachable only through the unscoped read — so a turn-by-turn
  reconstruction should page the thread unscoped, or fetch
  `kinds: ["compaction"]` separately.
- `order` is `"asc"` (default) or `"desc"`.
- `kinds` filters to a set of item kinds; unrecognized kinds are dropped
  from the filter rather than rejected, so a newer client asking for a kind
  this server does not know gets a narrower page, not an error.
- Iterate until `has_more` is false. `next_cursor` is null on the last page.

### Backward compatibility

Sessions written before this surface existed have `history`/`sessions` rows
and no domain rows. They are **projected and persisted (migrated) exactly
once, at the moment the thread row is first created**, and served from the
store thereafter.

That ordering is the contract, and it is load-bearing in both directions:

* The **recorder** projects before it creates a thread row. It claims a
  thread on the first notification of a run, and a session being *resumed*
  already has a transcript — creating the row empty would make that
  transcript permanently invisible.
* The **reader** projects only when no row exists at all. Re-projecting a
  populated thread would overwrite rows the recorder owns and reset the
  ordinal cursor, which destroys items.

Together: *a thread row exists ⇒ its prior history has been projected
exactly once*. Reads never write to an existing thread.

The decision is recorded **explicitly** on `Thread.projected_at` rather
than inferred from whether the thread has any items. A thread can
legitimately hold zero items, and inferring a lifecycle fact from a row
count is what produced this feature's worst bugs.

Every write to session history materializes the thread first — the guard
lives in `Session.append_message`, not at individual call sites, because
four separate sites needed it and a fifth would have been missed.

- Projected objects are labelled `source: "adapter"`; live ones are
  `source: "recorded"`.
- Legacy history carries no run correlation, so turn boundaries are
  *inferred* — a new turn opens at each user message — and inferred turns
  get a `trn_legacy_` id prefix.
- `function_call` + `function_call_output` collapse into one completed
  `tool_call` item. A call with no output row is `failed` (it never
  returned), never left at `started`.
- Migration refuses to touch a thread that already holds items, so it is
  safe to re-run against a live deployment.
- A **fork** is materialized by copying the parent's domain rows, not by
  re-projecting the copied history — a projection cannot see approvals,
  artifacts or elicitations, which never reach the history table. Child
  item ids are re-derived for the child thread and record
  `source_ref.forked_from_item_id`.
- **An item still in flight when the fork is taken is landed `cancelled` in
  the CHILD**, with `content.finalized_by = "fork"`. The parent's run will
  finish and finalize the parent's copy, but no `run_end` for that run ever
  reaches the forked thread, so an open copy there would stay `started`
  forever and falsify the run-end guarantee. `long_lived` items are exempt,
  as everywhere else.
- **`turn_id` is copied verbatim across a fork, so it is unique per thread,
  not globally.** A turn id is a `run_id`, which used to be globally
  unique; after a fork the parent and child both report the same turn ids
  for the copied turns. Every read on this surface is thread-scoped, so
  nothing on the server is ambiguous — but a client caching turns by
  `turn_id` alone will collide across a fork. **Key turn caches on
  `(thread_id, turn_id)`.** Item ids do not have this property: they are
  re-derived per thread and stay globally distinct.
- Thread-level `source` records how a thread ORIGINATED (`adapter` = began
  as a projection). Items carry their own `source`, so a resumed legacy
  thread reads as `adapter` items followed by `recorded` ones.
- Projected threads carry `created_at`/`updated_at` of `0.0`: the raw
  history rows the projection reads do not expose per-row timestamps.
  Ordering is by `seq`, which is authoritative.

### Authorization and stability

All four methods are **`RESOURCE_SCOPED`**. A thread *is* a session
(`thread_id == session_id`) and its turns and items are that session's
transcript, so ownership of the session gates the read; a cross-user
attempt is refused with `-32014` (`data.kind: "authorization_denied"`,
`data.resource: "thread"`). In an enforced (multi-user) deployment an
*unknown* thread also returns `-32014` rather than `-32080`, because a
failed ownership lookup denies — which additionally means thread-id
existence cannot be probed.

There is deliberately no globally-safe listing counterpart: `list_sessions`
already covers self-scoped discovery.

The methods are **`stable`** on entry. Desktop renders transcripts from
this surface, and a deterministic-rendering guarantee is worthless if
clients must opt in through `initialize.capabilities
.experimental_operations`. Result payloads stay open records while the item
`content` shape is still growing.

### Typed errors

| Code | Name | `data.kind` | Raised by |
| --- | --- | --- | --- |
| `-32080` | ThreadNotFound | `thread_not_found` | all four methods |
| `-32081` | ConversationItemNotFound | `item_not_found` | `get_item` |
| `-32082` | ConversationCursorInvalid | `cursor_invalid` | `list_turns`, `list_items` |

### Storage

Domain objects persist alongside the session history, in whichever backend
`OMNIAGENTS_HISTORY_BACKEND` selects: SQLite (colocated at
`<sessions.db>.domain`) or Postgres (`conversation_threads` /
`conversation_turns` / `conversation_items`, tenant-scoped with row-level
security pre-positioned on the `app.current_tenant` GUC, matching
`PgSessionStorage`).

The write path is fed from `stamp_session_event` — the single chokepoint
every original notification emission already passes through — plus explicit
hooks for the facts that never broadcast: mid-run `send_user_message`
appends, compaction checkpoints, reasoning blocks (written straight to
history by the SDK), and approval decisions (the `*_approval_resolved`
notifications carry only a correlation id). Each of those hooks
materializes the thread *before* its row lands in history, so the
creation-time projection cannot record the same fact twice.

`delete_session` drops the thread, its turns and its items alongside the
history and the replay journal; `archive_session` moves `Thread.status` to
`archived`.

Deletion durability: the SQLite store runs with `PRAGMA secure_delete = ON`,
so freed pages are zeroed rather than left readable in the free list — a
deleted transcript is not recoverable from the file. (This is cheaper than
`VACUUM`, which would rewrite the whole database under an exclusive lock on
every delete.) On Postgres, deleted tuples remain until autovacuum reclaims
them, matching the existing session-history posture.

## Workspace Filesystem Watch & Transfer (`fs_*` methods, experimental)

GUI clients keep a session's workspace view synchronized without polling
and move large files without oversized JSON frames. All `fs_*` operations
are **experimental**: clients opt in through
`initialize.capabilities.experimental_operations` (requests *and* the
three notifications), and un-granted calls fail with `-32013`.

Every wire path is **workspace-relative POSIX** and canonicalized
server-side before any I/O: absolute paths, drive letters, backslashes,
NUL bytes, and `..` traversal above the root are rejected with `-32060
fs_path_invalid`. On host workspaces the resolved path (symlinks
included) must stay inside the workspace root, so symlink escapes are
also `-32060`. Every method names a `session_id` and is authorized by
session ownership — another principal's workspace answers `-32014`
before any filesystem access happens.

### Substrate model

File access rides the same session/sandbox boundary the terminal uses:
the server resolves each session to a `Workspace` (host filesystem by
default; products that host the workspace inside a sandbox container or
remote backend install `AgentService.workspace_factory`). Watching is an
honest server-side polling scanner with coalesced diffs; the `fs_watch`
result reports the mechanism:

| mechanism        | substrate            | snapshot source                        | default poll |
|------------------|----------------------|----------------------------------------|--------------|
| `poll:host`      | host filesystem      | `os.scandir` walk in a worker thread   | 1000 ms      |
| `poll:workspace` | sandbox / remote     | recursive `Workspace.list_dir` calls   | 2000 ms      |

Symlinked directories are recorded but never descended into, so links
cannot pull foreign trees into a watch.

### Watching

- `fs_watch {session_id, path, recursive?, poll_interval_ms?}` →
  `{watch_id, session_id, path, recursive, mechanism, poll_interval_ms}`.
  The baseline snapshot is taken at subscribe time; only subsequent
  changes produce events. Poll interval is clamped to 200–30000 ms.
- `fs_unwatch {session_id, watch_id}` → `true`.
- `fs_events {session_id, watch_id, events}` — server notification. Each
  event is `{type: "created"|"modified"|"deleted", path, entry_type:
  "file"|"directory"}`. Diffs are coalesced per poll cycle (at most one
  event per path per cycle) and batched into frames of ≤128 events.
- `fs_rescan_required {session_id, watch_id, reason}` — overflow signal:
  - `reason: "event_overflow"` — a single cycle changed more than 512
    paths. The events are **not** delivered; the client relists
    (`fs_list`) the watched path. The watch keeps running.
  - `reason: "scan_limit_exceeded"` — the tree grew past the watchable
    limit (25 000 entries). The watch terminates; re-subscribe to a
    narrower path (per-directory non-recursive watches scale to any
    repo, mirroring how file-explorer UIs watch expanded folders).

Watches are **per-connection state, never journaled** in the durable
replay log (`resume_session` will not replay them). Rationale: change
events are high-volume, coalescible, and fully derivable from the tree
itself, so replaying them after a gap would be dishonest. On reconnect a
client re-subscribes with `fs_watch` and rescans with `fs_list` — the
same recovery as `fs_rescan_required`.

Flood posture (#292): coalescing plus the overflow signal bound every
frame, so a build touching 100k files becomes one `fs_rescan_required`,
never a burst that overflows the transport queue or kills the connection
with `TransportOverloadedError`.

### Listing

- `fs_list {session_id, path, recursive?}` →
  `{path, entries: [{path, type, size, mtime}], truncated}` — the rescan
  primitive after `fs_rescan_required` and after reconnect. `truncated`
  is set when the listing hit the scan limit.
- `fs_stat {session_id, path}` → `{path, type, size, mtime, mime}`.

### Chunked transfers

Large payloads never ride in a single JSON-RPC frame. Transfers use
**chunked frames over the existing `/ws` connection** (base64 chunks of
≤256 KiB, explicit offsets) rather than a side-channel HTTP byte-range
endpoint. Rationale: the deployed server surface is WebSocket-first —
the remote proxy mode (`remote_ws_url`) forwards only `/ws*` routes, the
desktop launcher already tunnels this socket, and terminal streaming set
the precedent for chunk-over-WS. An HTTP endpoint would need its own
ticket-auth path (#294) and would not traverse existing proxies.

Downloads are **client-pull** — each chunk is a request/response, so
flow control is inherent and cancellation is just closing the transfer:

- `fs_download_open {session_id, path}` → `{transfer_id, session_id,
  path, size, mime, mtime, sha256, chunk_size}`. The server hashes the
  file at open (one streaming pass); on non-host substrates the content
  is staged server-side in one substrate read so chunks serve a stable
  point-in-time view. The path is resolved **once**, here: subsequent
  chunks come from the file handle this open retained, so a path replaced
  with an outward-pointing symlink mid-transfer cannot redirect reads
  outside the workspace root. Files above the transfer limit (512 MiB,
  the download mirror of the upload cap) are refused with `-32062
  reason: "size_out_of_range"` before any content is read.
- `fs_download_read {session_id, transfer_id, offset?, length?}` →
  `{transfer_id, offset, data, bytes, eof, size}`. Omitting `offset`
  continues sequentially; passing it resumes after a reconnect.
- `fs_download_close {session_id, transfer_id}` → `true` (cancel or
  finish).

Uploads are sequential staged chunks, committed atomically:

- `fs_upload_open {session_id, path, size, sha256?, overwrite?}` →
  `{transfer_id, session_id, path, chunk_size}`.
- `fs_upload_chunk {session_id, transfer_id, offset, data}` →
  `{transfer_id, received, size}`. `offset` must equal the bytes
  received so far (`-32062 reason: "offset_mismatch"` otherwise), so a
  lost frame can never corrupt the staged file.
- `fs_upload_commit {session_id, transfer_id}` → `{path, size, sha256}`.
  Verifies the declared size and (when supplied) sha256 against the
  received bytes (`-32062 reason: "checksum_mismatch"`), then moves the
  staged content into place (`os.replace` on host; one substrate write
  elsewhere).
- `fs_upload_abort {session_id, transfer_id}` → `true` — cancellation
  mid-transfer discards the staging file.

`fs_transfer_progress {session_id, transfer_id, direction, transferred,
total}` is emitted (throttled) to the owning connection during both
directions and on completion.

Transfers, like watches, are bound to the creating connection: after a
reconnect, reopen and resume via `offset`. A transfer id presented from
another connection — even by the same user — answers `-32062
fs_transfer_invalid`.

### Errors

| code   | kind                  | meaning                                              |
|--------|-----------------------|------------------------------------------------------|
| -32060 | `fs_path_invalid`     | absolute/escaping/malformed path, symlink escape     |
| -32061 | `fs_not_found`        | missing file/directory, unknown session workspace    |
| -32062 | `fs_transfer_invalid` | unknown transfer, bad offset/length, size/checksum mismatch, payload above the 512 MiB transfer cap, target exists, transfer limit |
| -32063 | `fs_watch_invalid`    | unknown watch, watch limit, tree too large           |

## Typed Git Operations & Interactive Diff (`git_*` methods, experimental)

Safe, reviewable Git workflows that never require a client to parse shell
output. Every method is **session-scoped** (`RESOURCE_SCOPED`, so another
principal's repository answers `-32014`) and **experimental**: clients opt
in through `initialize.capabilities.experimental_operations`.

All git invocations ride the same `Workspace` boundary the terminal and
the `fs_*` surface use, so the repository may live on the host, inside a
sandbox container or on a remote backend. Commands are argv lists run with
`shell=False` against `git -C <repo>` — never a shell string, never the
server's own cwd. A substrate that cannot run a shell answers
`-32095 git_capability_unavailable` instead of failing obscurely.

### Addressing a repository

`git_list_repositories {session_id, path?, max_depth?}` →
`{session_id, path, repositories: [{repo, root, absolute_root, branch,
detached, bare, git_common_dir, is_linked_worktree}], truncated}`.

`repo` — the workspace-relative POSIX path of the repository root, `"."`
for the workspace root itself — is the **only** accepted repository
address, and it is required on every other method. There is no implicit
"the" repository, which is what makes a multi-repository project
unambiguous.

Confinement rests on four independent facts, all compared against the
*substrate's own* workspace root (a container path inside a sandbox, a
host path on the host):

1. the wire path canonicalizes to a relative POSIX path (no `..`, no
   absolute or drive-letter paths, no backslashes);
2. the resolved directory is inside the workspace root. This is checked
   by the git surface itself rather than delegated to the workspace's own
   `confine` flag, which some embedders switch off;
3. `rev-parse --show-toplevel` is that same directory, so a workspace
   nested *inside* a larger repository has no operable repository —
   `-32090` with `reason: "outside_workspace"` — rather than silently
   operating on the parent project;
4. `rev-parse --absolute-git-dir` **and** `--git-common-dir` are inside
   the workspace root too. Without (4) a plain **file** named `.git`
   holding `gitdir: /outside/.git` satisfies (3) while every object, ref,
   index and config entry is read from — and written to — outside the
   workspace.

A repository without a work tree (a bare repository) answers `-32090`
with `reason: "no_work_tree"`.

`git_list_repositories` also reports the launcher's published `--source`
descriptors: a repository that *is* a published source carries it in
`source`, and any published source that is not reachable inside the
workspace appears in `unreachable_sources` with
`reason: "not_in_workspace"` — the gap is shown, not hidden. Every remote
URL leaving this surface (`source.repoUrl`, `unreachable_sources[].repo_url`
and `git_push`'s `remote_url`) has its **userinfo stripped**, so a
credential-bearing URL a launcher published — `https://user:token@host/…`,
or a bare token in the userinfo — is never echoed back.

Discovery clamps: `max_depth` is capped at **4**. The effective value
comes back as `max_depth`, `max_depth_clamped` says whether the request
was reduced, and `truncated` is true whenever the answer is incomplete
for *any* reason (the 64-repository cap or a clamped depth).

### Reading state

- `git_status {session_id, repo, include_untracked?, include_ignored?,
  paths?}` → `{repo, head: {detached, unborn, branch, oid}, upstream:
  {name, ahead, behind} | null, entries: [...], untracked, ignored,
  conflicted, stash_count, clean, state}`. Parsed from
  `--porcelain=v2 --branch -z`; `state` is one of `clean`, `merging`,
  `rebasing`, `cherry_picking`, `reverting`, detected from the pseudo-refs
  by exit code. Detached and unborn HEADs are flags, not sentinel strings.
  Caveat: `rebasing` is detected via `REBASE_HEAD`, which git writes for a
  rebase stopped on a **conflict**; a rebase paused at an `edit` or
  `break` step writes no such ref and reports `clean` (with a detached
  HEAD). Detecting that exactly needs a git-directory probe on every
  status call and is deferred.
- `git_log {session_id, repo, rev?, max_count?, skip?, paths?}` →
  `{commits: [{oid, short_oid, parents, author_*, authored_at,
  committer_*, committed_at, refs, subject, body}]}`, read from a
  `%x00`/`%x1e`-delimited `--format`, so subjects and bodies may contain
  anything.
- `git_list_branches {session_id, repo, include_remote?}` →
  `{branches: [{ref, name, oid, remote, upstream, upstream_remote,
  current, worktree_path, category}]}`. `category` is `worker` for the
  `worker/` namespace `WorktreeIsolation` creates, otherwise `branch`.
- `git_list_worktrees {session_id, repo}` →
  `{worktrees: [{path, head, branch, detached, bare, locked, lock_reason,
  prunable, repo, accessible, inaccessible_reason, category}]}` from
  `git worktree list --porcelain`. Worker worktrees live under the
  framework scratch root, i.e. outside the session workspace: they are
  reported with `accessible: false` and `inaccessible_reason:
  "outside_workspace"` and cannot be operated on through this surface.
- `git_conflicts {session_id, repo, paths?}` →
  `{state, conflicts: [{path, stages, regions, regions_available}],
  content_truncated}`. Each stage is
  `{mode, oid, content, content_truncated}`: identities from
  `git ls-files -u -z`, contents from `git cat-file blob <oid>`. The
  contents matter because under git's default `merge` conflict style the
  working file has **no** base section, so `regions[].base` is null — the
  stage blobs are what make base/ours/theirs available to a three-way
  merge editor without a separate blob-read RPC. Contents are decoded as
  text and capped at 1 MiB per blob (`content: null` with
  `content_truncated: true` above it) for the first 25 conflicted paths
  (`content_truncated` at the top level when there are more); binary
  conflicts should be resolved file-level. Regions are parsed from the
  fixed ASCII conflict markers (both `merge` and `diff3` styles), which
  are part of git's file format, not its message catalog.

### The diff and hunk model

`git_diff {session_id, repo, mode?, paths?, context_lines?, from_rev?,
to_rev?}` → `{repo, mode, context_lines, files: [...]}`.

`mode` is `worktree` (index → worktree, the default), `staged`
(HEAD → index), `head` (HEAD → worktree) or `range` (`from_rev` required,
`to_rev` optional).

Each file carries `{path, old_path, change, old_mode, new_mode, old_oid,
new_oid, similarity, binary, added_lines, deleted_lines, unmerged,
submodule, hunk_selectable, hunks}`. File-level metadata is taken from
`--raw -z` and `--numstat -z` (`-`/`-` is the structural binary marker);
hunks are parsed from the unified-diff grammar with prefixes pinned to
`a/`/`b/`. Unmerged paths appear with `unmerged: true` and no hunks —
`git_conflicts` is their representation.

Each hunk carries `{hunk_id, index, header, section_heading, old_start,
old_lines, new_start, new_lines, lines}` where every line is
`{origin: "context" | "add" | "delete" | "no_newline", content,
old_lineno, new_lineno}` — a renderer never re-derives meaning from a `+`
or `-` prefix character.

`hunk_id` is a **content-addressed** digest over the diff scope, the path
and the full hunk body. Two consequences a client can rely on:

* the same hunk keeps the same id across repeated `git_diff` calls, so a
  selection UI can hold ids across refreshes, and
* the moment the underlying change moves, the id stops resolving, and the
  operation fails closed with `-32094 git_state_invalid`
  (`reason: "hunk_not_found"`, plus the offending `{path, hunk_id}` pairs)
  instead of applying somewhere unintended.

`hunk_selectable` is false for binary blobs, submodule pointers, unmerged
paths and **type changes** (a file replaced by a symlink or the reverse —
git renders one such change as two whole-file patches, which has no single
coherent line numbering). Those are file-level only, and the client learns
it from the diff rather than from a failure.

Diff clamps: `context_lines` is capped at **64**; the effective value comes
back as `context_lines`, with `context_lines_clamped` when the request was
reduced. `from_rev`/`to_rev` outside `mode: "range"` are a typed error
(`-32094`, `reason: "parameter_not_applicable"`), never silently dropped.

`git_log` caps `max_count` at **1000**; the effective value comes back as
`max_count` and `truncated` is true when the caller asked for more history
than was returned. Any git invocation whose output would exceed **16 MiB**
answers `-32092` with `reason: "output_truncated"` rather than returning a
half-read diff.

### Staging, unstaging and discarding

All three take a selection of whole `paths`, individual `hunks`
(`[{path, hunk_id}]`), or both — a path may not appear in both halves of
the same call (`reason: "overlapping_selection"`).

- `git_stage {session_id, repo, paths?, hunks?, context_lines?, mode?}` →
  `{staged_paths, staged_hunks}`. `mode` names the diff the `hunks` came
  from: `worktree` (default) or `head` — the "all my changes" view a UI
  usually opens on. Hunk ids are scope-bound, so staging a `head` diff's
  hunks requires saying so; a head-scope stage additionally needs the file
  to be unstaged (its index entry must still match HEAD), otherwise git
  refuses and the typed failure says so.
- `git_unstage {session_id, repo, paths?, hunks?, context_lines?}` →
  `{unstaged_paths, unstaged_hunks}`.
- `git_discard {session_id, repo, paths?, hunks?, context_lines?,
  confirmation_token?}` → `{discarded_paths, discarded_hunks}`.
  **Destructive** — see below.

Hunk selections are applied by rebuilding a minimal patch from a
*freshly recomputed* diff and feeding it to `git apply`. Line numbers are
recomputed for the direction of the apply: staging preserves the old side
(the index is the preimage) and shifts the new side by the size change of
the hunks included before it; unstaging and discarding are reverse applies,
so the new side is preserved and the old side shifts instead. Dropping a
hunk therefore never leaves the remaining hunks claiming line numbers that
no longer exist.

Only `paths` staging picks up untracked files — a file git has never seen
has no diff to select hunks from. On an unborn branch, `git_unstage` drops
the index entry (there is no HEAD to restore from).

`paths` are git **pathspecs**, so a directory or a wildcard is legal and
may also cover a file selected by hunk. Exact and ancestor-directory
overlaps are refused up front (`reason: "overlapping_selection"`); a
wildcard that git expands onto the same file is only discovered when the
hunk step runs, and is rolled back (see below).

Mutating operations are serialized per `(session, repository)`, so
concurrent calls — a deck of parallel agent columns, or a user clicking
"stage hunk" quickly — queue instead of losing to git's `index.lock`.
Contention from a git process *outside* this server still surfaces, as
`-32092` with `reason: "index_locked"`; the signal is the lockfile's
existence, never git's message text.

### Failure rollback

`git apply` is all-or-nothing, so a hunk-only operation cannot half-apply.
An operation with **both** `paths` and `hunks` runs two git commands, so
the index tree is captured with `git write-tree` before the first and read
back with `git read-tree` if a later step fails; the error then carries
`rolled_back: true`. The rollback is not limited to git-level failures —
an OS-level error or a cancellation between steps restores the snapshot
too. An index that cannot be snapshotted (unmerged entries) refuses
multi-step operations outright (`reason: "unmerged_index"`) rather than
risking a partial result.

Patches are written to a scratch file inside the repository's git
directory and passed to `git apply` as an argument, so patch size is
unbounded and no code path in this surface runs a shell.

`git_discard` cannot be undone by definition, so it is validated
end-to-end *before* anything runs — every path must have tracked changes,
every hunk id must resolve — and then applied as a single atomic reverse
patch (`--binary`, so binary and deleted files restore correctly).

### Destructive-action policy

Five operations never run on a bare request:

| operation | trigger |
|-----------|---------|
| `discard_changes` | any `git_discard` |
| `reset_hard` | `git_reset` with `mode: "hard"` |
| `checkout_discard_changes` | `git_checkout` with `discard_changes: true` |
| `force_push` | `git_push` with `force`, `force_with_lease`, or a refspec with a leading `+` |
| `delete_remote_ref` | `git_push` with a refspec whose source side is empty (`:refs/heads/x`) |
| `amend_commit` | `git_commit` with `amend: true` |

Destructive intent lives in the *refspec* as much as in the flags, and can
come from repository configuration that mentions neither
(`remote.<name>.push = +refs/…`, `remote.<name>.mirror = true`). So the
gate does not predict git's decision from the request: the identical argv
runs once with `--dry-run` first and the confirmation is driven by the
porcelain flags git itself reports (`+` forced, `-` deleted). The
`impact` then carries `planned_updates` — the refs git said it would
rewrite or remove.

The first call answers `-32093 git_confirmation_required` with

```json
{
  "kind": "git_confirmation_required",
  "operation": "reset_hard",
  "repo": "apps/web",
  "confirmation_token": "<single-use>",
  "impact": { "target": "HEAD", "head_oid": "…", "dirty_paths": ["a.txt"] },
  "reason": "confirmation_required"
}
```

The client shows `impact` and re-sends the same call with
`confirmation_token`. Tokens are single-use, expire, and are bound to the
exact `(session, repo, operation, impact)` they were minted for — the
impact is **recomputed from live repository state** at redemption, so a
repository that moved in between invalidates the token and the client is
shown the new impact (`reason: "stale_confirmation"`). A token minted for
one session, repository or operation cannot unlock another.

Every impact that describes lost work carries a `content_digest` of the
exact bytes involved, not just the paths: `git_discard` digests the patch
it would reverse, and `reset_hard`/`checkout_discard_changes` digest
`git diff HEAD --binary`. A parallel agent column rewriting a file between
the prompt and the confirmation therefore invalidates the token instead of
destroying work the user never saw.

### Branches, commits and history

- `git_commit {session_id, repo, message, amend?, allow_empty?, author?,
  confirmation_token?}` → `{oid, amended}`. Hooks are **not** run
  (`--no-verify`): an RPC surface must not execute repository-supplied
  code on behalf of a remote client. Committing with nothing staged is
  `-32094` (`reason: "nothing_staged"`), not a shell error.
- `git_checkout {session_id, repo, branch, create?, start_point?, detach?,
  discard_changes?, confirmation_token?}` → the post-switch `git_status`
  payload.
- `git_reset {session_id, repo, mode?, rev?, paths?,
  confirmation_token?}` → the post-reset `git_status` payload. A pathspec
  reset is always mixed.

Revisions, refspecs and remote names that begin with `-` are refused
(`reason: "invalid_revision"` / `"invalid_remote"`): commands are argv
lists, so the only injection left is a value git would read as an option.

### Network operations

`remote` is a **remote name**, never a URL: it must name a remote already
configured in the repository (`-32094`, `reason: "unknown_remote"`, with
the configured names in `supported`). Because repository config lives
inside the workspace and the session can write it, a **local** remote — a
filesystem path or a `file://` URL — must also resolve inside the
workspace root (`reason: "remote_outside_workspace"`); network remotes
(`https`/`ssh`/`git`) cross no workspace boundary and are left alone. Accepting a URL would let a caller
point git at any path or scheme reachable from the sandbox, importing
objects from outside the workspace; and an `ext::` URL turns a remote into
a shell command. As a second line of defence `protocol.ext.allow=never` is
pinned on the command line, which outranks repository-local config. A
`refspec` without a `remote` is refused too (`reason: "missing_remote"`) —
git reads its first positional as the repository, so it would be silently
reinterpreted as one.

- `git_fetch {session_id, repo, remote?, refspec?, prune?}` →
  `{remote, updated_refs: [{ref, old_oid, new_oid, change}]}`. Remote-
  tracking refs are snapshotted before and after, so ref movement is
  reported structurally without reading a line of git's progress output.
- `git_pull {session_id, repo, remote?, refspec?, rebase?}` →
  `{ok, state, conflicted, head}`. A pull that stops on a conflict is a
  **state**, not an error: `ok: false` with the conflicted paths. Only a
  failure that leaves no conflict raises `-32092`.
- `git_push {session_id, repo, remote?, refspec?, force?,
  force_with_lease?, set_upstream?, confirmation_token?}` →
  `{ok, remote_url, updates, rejected}` from `git push --porcelain`. The
  meaning comes from the leading flag character (`status` is one of
  `created`, `fast_forward`, `forced_update`, `deleted`, `rejected`,
  `up_to_date`); the trailing human summary is passed through untouched as
  `summary_raw` and is never interpreted. A rejected push is a result, not
  an exception.

`git_operation_progress {session_id, operation_id, repo, operation, phase,
detail?}` is emitted to the calling connection for `fetch`/`pull`/`push`
with `phase` in `started`/`completed`/`failed`. The phases are the
server's own lifecycle, **not** a reading of git's progress output — which
is exactly why they are safe to expose. Like the `fs_*` events they are
per-connection and are not journaled: a reconnecting client refetches
`git_status`.

### Errors

| code   | kind                          | meaning                                                        |
|--------|-------------------------------|----------------------------------------------------------------|
| -32090 | `git_repository_not_found`    | no repository at `repo`, or its root is outside the workspace   |
| -32091 | `git_path_invalid`            | absolute/escaping/malformed `repo` or file path                 |
| -32092 | `git_operation_failed`        | git exited non-zero; carries `operation`, `exit_code`, `stderr` |
| -32093 | `git_confirmation_required`   | destructive action without a valid impact-bound token           |
| -32094 | `git_state_invalid`           | stale hunk id, empty/overlapping selection, unmerged index, nothing staged, bad mode/revision |
| -32095 | `git_capability_unavailable`  | the substrate has no shell, or git is not installed in it       |

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
