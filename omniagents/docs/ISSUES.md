# OmniAgents Issue Register
_Last updated: 2025-10-20_

## Summary
- 6 active bug reports (2 partially mitigated) across optimization, logging, streaming, and AgentDeck clients.
- 10 improvement tasks covering logging focus, navigation, variable handling, linting, project scaffolding, UI polish, and Flutter deprecation cleanup.
- 7 feature requests targeting MCP management, evaluation taxonomy/tooling, persistence, and API discoverability.
- Documentation remains sparse; MCP server health still unverified; major initiatives include SSO integration and deployment automation.

## Bugs

### B-04 • Optimization progress feedback is thin
- Status: Open
- Severity: Medium
- Summary: Websocket job updates only track raw evaluation counts; UI shows `completed/total` without phase context and ETA can drop to null when GEPA pauses.
- Evidence: `AgentOptimizationManager.update_job` in `omniagents/studio/server/api.py` only updates `completed`/`eta_seconds`; UI percent in `AgentsView` derives from those counters; GEPA `log_hook` emits opaque `PROGRESS` lines.
- Next Steps: Include stage metadata, keep ETA sticky, surface log path earlier, and render richer progress in the dialog.
- Related: `omniagents/core/eval/optimize.py`, `omniagents/studio/server/web/src/features/agents/AgentsView.tsx`

### B-05 • Trace streaming requires manual refresh
- Status: Open
- Severity: High
- Summary: The web client expects `api.createWebSocket()` for live trace updates, but the API client ships without that method and the backend lacks a websocket endpoint.
- Evidence: `useWebSocket` in `omniagents/studio/server/web/src/shared/hooks/useWebSocket.ts` calls missing helper; `omniagents/studio/server/web/src/lib/api.ts` exposes no websocket creator; FastAPI app defines no `/ws` route.
- Next Steps: Implement websocket endpoint in Studio server, add `ApiClient.createWebSocket()`, and wire reconnection telemetry to keep traces live.
- Related: `omniagents/studio/server/api.py`

### B-07 • Flet backend no longer launches
- Status: Open
- Severity: High
- Summary: Flet backend imports legacy `agents.*` modules that are absent, causing `ModuleNotFoundError` before UI boot.
- Evidence: Import block in `omniagents/backends/flet/app_base.py` references `agents.model_settings`, `agents.mcp`, and `set_default_openai_client`; backend entrypoint only guards `flet` import.
- Next Steps: Replace legacy imports with OmniAgents equivalents or gate optional extras; add smoke test that exercises `omniagents run --mode gui`.
- Related: `omniagents/backends/flet/ui.py`

### B-10 • Optimizer samples unlabeled scenarios as positives
- Status: Open
- Severity: Medium
- Summary: When `positives_only` is false, unlabeled scenarios fall into the negative bucket and skew GEPA sampling even though they lack judgments.
- Evidence: `_maybe_filter` in `omniagents/core/eval/optimize.py` ignores `label is None`; `StratifiedBatchSampler` treats falsy labels as negatives.
- Next Steps: Filter out unlabeled scenarios or require failure categories before optimization; add dataset validation.
- Related: `omniagents/core/eval/stratified_sampler.py`

### B-13 • AgentDeck input draft lost when docking terminal
- Status: Needs triage
- Severity: High
- Summary: Partially typed messages disappear if the user opens a docked terminal because the chat subtree is rebuilt when focus layout toggles, resetting `_ChatViewState` and its `TextEditingController`.
- Evidence: `_buildFocusLayout` in `examples/agentdeck/lib/multi_column_screen.dart` swaps `chatPane` into a new `Row` once `deck.docked` becomes true, causing Flutter to discard the old state keyed by `ValueKey(conv.id)`.
- Next Steps: Keep the chat widget in a stable position (e.g., wrap in `KeyedSubtree` or pre-build the layout) so docking terminals doesn’t drop the existing state.
- Related: `examples/agentdeck/lib/multi_column_screen.dart`, `examples/agentdeck/lib/chat_screen.dart`

