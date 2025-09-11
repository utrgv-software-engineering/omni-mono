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
The `tool_approval_request` event may include a `metadata` field containing structured information for rich approval dialogs (diffs, previews, etc.) instead of displaying raw JSON arguments.

### Tool Result Metadata  
The `tool_result` event may include a `metadata` field containing UI-specific data (charts, tables, visualizations) separate from the text output sent to the agent.

## Transport

The protocol uses JSON-RPC 2.0 over WebSocket for bidirectional communication:
- **Client → Server**: RPC calls for agent operations
- **Server → Client**: Notifications for streaming events

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

### start_run(prompt: str, session_id?: str, variables?: dict)
Start a new agent run with the given prompt and optional initial variables.

**Parameters:**
- `prompt` (string): User message to send to the agent
- `session_id` (string, optional): ID of existing session to continue
- `variables` (dict, optional): Initial variables for context construction. Only sent on first run (when session_id is not provided). The server uses these variables to construct the typed context and maintains state after initialization

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
- `tool_approval_request`: Requesting approval for tool execution (if safe mode enabled)
- `run_started`: Run has been scheduled and is now active (contains run_id and session_id)
- `run_end`: Agent run completed

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

**Note:** The `first_message` and `last_message` fields include the complete message content, allowing clients to implement their own truncation strategies based on UI requirements.

### archive_session(session_id: string)
Archive a conversation session.

**Parameters:**
- `session_id` (string): ID of session to archive

**Returns:**
```json
true
```

### tool_approval_response(approval_id: string, approved: boolean, always_approve?: boolean, error_message?: string)
Respond to a tool approval request (safe mode only).

**Parameters:**
- `approval_id` (string): ID from the approval request
- `approved` (boolean): Whether to approve the tool execution
- `always_approve` (boolean, optional): Auto-approve this tool for the session
- `error_message` (string, optional): Error message to provide when rejecting a tool

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

### tool_approval_request
```json
{
  "jsonrpc": "2.0",
  "method": "tool_approval_request",
  "params": {
    "approval_id": "approval-123",
    "tool": "delete_file",
    "arguments": "{\"path\": \"/important/file.txt\"}",
    "metadata": {
      "display_type": "file_delete",
      "summary": "Delete /important/file.txt",
      "preview": "This will permanently delete the file",
      "file_info": {
        "size": 1024,
        "modified": "2024-01-01T00:00:00Z"
      }
    }
  }
}
```

**Note:** The `metadata` field is optional and provides rich UI hints for tool approval dialogs. Common metadata fields include:
- `display_type`: UI rendering hint (e.g., "diff", "file_write", "table", "custom")
- `summary`: Brief description of the operation
- `preview`: Preview text for the UI
- `value`: The actual data to display (diffs, content, etc.)
- `metadata`: Additional context (file paths, operations, stats, etc.)
- `truncated`: Boolean indicating if content was truncated

### run_end
```json
{
  "jsonrpc": "2.0",
  "method": "run_end",
  "params": {
    "run_id": "run-123",
    "end_reason": "completed",
    "model": "gpt-4o-mini",
    "context_window": 128000,
    "max_output_tokens": 2048,
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
    "context_window": 128000,
    "max_output_tokens": 2048,
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
- If present, "context_window" indicates the maximum input tokens the model can accept for that turn. Clients should consider reserving space for "max_output_tokens" when estimating whether the next call will fit.
- The final aggregate usage and last-known model metadata are also included in the run_end event.

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

- Class A (pre-run errors): Errors that occur before a run can start (e.g., bad credentials, invalid spec). These are returned as JSON-RPC errors on the `start_run` call and there will be no `run_end` event for that attempt. Clients should surface the error and keep the UI interactive.
- Class B (in-run errors): Errors that occur during execution (timeouts, rate limits, network flakiness). The server MAY internally retry and will emit `run_status` notifications while doing so. If the run ultimately fails, the server emits `run_end` with `end_reason: "error"` and an `error` object `{ "message", "type" }`. Clients should filter by `run_id` and show a non-blocking status.

Run lifecycle notifications:

- `run_started`: Emitted immediately after a run is scheduled; includes `{ run_id, session_id }`. This allows clients to correlate subsequent `run_status`, `token`, and `run_end` messages even if they arrive before the JSON-RPC response to `start_run` is processed.

## Implementation Guidelines

### For Servers
1. Implement all RPC methods listed above
2. Send appropriate event notifications during runs
3. Handle WebSocket connection lifecycle properly
4. Support session persistence across connections

### For Clients
1. Handle all event notification types
2. Implement proper reconnection logic
3. Manage session state appropriately
4. Provide user-friendly interfaces for tool approvals

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
