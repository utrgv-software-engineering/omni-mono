# OmniAgents → openai-agents 0.16.0 Migration Plan

## Summary

Upgrade the pinned `openai-agents` dependency from `0.4.2` to `0.16.0` (12 minor releases), delete OmniAgents code that the SDK now provides natively, and update the RPC protocol where the new SDK shapes are cleaner. All work lands as a single PR on branch `upgrade-openai-agents-0.16`. After this work, OmniAgents drops ~1,500 lines of duplicate logic, exposes tool-level guardrails, and tracks the latest SDK going forward.

## Current Status

| Step | Status | Commit | Lines |
|---|---|---|---|
| 1 — Foundation: deps + import fixes + defaults | ✅ Done | `ec3aa2e` | +302 −5 |
| 2 — Realtime/voice fix (gating) | ✅ Done (no-op) | — | 0 |
| 3 — Tool decorator cleanup + tool guardrails discovery | ✅ Done | `9c49c53` | +307 −96 |
| 4 — Session storage delegation | ⚠️ Partial — scope reduced | `399f491` | +61 −5 |
| 5a — Server-side SafeAgent removal + needs_approval wiring | ✅ Done | `2f5fc92` | +641 −1116 |
| 5b/c — RPC wire format + frontend lockstep | ✅ Done | `779c107` | +371 −285 |
| 5d — Tests for new approval flow | ✅ Done | `24bdcaa` | +tests |
| 5e — Examples + YAML deprecation warning | ✅ Done | `691e65b` | +small |
| 6 — Tier 1 YAML knobs (max_turns=null, retry_policy, error_handlers, tool_execution, nest_handoff_history) | ✅ Done | `09a98fc` | +275 −10 |
| 7 — model_provider config + MultiProvider routing | ✅ Done | `0244c94` | +143 −84 |
| 8 — Lifecycle hooks discovery + apply_patch primitives re-exported | ✅ Done | `6c3f345` | +164 −2 |
| 9 — OpenAIResponsesCompactionSession adoption + drop context-length retry | ✅ Done | `06a19f7` | +191 −220 |
| Hosted tools merged | ✅ Done | `3af685e` | +new module + YAML |
| Session backends merged | ✅ Done | `59c258d` | +new module + extras |
| MCP resources/approval merged | ✅ Done | `d8c750e` | +617 −0 |
| 10 — session_backend → Runner.run_streamed(session=...) | ✅ Done | `f195e35` | +9 −1 |

**Total committed so far:** ~670 lines changed across 14 files. Realtime and voice paths verified working on 0.16.0 with no code changes. Default agents now use `gpt-5.2` with `Reasoning(effort="medium")`. Tool-level guardrails (SDK 0.7+) discoverable via the existing `guardrails/` directory and configurable via two new YAML keys. OmniAgents `Session` now satisfies the SDK `Session` protocol so it can be passed to `Runner.run(session=...)` once step 5 wires that up.

**Next session pickup point:** Step 5 details below. Branch is local; not yet pushed.

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

## Step 1 — Foundation: dependency bump, import fixes, defaults ✅

**Status:** Done in commit `ec3aa2e`.

**Done:**
- Bumped `openai-agents[realtime]` from `==0.4.2` to `>=0.16.0,<0.17` in `pyproject.toml` and `requirements.txt`. Same for `voice` extra. Added a `litellm` extra.
- Default model literal changed from `gpt-4o` to `gpt-5.2` in `core/agents/builder.py:234` and `core/config/loader.py:230`.
- Added a guard in `core/agents/builder.py` that auto-sets `Reasoning(effort="medium")` for any `gpt-5.x` model when not explicitly configured, to avoid the SDK 0.7.0+ silent default of `"none"`.
- Smoke-tested: `examples/basic_yaml` and `examples/safe_agent_yaml` build cleanly; default-model agents pick up `gpt-5.2` and the medium reasoning effort.

