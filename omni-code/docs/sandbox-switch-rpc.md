# Sandbox switching (`sandbox.switch`) — pointer

In-place sandbox switching was designed here and then lifted wholesale
into the omniagents framework. omni-code no longer contains any of the
mechanism; this file only records what the product declares and where
the real spec lives.

## What omni-code declares

- `sandbox` in the `capabilities:` list of `omni_code/agents/omni/agent.yml`
  — the framework's builtin pack. It contributes the read-only agent
  tools `sandbox_get_state` / `sandbox_wait_until_ready` and the
  **operator-only** `sandbox.*` server functions (`pause`, `unpause`,
  `notify_activity`, `discard_snapshot`, `switch`). Server functions are
  never part of the model's tool schema: the LLM can observe the
  sandbox, only the human/launcher can move it.
- `omni_code/sandbox_profile_default.yml` — the packaged base of the
  layered profile chain `omni serve` resolves (then
  `<config>/sandbox/default.yml`, per-project `sandbox.yml`, and an
  explicit `--profile`).

## Where the mechanism lives (omniagents)

- Switch orchestration (quiesce → snapshot → rebuild → re-wire →
  rollback): `omniagents.core.runtime.serve_harness.perform_switch`.
- Executor seam the serve host installs:
  `omniagents.core.runtime.sandbox_lifecycle.set_switch_executor`.
- Builtin pack pieces: `omniagents/builtin/capabilities/sandbox.yml`,
  `omniagents/builtin/server_functions/sandbox_lifecycle.py`,
  `omniagents/builtin/tools/sandbox_observe.py`, plus the session
  lifecycle proxies in `omniagents/core/sandbox/lifecycle_session.py`.
- The serve CLI itself is the framework's reserved subcommand
  (`omniagents/product_serve.py`).

## Docs

- Design + lifecycle spec: omniagents `docs-internal/SANDBOX_LIFECYCLE.md`
- Wire contract (events, RPCs, readiness): omniagents `docs/serve-protocol.md`
