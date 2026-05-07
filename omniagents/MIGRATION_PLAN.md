# OmniAgents → openai-agents 0.16.0 Migration Plan

## Summary

Upgrade the pinned `openai-agents` dependency from `0.4.2` to `0.16.0` (12 minor releases), delete OmniAgents code that the SDK now provides natively, and update the RPC protocol where the new SDK shapes are cleaner. All work lands as a single PR on branch `upgrade-openai-agents-0.16`. After this work, OmniAgents drops ~1,500 lines of duplicate logic, exposes tool-level guardrails, and tracks the latest SDK going forward.

## Decisions Locked

| Decision | Choice | Rationale |
|---|---|---|
| Scope | Strict — delete duplications only (steps 1–5 from audit) | Keep PR reviewable; defer new-feature adoption to follow-ups |
| Realtime/voice | First-class fix, gates the migration | Voice is shipped in production; can't regress it |
| YAML config compat | Translate old keys + emit deprecation warnings | Existing user configs keep working; cleaner schema is documented for v1.0 |
| RPC wire format | Break clients; ship new shape directly | Web/Ink/Studio frontends are first-party; wire-compat shim isn't worth maintaining |

---

## Step 0 — Branch + baseline (already done)

- Branch `upgrade-openai-agents-0.16` created from `main`.
- This file is the only artifact from this step.

---

## Step 1 — Foundation: dependency bump, import fixes, defaults

**Goal:** Get the codebase compiling and the test suite running against `openai-agents 0.16.0` with no behavioral changes intended.

**Changes:**

- `pyproject.toml` and `requirements.txt`:
  - Bump `openai-agents[realtime]==0.4.2` → `openai-agents[realtime]>=0.16.0,<0.17`
  - Bump `voice` extra similarly
  - Add `openai-agents[litellm]` to extras since `litellm_provider` is now behind it
  - SDK pulls `mcp>=1.19`, `openai>=2.26`, `pydantic>=2.12.2`, `numpy>=2.2` automatically; verify no conflicts with existing pins (`pydantic==2.12.3` is fine)
- `omniagents/core/agents/builder.py:183`:
  - Replace `from agents.models._openai_shared import get_default_openai_client` with `OpenAIProvider().get_client()` or equivalent public API
- `omniagents/core/agents/service.py` and any other call sites: audit imports against the SDK's current `agents/__init__.py`; the audit identified all currently-used imports as still resolvable, but verify
- `omniagents/core/config/loader.py`: enforce explicit `model:` defaulting to `"gpt-5.2"` so that 0.16.0's silent default (`gpt-5.4-mini`) doesn't kick in
- Add explicit `reasoning.effort` default in `core/config/loader.py` and `core/session/manager.py:87-88` to avoid the 0.7.0 default flip to `"none"` causing silent quality regressions on `gpt-5.x` models
- Wherever code checks `result.final_output == ""` to detect refusals, switch to `try/except ModelRefusalError` (likely in `service.py` and possibly `core/eval/`)
- If any tests assert nested handoff history shape, set `RunConfig(nest_handoff_history=True)` explicitly to preserve old behavior

**Validation checkpoint:**
- `pytest -q tests/unit` passes
- `pytest -q tests/integration` runs to completion; record which tests fail and categorize (expected breakage from later steps vs. unexpected)
- `python -m omniagents.cli run -c examples/basic_yaml/agent.yml --mode server` starts and accepts a single message
- `make coverage-python` runs without import errors

**Rollback:** Single commit; `git revert` if the dep bump alone proves unstable.

**PR scope:** PR #1. ~50–150 lines changed across config, no behavioral changes intended.

---

## Step 2 — Realtime / Voice fix (gating)

**Goal:** Validate that realtime and voice still function on 0.16.0. This step is the project gate — if realtime can't be made to work, the whole migration must be re-scoped.

**Risk assessment:**
- The SDK crossed two breaking realtime boundaries between 0.4.2 and 0.16.0:
  - 0.3.0: migration to `gpt-realtime` GA model interface
  - 0.13.0: realtime module restructure (new `_default_tracker.py`, `events.py`, `handoffs.py`, `model_events.py`, `audio_formats.py`); default model bumped to `gpt-realtime-1.5`
