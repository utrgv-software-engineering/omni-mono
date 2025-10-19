# Tool Approval Routing Issue

## Summary

The AgentDeck example drives multiple chat sessions through one process and often a shared WebSocket, but OmniAgents' tool-approval pipeline (notably [`omniagents/core/agents/service.py`](../omniagents/core/agents/service.py), [`omniagents/core/agents/safe.py`](../omniagents/core/agents/safe.py), and [`omniagents/rpc/agents/local.py`](../omniagents/rpc/agents/local.py)) was designed around a single active session. Approval callbacks were stored in global state, and tool invocations didn't consistently carry stable run/session identifiers. When several chats are active, the most recently registered callback is reused, so approval requests surface in the wrong conversation.

## Attempts So Far

- Added explicit `run_id` / `session_id` tagging inside `_request_tool_approval`.
- Tracked the initiating `JsonRpcChannel` per run via `Session.run_channels`.
- Scoped `SafeAgent` approval callbacks per run and reset them after completion.
- Tightened AgentDeck's routing logic and added `tests/integration/test_tool_approval_routing.py` to verify server-side behavior.

All of these changes still rely on shared callback machinery, so race conditions remain: concurrent runs overwrite each other's callbacks, and approvals drift into a different chat.

## Root Cause

The approval system keeps conversation state in shared state and assumes only one session/run is active. AgentDeck's multi-column UI violates that assumption. As long as the callback and routing state are shared, overlapping runs will leak approvals regardless of tagging.

Even after removing global callback state, the current server still reuses a single SafeAgent instance across every session. Each `start_run` overwrites the shared agent's approval callback with the newest session/run context. When a previous session later invokes a tool, the callback still points at the newer session, so the approval card shows up in the wrong column. Without per-session (or per-run) agent instances, routing cannot stabilize.

## Architectural Changes Needed

1. **Eliminate shared approval state**: propagate per-session/run callbacks, and avoid caching agent instances across sessions.
2. **Use explicit identifiers end-to-end**: ensure every tool execution emits the session/run IDs provided by the runner; route approvals strictly by those IDs.
3. **Isolate transports** (or equivalent scoping): each chat should maintain its own channel/connection so callbacks can't cross streams.

Without these shifts, attempts to patch the existing design will continue to fail whenever multiple sessions share a process or transport.