### B-15 • AgentDeck columns ignore horizontal SafeArea
- Status: Open
- Severity: Medium
- Summary: Chat columns render edge-to-edge in embedded mode so the timeline and input row slide under iPhone landscape camera notches and home indicators.
- Evidence: `_ChatViewState.build` wraps the column in `SafeArea(top: false, left: false, right: false, …)` in `examples/agentdeck/lib/chat_screen.dart`, disabling horizontal insets for both deck and paged layouts.
- Next Steps: Respect horizontal safe-area padding (or apply `MediaQuery.viewPadding`) for conversation columns and ensure the chat input stays clear of system intrusions in deck and focus layouts.
- Related: `examples/agentdeck/lib/chat_screen.dart`, `examples/agentdeck/lib/multi_column_screen.dart`

## Improvements

### I-01 • Scope analysis goals to the active workflow
- Status: Open
- Priority: Medium
- Summary: Analysis Goals should be scoped to active workflow

### I-02 • URL-driven navigation
- Status: Open
- Priority: High
- Summary: Studio uses local React state with no router, so deep links, refresh, and sharing sections are impossible.
- Dependencies: Introduce routing (e.g., `react-router`) and sync with trace/agent state.
- Next Steps: Define canonical routes for home/logs/playground/agents/evaluation and persist selection in the URL.
- Related: `omniagents/studio/server/web/src/app/App.tsx`, `omniagents/studio/server/web/src/app/Dashboard.tsx`

### I-03 • Surface variables in Studio UI
- Status: Open
- Priority: Medium
- Summary: Session variables exist server-side but the Playground UI has no editor or inspector for them.
- Dependencies: Need API shape for listing/updating variables.
- Next Steps: Add variables panel to Playground, wire to `AgentService` variable helpers, and store edits per session.
- Related: `omniagents/core/agents/service.py`, `omniagents/studio/server/web/src/features/playground/PlaygroundView.tsx`

### I-04 • Add linter configuration
- Status: Open
- Priority: Medium
- Summary: The repo lacks lint tooling (`pyproject.toml` defines no `tool.ruff`/`flake8`), making CI drift easy.
- Dependencies: Choose linter (ruff/flake8) and align with existing CI.
- Next Steps: Add lint config and integrate with `tests.yml`.
- Related: `pyproject.toml`, `.github/workflows/tests.yml`

### I-05 • Scaffold evaluation assets by default
- Status: Open
- Priority: Medium
- Summary: `scaffold_project` omits evaluation files even though `scaffold_eval_quickstart` exists.
- Dependencies: Confirm desired default footprint.
- Next Steps: Invoke `scaffold_eval_quickstart` (flag-controlled) when creating new projects.
- Related: `omniagents/core/project/scaffold.py`

### I-06 • Scaffold pyproject template
- Status: Open
- Priority: Low
- Summary: Generated projects ship without a `pyproject.toml`, pushing teams to copy it manually.
- Dependencies: Decide on default metadata and extras.
- Next Steps: Author template in scaffold flow.
- Related: `omniagents/core/project/scaffold.py`

### I-07 • Scaffold pull request template
- Status: Open
- Priority: Low
- Summary: No pull request template is included when scaffolding projects.
- Dependencies: Provide canonical PR template (see `weather_agent` reference).
- Next Steps: Copy template into `.github/pull_request_template.md` during scaffold.
- Related: `omniagents/core/project/scaffold.py`

### I-08 • Scaffold release GitHub Actions
- Status: Open
- Priority: Low
- Summary: Project scaffolds miss workflows to build artifacts and attach them to GitHub releases.
- Dependencies: Decide on release toolchain.
- Next Steps: Add optional workflow similar to org standard (build + upload release assets).
- Related: `omniagents/core/project/scaffold.py`, `.github/workflows/publish.yml`

