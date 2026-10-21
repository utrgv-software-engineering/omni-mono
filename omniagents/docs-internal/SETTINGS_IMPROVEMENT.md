# Settings Flow Cleanup Plan

## Problem Summary

OmniAgents YAML specs support `settings:` entries that materialize as `AgentSpec.settings_fields`. These were introduced so UIs could collect API keys or other secrets at runtime, avoiding reliance on `.env` files. The current implementation is inconsistent:

- **Flet GUI** dynamically renders the declared fields and writes them to local storage, falling back to env/defaults when fields are empty.
- **Bubble Tea (TUI)**, **server/FastAPI**, and **CLI** ignore the field list: they only read environment variables and defaults during agent construction.
- Settings resolution logic is duplicated across `_default_build_agent`, `AgentService`, `Runner`, `LocalAgent`, and Flet helpers, leading to divergent behavior.

Consequences:

- Terminals and remote clients cannot provide secrets interactively; they must pre-set environment variables, defeating the purpose of runtime settings.
- Mobile/Flet builds that cannot ship `.env` files rely on bespoke code paths, while other backends silently fail when required values are missing.
- Maintaining multiple ad-hoc merges of env/defaults increases the risk of regressions and makes enhancements (e.g., validations, masking) hard.

## Why Cleaning This Up Matters

- **Consistent UX**: Every backend should have a clear story for collecting secrets without bundling them into the binary or environment.
- **Security & flexibility**: Users should supply sensitive values at runtime when deploying to constrained environments (iOS, web), with optional caching at the UI layer.
- **Maintainability**: A single resolution path reduces duplication and makes it easier to evolve validation, logging, and tooling.

Without consolidation, each new backend (or update) needs custom logic to honor `settings_fields`, increasing drift and confusion.

## Proposed Solution

### 1. Centralize Settings Resolution

- Introduce a helper (e.g., `core/settings.py::resolve_settings(spec, overrides, env=os.environ)`) that:
  - Starts from `DEFAULT_SETTINGS_FIELDS` + project-level defaults + spec overrides.
  - Applies stored overrides from the client.
  - Falls back to environment variables (`key.upper()`) and declared defaults.
  - Returns both the merged dict and the list of missing-required fields.
- Replace direct env-merging logic in `_default_build_agent`, `AgentService`, `Runner`, `LocalAgent`, CLI, and Flet with this helper.

### 2. Session-Level Override API

- Extend `AgentService` with an RPC method (e.g., `session.update_settings`) to accept a dict of overrides, storing them on the session object.
- When a session starts, resolve settings using the centralized helper and per-session overrides; the merged dict feeds MCP/tool builders.

### 3. Client-Driven Dialog via RPC

- If required fields are still missing after env/defaults/overrides, `AgentService` should emit a `client_request` (similar to tool approvals) describing the fields that need values.
- Clients respond with a map of user-provided entries; `AgentService` merges them and re-resolves before continuing.
- Backends that already have values (Flet, cached CLI) can push them proactively via `session.update_settings` to skip the prompt cycle.

### 4. Backend Integration

- **Flet**: continue storing values locally, but remove bespoke env merges; call `session.update_settings` on save and enhance validation/masking.
- **Bubble Tea**: add a prompt flow for missing values (simple modal or command palette). On submission, call the new RPC, optionally caching to disk.
- **Server (FastAPI)**: expose an HTTP/RPC endpoint for external clients or web UIs to supply overrides.
- **CLI**: optional interactive prompt or flags to send overrides; otherwise document the env-based fallback.

### 5. Documentation & Validation

- Document precedence (client overrides → env → defaults) and key casing expectations.
- Consider marking fields as `required` (e.g., `hint` containing “Required”) to signal when the server should prompt.
- Ensure secrets are masked in logs/playback and never written to disk without explicit user intent.

## Relevant Files to Touch

- `omniagents/core/config/loader.py`
- `omniagents/core/agents/specs.py`
- `omniagents/core/agents/builder.py`
- `omniagents/core/agents/service.py`
- `omniagents/core/agents/factory.py`
- `omniagents/core/runtime/runner.py`
- `omniagents/rpc/agents/local.py`
- `omniagents/backends/flet/app_base.py`
- `omniagents/backends/flet/settings.py`
- `omniagents/backends/flet/storage.py`
- `omniagents/backends/bubbletea/__init__.py`
- `omniagents/backends/cli/runner.py`

Additional contexts that may need updates:

- RPC payload definitions (`omniagents/rpc`)
- Documentation describing YAML schema and runtime behavior
- Tests covering settings resolution and RPC handshake

## Next Steps

1. Implement `resolve_settings` helper and migrate core builders to use it.
2. Add session-scoped overrides and missing-field detection in `AgentService`.
3. Define new RPC messages (`session.update_settings`, `client_request` payload for settings prompts) and update local/remote agents.
4. Update Flet, Bubble Tea, and CLI backends to participate in the new flow.
5. Add tests for settings resolution, RPC exchanges, and UI integration hooks.
6. Refresh documentation to reflect the unified settings story.