- Files at risk:
  - `omniagents/rpc/realtime_session.py` (857 lines) — imports `RealtimeAgent`, `RealtimeRunner` directly
  - `omniagents/rpc/voice_pipeline_service.py` (535 lines) — imports `StreamedAudioInput`, `VoicePipeline`, `VoicePipelineConfig`, `agents.voice.imports.np`
  - `omniagents/voice/openai_compatible.py`
  - `tests/integration/test_realtime_integration.py`
  - `tests/integration/test_realtime_sdk_integration.py`

**Changes:**
- Cross-reference each import against the SDK's new `agents.realtime.*` and `agents.voice.*` surfaces; rewrite call sites that have moved
- Update event handling for any new realtime event types (`handoff_*`, audio format events)
- If `agents.voice.imports.np` was removed, import `numpy` directly
- Update `voice_pipeline_service.py:25` event passthrough set to include any new event names that should reach the client

**Validation checkpoint:**
- `pytest -q tests/integration/test_realtime_integration.py tests/integration/test_realtime_sdk_integration.py` passes
- `python -m omniagents.cli run -c examples/voice_pipeline_openai_compatible/agent.yml --mode web` boots; manual smoke test of voice round-trip
- `python -m omniagents.cli run -c examples/safe_agent_yaml/agent.yml --mode web` with realtime voice controls: voice + tool approval (still on legacy SafeAgent here) work end-to-end

**Failure mode:** No fallback. The migration target is 0.16.0; if realtime is fundamentally broken there, the work stops and the blocker is escalated to the user with a concrete reproduction. There is no downgrade path.

**Rollback:** This step is part of a single PR; revert as a whole if needed.

---

## Step 3 — Tool decorator cleanup + Tool guardrails discovery

**Goal:** Remove the now-redundant `_wrap_sync_function` from OmniAgents' tool decorator and extend tool/guardrail discovery to surface SDK 0.16.0's new tool-level guardrails.

**Changes:**