### I-09 • Scaffold Gemfury publishing workflow
- Status: Open
- Priority: Low
- Summary: Requesting automated Gemfury publishing for scaffolded projects.
- Dependencies: Requires Gemfury credentials pipeline.
- Next Steps: Provide optional workflow that runs on tagged releases and pushes to Gemfury.
- Related: `omniagents/core/project/scaffold.py`

### I-11 • Clear Flutter deprecation warnings
- Status: Open
- Priority: Medium
- Summary: Running `fvm flutter analyze` surfaces numerous Flutter 3.18 deprecations (`RawKeyboard`, `surfaceVariant`, `withOpacity`, etc.), causing the analyzer to exit with status 1.
- Evidence: `/home/ericm/.fvm_flutter/bin/fvm flutter analyze` output from 2025-10-18 lists 32 `deprecated_member_use` findings across `examples/agentdeck/lib/chat_screen.dart`, `terminal_panel.dart`, and related theme files.
- Next Steps: Replace deprecated APIs with current alternatives (e.g., `HardwareKeyboard`, `.withValues()`, `surfaceContainerHighest`) and introduce analyzer suppression only where migration is infeasible.
- Related: `examples/agentdeck/lib/chat_screen.dart`, `examples/agentdeck/lib/terminal_panel.dart`, `examples/agentdeck/lib/theme/tweetdeck_scheme.dart`

## Features

### F-01 • MCP server management UI
- Status: Planned
- Scope: Studio configuration
- Summary: Need UI and storage to register/manage MCP servers per project/agent.
- Blocked By: Missing backend persistence and API shape.
- Next Steps: Define MCP schema, extend Studio API, expose controls in Agents/Playground settings.
- Related: `omniagents/backends/flet/app_base.py`

### F-03 • Generate failure taxonomy draft
- Status: Planned
- Scope: Evaluation tooling
- Summary: No pipeline to auto-suggest failure categories from traces.
- Blocked By: Requires clustering/tooling pipeline.
- Next Steps: Prototype taxonomy generator and connect to Studio review workflow.
- Related: `omniagents/studio/tracing/storage.py`

### F-04 • Assign failure categories in Studio UI
- Status: In progress
- Scope: Logs review
- Summary: Analysis panel stores category IDs but UI lacks dedicated selectors and feedback.
- Blocked By: Better UX for multi-select and sync with `analysis_categories`.
- Next Steps: Add chip-based selector and show category usage counts.
- Related: `omniagents/studio/server/web/src/features/review/components/AnalysisPanel.tsx`

### F-06 • User model for server mode
- Status: Planned
- Scope: Multi-user deployments
- Summary: Server backend assumes single user; no auth/roles for shared deployments.
- Blocked By: Need authentication and session model.
- Next Steps: Design user schema, integrate with FastAPI dependencies, update clients.
- Related: `omniagents/core/agents/service.py`, `omniagents/studio/server/api.py`

### F-07 • Postgres (and other DB) support
- Status: Planned
- Scope: Tracing storage
- Summary: Storage layer only supports SQLite via `SQLiteStorage`.
- Blocked By: Abstraction over storage manager.
- Next Steps: Introduce interface plus Postgres implementation, support migrations, update config knobs.
- Related: `omniagents/studio/tracing/storage.py`

### F-08 • Version management UX
- Status: Planned
- Scope: Studio project lifecycle
- Summary: No tooling to track project versions or manage generated project releases.
- Blocked By: Need metadata schema and UI flows.
- Next Steps: Define version entities, integrate into Studio sidebar, expose CLI helpers.
- Related: `omniagents/core/project/compiler.py`, `omniagents/studio/server/web/src/app/Dashboard.tsx`