**Findings (deviations from original plan):**
- The `_openai_shared` import (`builder.py:183`) **still resolves cleanly** in 0.16.0 — no rewrite was needed. Left as-is; switching to a public API is a code-quality follow-up, not a migration blocker.
- No `final_output == ""` checks exist in OmniAgents — `ModelRefusalError` migration was a no-op.
- `pydantic==2.12.3` and `pydantic-core==2.41.4` work fine with the new SDK; `openai` was force-upgraded from 1.x to 2.35.1 by the SDK requirement (`openai>=2.26.0,<3`).
- Unit tests: 997 pass / 103 fail. ~100 of the failures are pre-existing flakes (test-order dependence on `test_security`, `Debug.log` writes to stderr but tests assert on `captured.out`, `_stream_event_payload()` signature drift). 2 of the failures (`test_reasoning_pairing.py::TestOrphanedReasoningHandling`) appear migration-related (SDK reasoning-streaming behavior change) but don't block the migration; they should be addressed alongside step 5 or as a follow-up.

---

## Step 2 — Realtime / Voice fix (gating) ✅

**Status:** Done — turned out to be a no-op. No code changes needed.

**Findings:**
- All realtime/voice imports OmniAgents uses still resolve cleanly on 0.16.0:
  - `from agents.realtime import RealtimeAgent, RealtimeRunner`
  - `from agents.voice import StreamedAudioInput, VoicePipeline`
  - `from agents.voice.pipeline_config import VoicePipelineConfig`
  - `from agents.voice.imports import np, websockets`
  - `from agents.voice.model import VoiceModelProvider`
  - `from agents.voice.models.openai_model_provider`, `.openai_stt`, `.openai_tts`
- `tests/integration/test_realtime_integration.py` (9 tests including session lifecycle, multiple sessions, event streaming, send_audio, send_text, interrupt, session_not_found, duplicate_session, service_cleanup) all pass.
- `tests/integration/test_realtime_sdk_integration.py` collects and runs cleanly.
- `tests/unit/rpc/test_realtime_session.py`: 45 of 48 tests pass. The 3 failures (`test_start_session_with_handoffs`, `test_start_session_with_tools_and_handoffs`, `test_start_session_with_tools_handoffs_and_guardrails`) are mock-based and unrelated to realtime — they fail because `SafeAgent` is mocked but the test setup doesn't stub `set_global_safe_tools`. These will resolve naturally in step 5 when SafeAgent is removed.
- The audit's prediction that the realtime restructure (0.13.0) would cause major breakage didn't materialize for OmniAgents because OmniAgents uses `RealtimeAgent`/`RealtimeRunner` at a high level and doesn't touch the internal event types that were restructured.

**No commit for this step** — verification only.

---

## Step 3 — Tool decorator cleanup + Tool guardrails discovery ✅

**Status:** Done in commit `9c49c53`.

