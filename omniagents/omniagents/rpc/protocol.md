# Remote Agent Protocol

## Overview

The Remote Agent Protocol is a JSON-RPC 2.0 based protocol for communicating with AI agents over WebSocket connections. This protocol allows agents to be deployed on remote servers while providing the same interface as local agents.

## Protocol Features

- **Stateful Sessions**: Maintain conversation history across multiple interactions
- **Streaming Events**: Real-time updates during agent execution
- **Tool Approval**: Support for human-in-the-loop tool execution
- **Session Management**: List, archive, and manage conversation sessions

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
agent = RemoteAgent("ws://localhost:8000/ws", token="optional-auth-token")

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

### start_run(prompt: str, session_id?: str)
Start a new agent run with the given prompt.

**Parameters:**
- `prompt` (string): User message to send to the agent
- `session_id` (string, optional): ID of existing session to continue

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

### tool_approval_response(approval_id: string, approved: boolean, always_approve?: boolean)
Respond to a tool approval request (safe mode only).

**Parameters:**
- `approval_id` (string): ID from the approval request
- `approved` (boolean): Whether to approve the tool execution
- `always_approve` (boolean, optional): Auto-approve this tool for the session

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
    "call_id": "call-123"
  }
}
```

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
    "arguments": "{\"path\": \"/important/file.txt\"}"
  }
}
```

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

# Connect to server
agent = RemoteAgent("ws://server:8000/ws")

# Stream agent responses
async def chat():
    messages = [{"role": "user", "content": "Hello!"}]
    result = agent.run_streamed(messages)
    
    async for event in result.stream_events():
        if event.type == "message_output_created":
            print(f"Agent: {event.item.raw_item.content[0].text}")
        elif event.type == "tool_called":
            print(f"Calling tool: {event.item.name}")
```

This protocol enables seamless integration of remote AI agents while maintaining the same interface as local agents.