- `omniagents/core/tools/discovery.py`:
  - Remove `_wrap_sync_function` (the SDK now handles sync→async via `asyncio.to_thread` since 0.8.0)
  - Keep `_is_omniagents_core_tool`, `_client_status_text`, `_original_func` markers
  - Keep `RichToolOutput` (UI sidecar; **not** equivalent to SDK's `ToolOutputText/Image/FileContent`)
  - Keep `@approval_metadata()` registry (no SDK equivalent for rich approval-dialog metadata)
- `omniagents/core/guardrails/discovery.py`:
  - Add discovery for `ToolInputGuardrail` and `ToolOutputGuardrail` instances (SDK exposes via `agents.tool_guardrails`)
  - Re-export `tool_input_guardrail`, `tool_output_guardrail`, `ToolGuardrailFunctionOutput`, `RejectContentBehavior`, `RaiseExceptionBehavior` from `omniagents.core`
- `omniagents/core/config/loader.py`:
  - Extend YAML schema to allow `tool_input_guardrails` and `tool_output_guardrails` per-tool or globally
  - Wire discovered tool guardrails into `FunctionTool.tool_input_guardrails` / `tool_output_guardrails`
- `omniagents/core/__init__.py`: re-export new guardrail symbols

**Validation checkpoint:**
- All existing tests still pass (no behavior change for existing tools)
- New unit test: a tool with a `ToolInputGuardrail` returning `RejectContentBehavior` produces a model-visible rejection without raising
- `examples/guardrails_yaml/` continues to work; consider adding a `tool_guardrails_yaml` example demonstrating the new feature

**Rollback:** Two independent commits within the single PR — either is safely revertable in isolation if needed pre-merge.

---

## Step 4 — Session storage delegation

**Goal:** Replace OmniAgents' bespoke SQLite history layer with delegation to SDK `SQLiteSession`, while preserving multi-tenant scoping, the RPC `SessionManager`, and the Studio query layer.

**Changes:**

- `omniagents/core/session/history_db.py` (805 lines): rewrite as a thin shim around `agents.memory.SQLiteSession`:
  - `load_history(session_id)` → `SQLiteSession(...).get_items(limit=None)`
  - `append_message(session_id, item)` → `SQLiteSession(...).add_items([item])`
  - `register_session(project_slug, agent_slug, session_id)` stays — multi-tenant scoping is OmniAgents-specific
  - `set_archived`, `list_sessions`, search/count/stats from `query.py` stay — these are query-layer concerns the SDK doesn't address
  - DB path resolution stays (single `sessions.db` at repo root unless overridden)
- `omniagents/core/session/manager.py` (373 lines): unchanged surface, but:
  - `SessionBase.add_to_history` and `load_history` now delegate to the new shim
  - `channels`, `pending_events`, `run_channels`, `current_task`, `cancel_requested`, `safe_tool_overrides`, `active_run_id`, `pending_client_requests`, `active_model`, `reasoning_effort` — all stay in-memory in `SessionManager` (these are RPC connection state, not persistence)
- `omniagents/core/agents/service.py`: where it calls `Runner.run(...)`, pass `session=SQLiteSession(...)` so the SDK manages history natively. Remove manual history rolling.
- `omniagents/core/session/audio_storage.py`: keep as-is (no SDK equivalent)
- `omniagents/core/session/query.py`: keep as-is (Studio depends on this)

**Schema migration:** SDK's `SQLiteSession` uses tables `sessions` and `messages` (configurable via `sessions_table` / `messages_table` kwargs). OmniAgents' current schema differs — write a one-time migration that renames/copies into SDK-compatible tables on first 0.16.0 boot. Migration script lives at `omniagents/core/session/_migrations/v0_16_session_schema.py`.

**Validation checkpoint:**
- `pytest -q tests/integration/test_session_management.py` passes
- A pre-migration `sessions.db` from a 0.4.2 build is migrated cleanly to the new schema; conversation resume works
- Studio session list / search / archive operations all still work
- Multi-tenant project/agent scoping respected

**Rollback:** The schema migration is **idempotent** and leaves the old tables intact (renamed with `_legacy` suffix) so a downgrade after merge is still possible.

---

## Step 5 — SafeAgent → `needs_approval` + new RPC wire format

**Goal:** Delete `omniagents/core/agents/safe.py` (534 lines) and rewire tool approval through the SDK's native interruption model. Break the RPC wire format and update first-party clients in the same PR.

**Why this is last:** It's the highest-blast-radius change and depends on a stable foundation (steps 1–2) and a cleanly-delegated session layer (step 4) since approval state interacts with run resumption and history.

### 5a. Server-side migration

- Delete `omniagents/core/agents/safe.py` entirely
- `omniagents/core/agents/builder.py`:
  - Remove `SafeAgent` import and construction (lines 222–229, 449–485)
  - When `spec.use_safe_agent` is true: for each tool, set `tool.needs_approval = make_needs_approval_callable(tool, spec.safe_agent_options)` where the callable consults the runtime `SessionManager.safe_tool_overrides` allow-list
  - The callable receives `(RunContextWrapper, args_dict, call_id)` and returns `bool`
  - Handoffs: SDK's first-class handoffs propagate `needs_approval` naturally; the recursive `_wrap_handoffs` in `safe.py:183-220` is no longer needed
- `omniagents/core/agents/service.py`:
  - Remove all `isinstance(agent, SafeAgent)` checks (lines 669, 690, 811)
  - `Runner.run(...)` now returns `RunResult` with `interruptions: list[ToolApprovalItem]` when approval is needed
  - On interruption: serialize `RunState` via `state.to_json()`, persist alongside session, and emit a wire event (see 5b)
  - On client approval response: deserialize `RunState`, call `state.approve(item, always_approve=...)` or `state.reject(item)`, then `Runner.run(state=state, ...)` to resume
- `omniagents/core/config/loader.py`:
  - Keep `use_safe_agent`, `safe_tool_names`, `safe_tool_patterns` keys working
  - Emit `DeprecationWarning` and a structured logger warning when these are used, pointing to the future per-tool `needs_approval` schema
  - Document the future schema in `omniagents/core/config/README.md` (or equivalent) without yet implementing it

### 5b. RPC wire format

- `omniagents/rpc/protocol.md`: replace section "Tool approval (`ui.request_tool_approval`)" with a new event-based flow:
  - Server emits `tool_approval_requested` event with payload `{call_id, tool_name, arguments, metadata, run_state_token}` where `run_state_token` is an opaque server-side reference to the serialized `RunState`
  - Client responds via a new `tool_approval_response` RPC method with payload `{run_state_token, decision: "approve" | "reject", always_approve?: bool, rejection_message?: str}`
  - Remove `client_request` / `client_response` handling for `ui.request_tool_approval` (the generic `client_request` mechanism remains for other purposes — e.g., `ui.add_artifact`)
- `omniagents/rpc/agents/_events.py`: extend `map_notification_to_event` to handle `tool_approval_requested` and the new SDK event names: `handoff_requested`, `handoff_occured`, `reasoning_item_created`, `mcp_approval_requested`, `mcp_approval_response`, `mcp_list_tools`, `tool_search_called`, `tool_search_output_created`
- `omniagents/rpc/agents/workers.py:61`: route `tool_approval_response` to the SafeApproval queue replacement

### 5c. First-party client updates (lockstep)

- `omniagents/backends/web/ui/src/App.tsx:131`: drop `ui.request_tool_approval` from `clientFunctions(...)` registration; subscribe to the `tool_approval_requested` event stream instead
- `omniagents/backends/web/ui/src/components/VoiceModal.tsx:466-482`: replace `client_request`/`client_request_resolved` branch for `ui.request_tool_approval` with the new event handler
- `omniagents/backends/ink/tui/src/types/protocol.ts:164-174`: remove the `ui.request_tool_approval` `client_request` shape; add `ToolApprovalRequestedEvent` and `ToolApprovalResponseRequest` types
- `omniagents/backends/ink/tui/src/rpc/client.ts:241-407`: replace approval response handling
- `omniagents/backends/ink/tui/src/hooks/useMessagesNew.ts:445-461`: drop `registerClientFunction("ui.request_tool_approval", ...)`; handle `tool_approval_requested` events instead
- Studio: search for `ui.request_tool_approval` in `omniagents/studio/server/web/` and update (if any references)

### 5d. Test updates

- `tests/integration/test_tool_approval.py` and `tests/integration/test_tool_approval_routing.py`: rewrite for new wire format
- `tests/fixtures/agent_specs.py`: any fixture using `SafeAgent` directly → use plain `Agent` with `needs_approval` on tools
- New test: serialize/deserialize `RunState` mid-run, resume in a different process, verify identical output

### 5e. Examples

- `examples/safe_agent_yaml/agent.yml` and `examples/safe_agent_python/`: keep working via the YAML translation layer (5a), but add a comment noting deprecation

**Validation checkpoint:**
- All `tests/integration/test_tool_approval*.py` pass
- Manual: `python -m omniagents.cli run -c examples/safe_agent_yaml/agent.yml --mode web`, attempt a tool, see approval dialog, approve — tool runs; reject — tool refuses with message; "always approve" persists across calls in the same session
- Manual: same flow in Ink TUI mode
- Manual: same flow in voice mode (voice approvals)
- Resume scenario: kill the server mid-approval, restart, deserialize state, resume from interruption

**Rollback:** Part of the single PR. If broken in production after merge, revert the merge commit. The deprecation warnings and YAML translation layer mean user configs don't need to change to roll back.

---

## PR Strategy

A single PR off `upgrade-openai-agents-0.16` containing all five steps as logically-grouped commits, in the order documented above:

1. Foundation (deps + import fixes + pinned defaults)
2. Realtime/voice fix
3. Tool decorator cleanup + tool guardrail discovery
4. Session storage delegation
5. SafeAgent removal + RPC wire format break + frontend lockstep

The branch merges to `main` as a single squash. Each commit has a focused message so the reviewer can read the history if they prefer commit-by-commit review.

Approximate total: 2,500–4,200 lines changed (majority deletions).

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Realtime can't be made to work on 0.16.0 | Medium | Project-blocking | No fallback; if step 2 fails, escalate to user with concrete reproduction. Migration stops. |
| Schema migration corrupts a user's `sessions.db` | Low | High | Idempotent migration; keep `_legacy` tables for one release; tested on snapshot data |
| RunState serialization is incompatible across SDK patch versions | Medium | Medium | Tighten SDK pin to `>=0.16.0,<0.17`; document upgrade caveat |
| Frontend lockstep breaks if Studio/web/Ink not updated together | Medium | Medium | All three updated in PR 5; deployment is atomic |
| SafeAgent's "always approve" semantics don't map cleanly to `needs_approval=callable` checking a live allow-list | Low | Medium | The callable closure references a mutable `safe_tool_overrides` set on `SessionManager`; mutation is visible on next tool call; covered by test |
| `ModelRefusalError` raise breaks code paths that expected empty `final_output` | Medium | Low | Step 1 audits this; `core/eval/` and `core/agents/service.py` are the likely sites |
| Default model silently changing to `gpt-5.4-mini` regresses behavior | High if missed | Medium | Step 1 forces explicit defaults |

---

## Test Plan

Per-step validation is in each step. Cumulative end-state acceptance criteria for the full migration:

- `pytest -q` passes (all 168 test files)
- `make coverage-all` runs to completion
- All examples in `examples/` boot via `python -m omniagents.cli run -c <example> --mode <mode>` without errors
- Manual smoke tests across web, Ink, server, and voice modes for: basic chat, tool call, tool approval (approve + reject + always-approve), handoff, voice round-trip, session resume
- A new test case: persist `RunState` during interruption, resume in fresh process, verify equivalent behavior
- A new test case: a tool with `ToolInputGuardrail` rejects with `RejectContentBehavior` and the model sees the rejection content
- No deprecation warnings emitted from `agents.*` imports under default pytest config (warnings-as-errors in CI for the `agents` namespace optional)

---

## Assumptions (defaults chosen without further user input)

1. **Concrete pinned model**: foundation step pins `model: gpt-5.2` as the project-wide default unless a YAML overrides.
2. **Reasoning effort default**: `reasoning.effort: "medium"` for `gpt-5.x` models, falling back to SDK defaults otherwise. The 0.7.0-introduced `"none"` default is explicitly avoided.
3. **Migration script for sessions DB**: runs on first server boot under 0.16.0; logs both old-row and new-row counts; refuses to run if the schema is already migrated (idempotent).
4. **Approval state lifecycle**: matches existing OmniAgents behavior — held in-memory on `SessionManager` (`pending_client_requests`) and on the service (`_pending_client_requests` asyncio Futures). Lost on server restart, same as today. The `run_state_token` is a server-side handle to serialized `RunState` cached in-memory keyed by token; survives reconnect within the same server process, not server restart. No persistence layer added.
5. **YAML deprecation horizon**: `use_safe_agent` / `safe_tool_names` / `safe_tool_patterns` continue to work for 1 minor release after this migration ships; removal in a subsequent major bump (target: omniagents 1.0).
6. **`max_turns=None` adoption**: not opted-in by default; existing turn-limit behavior preserved unless YAML opts in.
7. **`mcp` being a hard dep on 0.16.0**: acceptable; OmniAgents already imports `agents.mcp` and the additional `mcp>=1.19` install footprint is small.
8. **`numpy>=2.2` requirement**: acceptable; existing OmniAgents code does not pin numpy.

---

## Out of Scope (deferred to follow-up PRs)

These are net-new features the audit identified as worth adopting, but are explicitly excluded from this migration to keep scope tight:

- Hosted tools: `WebSearchTool`, `FileSearchTool`, `CodeInterpreterTool`, `ComputerTool`, `ApplyPatchTool`, `LocalShellTool`, `ImageGenerationTool`, `HostedMCPTool`
- Sandbox agents (0.14.0)
- `OpenAIResponsesCompactionSession` / context compaction
- `MultiProvider` and `litellm`/`any-llm` model routing extensions
- Run error handlers (`RunErrorHandler`, `model_refusal` handler)
- `responses_websocket_session()` for WebSocket Responses transport
- New session backends: `SQLAlchemySession`, `RedisSession`, `MongoDBSession`, `EncryptSession`, `DaprSession`
- MCP resources: `list_resources()`, `read_resource()`
- `RetryPolicy` exposure in YAML (the SDK's retry runs internally even if not configured)
- `ApplyPatchEditor` / `apply_diff` primitives
- `max_turns=None` toggle in YAML schema

A follow-up planning doc should sequence these by user value once the migration lands.