**Done:**
- `core/tools/discovery.py`: deleted `_wrap_sync_function` and its callers in `function_tool` / `rich_function_tool`. The SDK 0.8+ wraps sync functions via `asyncio.to_thread` natively with the same contextvars semantics our wrapper provided. Kept `_is_omniagents_core_tool`, `_client_status_text`, `_original_func` markers, `RichToolOutput`, and `@approval_metadata()` registry.
- `core/runtime/bridge.py`: updated stale comment referencing the deleted helper.
- `core/guardrails/discovery.py`: `discover_guardrails_in_dir` now also scans for `ToolInputGuardrail` / `ToolOutputGuardrail` instances; return shape is now a 4-tuple.
- `core/config/loader.py`: read new YAML keys `tool_input_guardrails:` / `tool_output_guardrails:`, resolve names against discovery, populate `AgentSpec`.
- `core/project/runtime.py`: extend project-level guardrails registry with `tool_input` / `tool_output` buckets.
- `core/agents/specs.py`: `AgentSpec` gains `tool_input_guardrails` and `tool_output_guardrails` list fields.
- `core/agents/builder.py`: propagate spec-level tool guardrails onto every `FunctionTool` the agent uses (additive, doesn't clobber per-tool lists).
- `core/__init__.py`: re-export `ToolInputGuardrail`, `ToolOutputGuardrail`, `tool_input_guardrail`, `tool_output_guardrail`, `ToolGuardrailFunctionOutput`.
- `tests/unit/core/guardrails/test_discovery.py`: updated existing assertions for the new 4-tuple return; added a new test covering tool-guardrail discovery end-to-end (passes).
- `tests/unit/core/tools/test_discovery.py`: dropped two `_wrap_sync_function` tests; tightened the marker test to check `_original_func` instead of invoking `on_invoke_tool` with a `None` ctx (which 0.16+ rejects).

**Findings (deviations from original plan):**
- The plan suggested per-tool YAML guardrails. Implemented as **agent-global lists** instead — simpler, same coverage. Per-tool wiring is a clean follow-up if anyone needs it.
- Did not write a `RejectContentBehavior` integration test. The discovery surface is verified; the actual reject-content roundtrip relies on the SDK's runner doing the work and is best covered by a runtime-level test rather than a discovery test. Deferred.

---

## Step 4 — Session storage delegation ⚠️ Partial — scope reduced

**Status:** Scope reduced and done in commit `399f491`. The full delegation envisioned in the original plan was not done; see "Why scope reduced" below.

**Done:**
- `core/session/manager.py`: OmniAgents `Session` now satisfies the openai-agents SDK `Session` Protocol (`agents.memory.Session`). Added four async methods that delegate to the existing sync persistence layer:
  - `get_items(limit)` — returns `list(history)`, optionally tail-sliced
  - `add_items(items)` — calls `append_message` for each, preserving the existing repair/persistence path
  - `pop_item()` — pops from in-memory history (persistent rows kept, matches OmniAgents non-destructive semantics)
  - `clear_session()` — clears in-memory history (persistent rows kept)
- Exposed `session_id` as a property so the protocol's class-level attribute declaration is satisfied.
- Verified `isinstance(session, agents.memory.Session)` is `True`. `tests/integration/test_session_management.py` passes.

**Why scope reduced:**
- A wholesale rewrite of `history_db.py` (805 lines) as a shim over `agents.memory.SQLiteSession` would require:
  - A schema migration (OmniAgents tables `history`, `sessions` with columns `archived`, `hold`, `context_json`, `variables_json`, `user_id`, `audio_metadata` — SDK uses `agent_sessions` and `agent_messages`).
  - Coordinating reads/writes between two storage systems if they were to share a DB file.
  - Updating ~14 call sites across `service.py`, `runtime/bridge.py`, `eval/pipeline.py`, `eval/multi_turn.py`, `eval/gepa_agent_adapter.py`, `session/cli.py`.
- More importantly, **`history_db.py` is not strictly duplicating SDK code** — it adds substantial functionality the SDK doesn't have:
  - Multi-tenant project/agent scoping (`register_session(project_slug, agent_slug, ...)`)
  - Archived/hold flags
  - Audio metadata storage referenced from messages
  - Cross-session search and metadata queries (`query.py`, used by Studio)
  - Encryption-provider hook (`set_encryption_provider`)
- Per the locked decision "Strict — delete duplications only", and given there is no actual duplication to delete, the minimal valuable change is API alignment: make our `Session` class satisfy the SDK protocol so that `Runner.run(session=our_session)` becomes possible. That unlocks future SDK-driven persistence (compaction, etc.) without ripping out working code.

**Deferred to follow-up:**
- Wiring `Runner.run(session=session)` into `service.py` and `runtime/bridge.py` — currently OmniAgents manages history manually before/after each `Runner.run` call. Switching to SDK-managed history is a meaningful refactor and is best done after step 5 lands so the approval flow doesn't have to be coordinated against two storage layers in flight.
- Schema migration to SDK-compatible tables — only relevant if we adopt SDK-managed history.
- Adopting `OpenAIResponsesCompactionSession` for automatic context compaction — listed in "Out of Scope" below.

---

## Step 5 — SafeAgent → `needs_approval` + new RPC wire format ⏳ PENDING

**Status:** Not started. This is the largest step and was deferred so steps 1–4 can land as a coherent first PR. Picked up next.

**Goal:** Delete `omniagents/core/agents/safe.py` (534 lines) and rewire tool approval through the SDK's native interruption model. Break the RPC wire format and update first-party clients in the same PR.

**Why this is last:** It's the highest-blast-radius change. Touches server runtime, RPC protocol, three first-party frontends (web React, Ink TUI, Studio), and integration tests. Audit estimated ~2 weeks for this step alone.

### Pre-flight references (verified during steps 1–4)

- SDK approval API:
  - `FunctionTool.needs_approval: bool | Callable[[RunContextWrapper, dict, str], Awaitable[bool]]` (declared in `agents.tool`)
  - `RunResult.interruptions: list[ToolApprovalItem]` returned by `Runner.run(...)` when approval is required
  - `RunState.approve(item, always_approve=False)` / `RunState.reject(item, always_reject=False, rejection_message=...)` (in `agents.run_state`)
  - Resume: pass `state=run_state` into the next `Runner.run(...)`
- Existing OmniAgents touch points (verified line numbers):
  - `core/agents/safe.py` — entire file slated for deletion (534 lines)
  - `core/agents/builder.py:225, 457` — SafeAgent construction
  - `core/agents/service.py:47, 669, 690, 811, 1398, 1616` — runtime SafeAgent integration
  - `core/config/loader.py:265, 371, 1186, 1430` — YAML loading of `safe_agent_options`
  - `core/agents/specs.py` — `SafeAgentOptions` already used; keep but interpret differently
  - `rpc/protocol.md:36-55, 377-501, 423-468` — current `client_request: ui.request_tool_approval` flow
  - `rpc/agents/_events.py:115-118` — `client_request` notification mapping
  - `rpc/agents/workers.py:61` — `client_request` routing
  - Frontends: `backends/web/ui/src/App.tsx:131`, `backends/web/ui/src/components/VoiceModal.tsx:466-482`, `backends/ink/tui/src/types/protocol.ts:164-174`, `backends/ink/tui/src/rpc/client.ts:241-407`, `backends/ink/tui/src/hooks/useMessagesNew.ts:445-461`

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

**Original plan:** A single PR containing all five steps as commits, squash-merged to `main`.

**Current proposal (revised after step 4):** Two PRs.

- **PR A — Steps 1–4** (ready now): foundation, realtime/voice verification, tool decorator cleanup + tool guardrail discovery, SDK Session protocol on omniagents `Session`. Net-positive on its own; SafeAgent stays working until PR B.
  - Commits: `ec3aa2e`, `9c49c53`, `399f491` plus `MIGRATION_PLAN.md`. ~670 lines changed.
- **PR B — Step 5**: SafeAgent removal + RPC wire format break + frontend lockstep. Atomic; reverts as one. Estimated 1500–2500 lines.

Either land both as a single PR per the original plan, or split as above. The split keeps PR A reviewable independently and lets PR B focus reviewer attention on the high-risk approval refactor.

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

1. **Concrete pinned model** ✅: `gpt-5.2` is now the default in `core/agents/builder.py` and `core/config/loader.py`.
2. **Reasoning effort default** ✅: `Reasoning(effort="medium")` auto-applied for any `gpt-5.x` model when not explicitly configured.
3. **Migration script for sessions DB**: not written. Step 4 was scoped down so no schema migration is needed. Re-add if SDK-managed history is adopted in a follow-up.
4. **Approval state lifecycle**: matches existing OmniAgents behavior — held in-memory on `SessionManager` (`pending_client_requests`) and on the service (`_pending_client_requests` asyncio Futures). Lost on server restart, same as today. The step-5 `run_state_token` will be a server-side handle to serialized `RunState` cached in-memory keyed by token; survives reconnect within the same server process, not server restart. No persistence layer to add.
5. **YAML deprecation horizon**: `use_safe_agent` / `safe_tool_names` / `safe_tool_patterns` continue to work for 1 minor release after step 5 ships; removal targeted for omniagents 1.0.
6. **`max_turns=None` adoption**: not opted-in by default; existing turn-limit behavior preserved unless YAML opts in.
7. **`mcp` being a hard dep on 0.16.0** ✅ confirmed: clean install, no conflicts.
8. **`numpy>=2.2` requirement** ✅ confirmed: clean install.
9. **`openai` major-version bump (1.x → 2.35.1)** ✅ confirmed: pulled in transitively by SDK; no breakage observed.

---

## Out of Scope (deferred to follow-up PRs)

These are net-new features the audit identified as worth adopting, but are explicitly excluded from this migration to keep scope tight:

- ~~Hosted tools: `WebSearchTool`, `FileSearchTool`, `CodeInterpreterTool`, `ComputerTool`, `ApplyPatchTool`, `LocalShellTool`, `ImageGenerationTool`, `HostedMCPTool`~~ ✅ Wired in this PR via `omniagents/core/tools/hosted.py`
- Sandbox agents (0.14.0) — **still deferred** (large surface; deserves its own PR)
- ~~`OpenAIResponsesCompactionSession` / context compaction~~ ✅ Wired in step 9
- ~~`MultiProvider` and `litellm`/`any-llm` model routing extensions~~ ✅ Wired in step 7
- ~~Run error handlers (`RunErrorHandler`, `model_refusal` handler)~~ ✅ Wired in step 6 via `error_handlers:` YAML
- `responses_websocket_session()` for WebSocket Responses transport — **still deferred** (premature without a use case)
- ~~New session backends: `SQLAlchemySession`, `RedisSession`, `MongoDBSession`, `EncryptSession`~~ ✅ Wired via `omniagents/core/session/backends.py`. `DaprSession` not wired but trivial to add following the same factory pattern.
- ~~MCP resources: `list_resources()`, `read_resource()`~~ ✅ Wired via new `mcp.list_resources` / `mcp.read_resource` server functions and MCP-approval flow.
- ~~`RetryPolicy` exposure in YAML~~ ✅ Wired in step 6 via `retry_policy:` YAML
- ~~`ApplyPatchEditor` / `apply_diff` primitives~~ ✅ Re-exported from `omniagents.core` in step 8
- ~~`max_turns=None` toggle in YAML schema~~ ✅ Wired in step 6

### Remaining work after this PR

- **Full `Runner.run(session=...)` refactor** — partially done (only when the user opts into `session.compaction` or `session.backend`). The default path still feeds history manually because the SDK-managed path persists at end-of-run via `save_result_to_session`, which loses partial transcripts on cancellation. Collapsing the two paths needs an SDK Session adapter that emits per-event live updates.
- **Frontend wiring for MCP approval events** — server emits `mcp_approval_requested` / `mcp_approval_resolved` but no first-party frontend handles them yet (workers auto-approve so multi-agent flows don't hang).
- **`Agent.as_tool()` YAML surface** — could become its own `agent_tools:` block alongside `handoffs:`. Out of scope here to keep this PR reviewable.
- **`is_enabled` callables on tools/handoffs** — the SDK supports per-tool/per-handoff predicates that overlap with our session-level `safe_tool_overrides` allow-list. Worth a follow-up that consolidates the two patterns.
- **Sandbox agents** — entire SDK subsystem (manifests, capabilities, multiple providers) still untouched.
- **Reasoning content replay** — SDK 0.13+ supports replaying reasoning items for adapters like DeepSeek; might fix the two `test_reasoning_pairing.py` failures noted in step 1.
