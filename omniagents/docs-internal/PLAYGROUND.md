# OmniAgents Studio Playground Plan

## Objective
Create a lightweight agent experimentation flow inside OmniAgents Studio that mirrors the AgentDeck UX while letting users configure model, tools, and instructions manually. Runs must be stored in the current project under workflow `playground` and streamed via the existing JSON-RPC protocol.

## Backend Status
- ✅ `omniagents/studio/tracing/serve()` loads the nearest `project.yml`, builds the entry agent spec, and stashes tool registries/defaults for reuse.
- ✅ `/api/playground/metadata` and `/api/playground/session` expose manifest-derived defaults, validate tool selections, and return a stable session id. Metadata now includes `playground_ws_url` so the client connects to the FastAPI origin, not the Vite dev host.
- ✅ `/api/playground/ws` spins up an `AgentService` on demand, proxies JSON-RPC traffic, and logs connection lifecycle/events. Runs inherit the base spec with `workflow_name="playground"`, so traces land alongside evaluations.
- ✅ Tool metadata (description, JSON schema, strictness) is extracted from `FunctionTool` objects for UI display.

## Frontend Status
- ✅ Navigation updated with a Playground section in the sidebar, home hero CTA, and dedicated header. Playground state lives alongside trace store without interfering with evaluations.
- ✅ Config panel lets users enter a model (required), review/edit instructions, and manage tools via pill list + modal selector (schema hidden for brevity). Reset falls back to manifest defaults.
- ✅ Chat surface streams live data using `usePlaygroundSession`, which mirrors the Bubbletea RPC client (handshake, session-id reuse, tool/status handling, usage totals). Transcript renders user/assistant bubbles, tool call chips, status messages, and exposes Send/Stop controls (with Ctrl/⌘+Enter shortcut).
- ➡️ Trace list already filters by workflow; playground runs appear once recorded. No additional UI needed.

## Remaining Work / Ideas
- Handle SafeAgent approvals in-playground (UI for `client_request` `ui.request_tool_approval` + response buttons).
- Toasts or inline errors when websocket disconnects unexpectedly; optional auto-retry.
- Persist chat history across page reloads (local storage keyed by session id) if desired.
- “Save run to traces” or “Export conversation” actions.
- Keyboard shortcuts / focus management polish in chat composer.

## References
- Manifest loader: `omniagents/core/project/runtime.py`
- Agent spec construction: `omniagents/core/config/loader.py`, `omniagents/core/agents/specs.py`
- RPC protocol details: `omniagents/rpc/protocol.md`
- Studio server: `omniagents/studio/server/api.py`
- Tracing utilities: `omniagents/studio/tracing/__init__.py`, `omniagents/studio/tracing/storage.py`
- Frontend playground: `omniagents/studio/server/web/src/features/playground/*`
- Existing UI patterns: `examples/agentdeck`

## Open Questions
- Should approvals be first-class in the playground, or deferred until basic tooling stabilises?
- How should we surface websocket auth when Studio runs with `--token`?