### F-09 • Generate OpenAPI specification
- Status: Planned
- Scope: Studio/server API
- Summary: FastAPI app does not expose curated OpenAPI spec for clients.
- Blocked By: Missing schema annotations and docs pipeline.
- Next Steps: Harden response models, enable FastAPI docs export, commit spec file.
- Related: `omniagents/studio/server/api.py`

## Documentation

### D-01 • Populate documentation set
- Status: Open
- Priority: Critical
- Summary: Documentation folder is largely empty and lacks onboarding, architecture, and usage guides.
- Next Steps: Draft README hierarchy for Studio, CLI, backends, and contribute guidelines.
- Related: `docs/`

## Needs Verification

### NV-01 • MCP server support health check
- Status: Pending verification
- Summary: Confirm whether MCP server integrations still function after recent refactors.
- Next Steps: Stand up MCP sample server, connect via Flet/Studio, document findings.
- Related: `omniagents/backends/flet/app_base.py`, `omniagents/core/agents/service.py`

## Larger Initiatives

### L-01 • SSO via OAuth
- Status: Planned
- Summary: Implement OAuth-based SSO for Studio/server deployments, covering token storage and session management.

### L-02 • Backends vs clients boundary
- Status: Planned
- Summary: Clarify architecture for bundling backends (Flet, Bubble Tea) versus clients (AgentDeck, future React client), including shared protocol surface.

### L-03 • Deployment automation
- Status: Planned
- Summary: Provide Kamal deployment, apt package, Brew formula, and Dockerfile for server distribution.

## Appendix
- B-04: `omniagents/core/eval/optimize.py`, `omniagents/studio/server/api.py`, `omniagents/studio/server/web/src/features/agents/AgentsView.tsx`
- B-05: `omniagents/studio/server/web/src/shared/hooks/useWebSocket.ts`, `omniagents/studio/server/web/src/lib/api.ts`, `omniagents/studio/server/api.py`
- B-07: `omniagents/backends/flet/app_base.py`, `omniagents/backends/flet/ui.py`
- B-10: `omniagents/core/eval/optimize.py`, `omniagents/core/eval/stratified_sampler.py`
- I-01: `omniagents/studio/server/api.py`, `omniagents/studio/tracing/storage.py`, `omniagents/studio/server/web/src/features/traces/store/traceStore.ts`
- I-02: `omniagents/studio/server/web/src/app/App.tsx`, `omniagents/studio/server/web/src/app/Dashboard.tsx`
- I-03: `omniagents/core/agents/service.py`, `omniagents/studio/server/web/src/features/playground/PlaygroundView.tsx`
- I-04: `pyproject.toml`, `.github/workflows/tests.yml`
- I-05: `omniagents/core/project/scaffold.py`
- I-06: `omniagents/core/project/scaffold.py`
- I-07: `omniagents/core/project/scaffold.py`
- I-08: `omniagents/core/project/scaffold.py`, `.github/workflows/publish.yml`
- I-09: `omniagents/core/project/scaffold.py`
- F-01: `omniagents/backends/flet/app_base.py`
- F-03: `omniagents/studio/tracing/storage.py`
- F-04: `omniagents/studio/server/web/src/features/review/components/AnalysisPanel.tsx`
- F-06: `omniagents/core/agents/service.py`, `omniagents/studio/server/api.py`
- F-07: `omniagents/studio/tracing/storage.py`
- F-08: `omniagents/core/project/compiler.py`, `omniagents/studio/server/web/src/app/Dashboard.tsx`
- F-09: `omniagents/studio/server/api.py`
- D-01: `docs/`
- NV-01: `omniagents/backends/flet/app_base.py`, `omniagents/core/agents/service.py`
- L-01: `omniagents/studio/server/api.py`, `omniagents/core/agents/service.py`
- L-02: `omniagents/backends/flet/`, `omniagents/backends/bubbletea/`, `examples/agentdeck/`
- L-03: `.github/workflows/`, `omniagents/backends/server/`
