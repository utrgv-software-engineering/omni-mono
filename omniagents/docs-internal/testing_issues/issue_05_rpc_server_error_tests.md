---
title: "Add unit tests for RPC server error handling"
labels: ["testing", "rpc", "coverage", "priority: medium"]
---

## Description

The `server.py` module in `omniagents/rpc/server.py` has 66% coverage. While integration tests exist for basic RPC functionality, error handling and edge cases are untested.

## ⚠️ IMPORTANT: Test Intent, Not Current Behavior

**When implementing these tests, you MUST probe the intended behavior, not just verify what the code currently does.**

### When a Test Fails - Investigate Before Fixing

Do NOT immediately change assertions to make tests pass. Instead, ask:
1. **Is the implementation wrong?** (Bug in the code)
2. **Is the test wrong?** (Wrong assumptions)
3. **Is it both?**

### Focus Areas for This Module

For RPC server, pay attention to:
- **Error payloads** - Do they follow JSON-RPC 2.0 spec correctly?
- **Malformed message handling** - Does it return proper error codes (-32700 for parse error, etc.)?
- **Connection cleanup** - Are resources properly freed on disconnect?

**Example:**
```python
# ❌ BAD: Not verifying spec compliance
def test_error_handling():
    response = endpoint.handle_message("{bad json")
    assert "error" in response  # Too vague

# ✅ GOOD: Verifying JSON-RPC 2.0 spec compliance
async def test_malformed_json_returns_parse_error():
    """
    JSON-RPC 2.0 spec requires:
    - Error code -32700 for parse errors
    - Error message describing the problem
    - No result field when there's an error

    Spec: https://www.jsonrpc.org/specification

    If this fails:
    - Check error code matches spec
    - Verify error message is informative
    - Ensure response structure is valid JSON-RPC
    """
    endpoint = JsonRpcEndpoint()
    response = await endpoint.handle_message("{invalid json}")

    assert response["jsonrpc"] == "2.0"
    assert response["error"]["code"] == -32700
    assert "parse" in response["error"]["message"].lower()
    assert "result" not in response
```

See `docs/TESTING_GUIDE.md` section 2.4 for more details.

---

## Current State

- Current coverage: **66%** (90 lines, 27 missed)
- Integration tests cover basic RPC calls
- **No tests for error handling, malformed messages, concurrent connections**

## What Needs Testing

### Unit Tests Needed

1. **JsonRpcChannel error handling**:
   - Test `send_result()` with transport errors
   - Test `send_error()` payload construction
   - Test `async_call()` timeout handling

2. **JsonRpcEndpoint message handling**:
   - Test malformed JSON parsing
   - Test missing "jsonrpc" field
   - Test notification handling (no "id" field)
   - Test method not found errors

3. **Debug logging**:
   - Verify debug output when `DEBUG_SERVER=true`
   - Test log message formatting

### Integration Tests Needed

1. **Concurrent connections**:
   - Test multiple WebSocket connections
   - Verify connection isolation

2. **Connection cleanup**:
   - Test cleanup on disconnect
   - Verify resource cleanup

## Acceptance Criteria

- [ ] Unit test: JsonRpcChannel error handling (send errors)
- [ ] Unit test: JsonRpcEndpoint malformed message handling
- [ ] Unit test: Notification handling (no id)
- [ ] Unit test: Debug logging verification
- [ ] Integration test: Concurrent connections
- [ ] Integration test: Connection cleanup
- [ ] Coverage for `server.py` increases to at least **85%**

## Testing Approach

- Unit tests for error handling and message parsing
- Integration tests for connection lifecycle
- Use real WebSocket connections for integration tests

## Files to Modify

- `tests/unit/rpc/test_server.py` (new file)
- `tests/integration/test_server_rpc.py` (expand existing)

## Example Test Structure

```python
# tests/unit/rpc/test_server.py

async def test_jsonrpc_endpoint_handles_malformed_json():
    """Unit test: Malformed JSON error handling"""
    endpoint = JsonRpcEndpoint()

    # Malformed JSON
    result = await endpoint.handle_message("{invalid json")

    # Should return error response
    assert "error" in result
    assert result["error"]["code"] == -32700  # Parse error

async def test_jsonrpc_endpoint_handles_notification():
    """Unit test: Notification handling (no id field)"""
    endpoint = JsonRpcEndpoint()

    # Notification (no id)
    message = {
        "jsonrpc": "2.0",
        "method": "notify",
        "params": {}
    }

    result = await endpoint.handle_message(json.dumps(message))

    # Notifications don't return responses
    assert result is None

# tests/integration/test_server_rpc.py

async def test_concurrent_websocket_connections():
    """Integration test: Multiple concurrent connections"""
    # Start RPC server
    server = await start_rpc_server()

    # Connect multiple clients
    client1 = await connect_websocket(server.url)
    client2 = await connect_websocket(server.url)

    # Both should work independently
    response1 = await client1.call("method", {})
    response2 = await client2.call("method", {})

    assert response1 != response2  # Different sessions
```

## Estimated Effort

Small (2-3 hours)
