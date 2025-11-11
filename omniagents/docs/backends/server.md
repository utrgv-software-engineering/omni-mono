# Server Backend

Expose agents via JSON-RPC 2.0 WebSocket API for remote client connections.

## Installation

```bash
pip install -e ".[server]"
```

## Usage

```bash
omniagents run -P agent.yml --mode server --host 0.0.0.0 --port 8000
```

## Features

- WebSocket endpoint at `/ws`
- JSON-RPC 2.0 protocol
- Stateful sessions
- Bidirectional communication
- Server-side function calls
- Client capability requests

## WebSocket Endpoint

Connect to: `ws://localhost:8000/ws`

## Protocol

The server implements a JSON-RPC 2.0 protocol with extensions. See [RPC Protocol](../api/rpc-protocol.md) for full details.

### Client Connection

```python
from omniagents.rpc import RemoteAgentProxy

proxy = RemoteAgentProxy(
    url="ws://localhost:8000/ws",
    session_id="my-session"  # Optional
)

# Send message
response = await proxy.send_message("Hello, agent!")

# Stream events
async for event in proxy.stream_events():
    print(event)
```

### Events

Server sends events:
- `message_output`: Agent messages
- `tool_called`: Tool execution started
- `tool_result`: Tool execution completed
- `token`: Token usage statistics
- `run_status`: Status updates (retries, etc.)
- `run_end`: Run completion

### Client Functions

Server can invoke client capabilities:

- `ui.request_tool_approval`: Request tool approval (Safe Agent mode)
- `ui.add_artifact`: Send rich content/artifacts

Clients must implement handlers for these functions.

## Configuration

### Host and Port

```bash
omniagents run -P agent.yml --mode server --host 127.0.0.1 --port 8000
```

### CORS (if needed)

For web clients, configure CORS in server settings.

## Use Cases

- Web applications
- Mobile apps
- Remote clients
- Integration with other systems
- Microservices architecture

## Example Server

Start server:

```bash
omniagents run -P examples/basic_yaml/agent.yml --mode server
```

Connect client:

```python
import asyncio
from omniagents.rpc import RemoteAgentProxy

async def main():
    proxy = RemoteAgentProxy("ws://localhost:8000/ws")

    # Send message
    response = await proxy.send_message("What time is it?")

    # Stream events
    async for event in proxy.stream_events():
        if event["type"] == "message_output":
            print(f"Agent: {event['content']}")
        elif event["type"] == "run_end":
            break

    await proxy.close()

asyncio.run(main())
```

See `examples/remote_agent/` for complete examples.

## Security Considerations

1. **Authentication**: Implement authentication for production
2. **Authorization**: Validate client permissions
3. **Rate Limiting**: Protect against abuse
4. **Input Validation**: Sanitize all inputs
5. **HTTPS**: Use WSS (WebSocket Secure) in production

## Dependencies

Requires:
- FastAPI 0.115.12+
- Uvicorn 0.34.1+

Installed with `[server]` extra.

## Next Steps

- [RPC Protocol](../api/rpc-protocol.md) - Protocol specification
- [Examples](../examples.md) - Remote agent examples
