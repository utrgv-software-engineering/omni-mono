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
- `run_end`: Agent run completed

### stop_run(run_id: string)
Cancel an active agent run.

**Parameters:**
- `run_id` (string): ID of the run to cancel

**Returns:**
```json
true
```

### list_sessions()
Get list of all conversation sessions.

**Returns:**
```json
[{
  "id": "session-id",
  "created_at": "2024-01-01T00:00:00Z",
  "archived": false,
  "message_count": 10
}]
```

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
    "run_id": "run-123"
  }
}
```

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