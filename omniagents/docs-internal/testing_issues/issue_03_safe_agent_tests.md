---
title: "Add comprehensive tests for SafeAgent approval workflow"
labels: ["testing", "safe-agent", "coverage", "priority: high"]
---

## Description

The `SafeAgent` class in `omniagents/core/agents/safe.py` has only 49% test coverage despite being a critical security feature. Many important workflows like handoff wrapping, pattern matching, and approval metadata are completely untested.

## ⚠️ IMPORTANT: Test Intent, Not Current Behavior

**When implementing these tests, you MUST probe the intended behavior, not just verify what the code currently does.**

### When a Test Fails - Investigate Before Fixing

Do NOT immediately change assertions to make tests pass. Instead, ask:
1. **Is the implementation wrong?** (Bug in the code - especially critical for security!)
2. **Is the test wrong?** (Wrong assumptions)
3. **Is it both?**

### Focus Areas for This Module (SECURITY CRITICAL)

For SafeAgent, pay **extra attention** to security implications:
- **Approval bypass bugs** - Could a tool execute without approval when it shouldn't?
- **Pattern matching edge cases** - Could regex patterns fail to match dangerous tools?
- **Halt-on-rejection** - Does rejection actually stop execution?
- **Safe tool inheritance** - Do handoff agents properly inherit safe tool settings?

**Example:**
```python
# ❌ BAD: Accepting insecure behavior
def test_skip_approvals():
    agent = SafeAgent(..., skip_approvals=True)
    result = agent.run("Delete files")
    assert "deleted" in result  # Dangerous! Did it really skip approval?

# ✅ GOOD: Verifying security guarantee
async def test_skip_approvals_bypasses_all_approvals():
    """
    When skip_approvals=True, tools should execute without callbacks.
    This is used for trusted contexts only.

    Security consideration: If this fails, verify that:
    1. No approval callback is invoked
    2. Tools execute immediately
    3. This mode is documented as requiring trust

    If approval IS requested when skip_approvals=True, that's a bug!
    """
    callback_invoked = False
    def track_callback(name, args, meta):
        nonlocal callback_invoked
        callback_invoked = True
        return (True, False, None)

    agent = SafeAgent(
        tools=[dangerous_tool],
        skip_approvals=True,
        approval_callback=track_callback
    )
    await agent.run("Execute tool")

    assert not callback_invoked, "Callback should NOT be invoked with skip_approvals=True"
```

See `docs/TESTING_GUIDE.md` section 2.4 for more details.

---

## Current State

- Current coverage: **49%** (215 lines, 90 missed)
- Only 2 basic tests exist (rejection and always approve)
- No tests for handoffs, patterns, metadata, tool use behavior

## What Needs Testing

### Integration Tests Needed (Critical)

1. **Full approval workflow with RPC**:
   - Test tool approval request sent via RPC
   - Verify approval/rejection handling
   - Test halt on rejection behavior
   - Test "always approve" flag propagation

2. **Handoff agent wrapping** (`_wrap_handoffs()` method):
   - Test approval callback propagation to handoff agents
   - Verify safe tools inherited by handoff agents
   - Test nested handoffs

3. **Skip approvals mode**:
   - Test that `skip_approvals=True` bypasses approval
   - Verify all tools execute without prompting

4. **Multi-tool execution with halt**:
   - Test execution stops after rejection
   - Verify partial tool execution results

### Unit Tests Needed

1. **Safe tool pattern matching** (`is_tool_safe()` method):
   - Test exact name matching
   - Test regex pattern matching
   - Test global vs instance-level safe tools

2. **Tool use behavior modes** (`_custom_tool_use_behavior()` method):
   - Test `run_llm_again` mode
   - Test `stop_on_first_tool` mode
   - Test `StopAtTools` dict mode

3. **Approval metadata computation** (`_compute_approval_metadata()` method):
   - Test metadata generation for different tool types
   - Test preview rendering
   - Test with/without approval metadata decorators

4. **Safe tool management**:
   - `add_safe_tool()` - instance level
   - `add_safe_tool_pattern()` - regex validation
   - `remove_safe_tool()` - removal logic
   - `clear_safe_tools()` - clearing all
   - `set_global_safe_tools()` - global configuration
   - `add_global_safe_tool()` - global addition

## Acceptance Criteria

- [ ] Integration test: Full approval workflow via RPC with tool execution
- [ ] Integration test: Handoff agent wrapping and callback propagation
- [ ] Integration test: Skip approvals mode end-to-end
- [ ] Integration test: Multi-tool execution with halt on rejection
- [ ] Unit test: Pattern matching (exact + regex)
- [ ] Unit test: Tool use behavior modes (all 3 modes)
- [ ] Unit test: Approval metadata computation
- [ ] Unit test: Safe tool management (add/remove/clear)
- [ ] Coverage for `safe.py` increases to at least **80%**

## Testing Approach

- Test through RPC protocol for approval flows (integration)
- Use real SafeAgent instances with mocked OpenAI
- Test tool execution with real tool implementations
- Unit test complex logic (pattern matching, metadata)

## Files to Modify

- `tests/integration/core/agents/test_safe_agent_approval.py` (new file)
- `tests/unit/core/agents/test_safe_agent.py` (expand existing)

## Example Test Structure

```python
# tests/integration/core/agents/test_safe_agent_approval.py

async def test_safe_agent_requests_approval_via_rpc(mock_openai, mock_rpc_client):
    """Integration test: Full approval workflow through RPC"""
    # Create safe agent
    agent = SafeAgent(
        name="safe",
        tools=[bash_tool],
        instructions="Help with tasks",
        approval_callback=lambda name, args, meta: (True, False, None)
    )

    # Mock OpenAI to call bash tool
    mock_openai.tool_calls = [{"name": "bash", "arguments": {"command": "ls"}}]

    # Execute
    response = await agent.run("List files")

    # Verify approval was requested
    assert mock_rpc_client.requests[0]["function"] == "ui.request_tool_approval"
    assert mock_rpc_client.requests[0]["params"]["tool_name"] == "bash"

# tests/unit/core/agents/test_safe_agent.py

def test_is_tool_safe_with_patterns():
    """Unit test: Safe tool pattern matching"""
    agent = SafeAgent(
        name="safe",
        tools=[],
        instructions="",
        model="gpt"
    )

    # Add pattern for read-only tools
    agent.add_safe_tool_pattern(r"read_.*")

    # Test pattern matching
    read_tool = make_tool("read_file", "content")
    write_tool = make_tool("write_file", "ok")

    assert agent.is_tool_safe(read_tool) is True
    assert agent.is_tool_safe(write_tool) is False

def test_tool_use_behavior_stop_on_first_tool():
    """Unit test: Tool use behavior - stop on first tool"""
    agent = SafeAgent(
        name="safe",
        tools=[],
        instructions="",
        model="gpt",
        tool_use_behavior="stop_on_first_tool"
    )

    behavior = agent._custom_tool_use_behavior()
    assert behavior.run_llm_again is False
```

## Estimated Effort

Medium (4-6 hours)
