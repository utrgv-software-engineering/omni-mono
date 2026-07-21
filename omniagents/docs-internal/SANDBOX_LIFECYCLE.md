# Sandbox lifecycle: builtin RPC surface, observe tools, and the switch seam

Lifted from omni-code (Tier-3 wave B1). The framework now owns the sandbox
lifecycle *surface*; the serve host keeps the *orchestration*.

## Authority model (security property of the `sandbox` pack)

Changing the sandbox is a **control-plane operation owned by the human
operator** — exactly like switching the model. The LLM must never be able to
move itself to a less-isolated profile (e.g. `devbox` → `host`), which would be
a sandbox-escape / host-file-overwrite / egress-allowlist-bypass vector.

| Capability | Mechanism | Who calls it |
|---|---|---|
| Pause / unpause / idle ping / snapshot discard | `sandbox.pause` / `sandbox.unpause` / `sandbox.notify_activity` / `sandbox.discard_snapshot` server functions | Launcher / operator UI — **never the LLM** |
| Change the sandbox | `sandbox.switch` server function | Launcher UI / `/sandbox <profile>` slash command — **never the LLM** |
| Observe sandbox state | `sandbox_get_state` tool | The LLM (read-only) |
| Wait for a switch to finish | `sandbox_wait_until_ready` tool | The LLM (blocks the run until ready) |

Server functions live in a separate registry (`AgentService._server_functions`)
and are never added to the model's tool schema; tools are the only
LLM-reachable surface. Both halves ship in the one builtin `sandbox` capability
pack (`omniagents/builtin/capabilities/sandbox.yml`) — activating it gives the
operator the lifecycle RPCs and the agent only the two read-only tools.

## Pieces and where they live

* **Session lifecycle proxies** —
  `omniagents/core/sandbox/lifecycle_session.py`.
  `LifecycleSandboxSession` (returned by every omniagents extended sandbox
  client via `LifecycleSessionClientMixin._wrap_session`) adds first-class,
  capability-probing `pause()` / `unpause()` / `paused()` /
  `notify_activity()` / `set_idle_threshold()` / `stop_idle_watcher()` /
  `is_idle_paused()` to the SDK's `SandboxSession` wrapper. Downstream code
  must never reach through `session._inner`; the unwrap is framework-internal
  (`resolve_backend_session` / `backend_supports`).
* **Lifecycle RPCs** —
  `omniagents/builtin/server_functions/sandbox_lifecycle.py`, activated by the
  `sandbox` pack. Resolve the live session from
  `omniagents.core.runtime.serve_state.get_sandbox_session()` and degrade to
  `{"ok": false, "supported": false}` on backends without the primitive.
* **Switch executor seam** —
  `omniagents/core/runtime/sandbox_lifecycle.py::set_switch_executor`.
  `sandbox.switch` validates and delegates to the installed
  `async (profile: str) -> dict`; with none installed it returns a clear
  `{"ok": false}` error. The orchestration (quiesce runs, snapshot, teardown,
  bring-up, re-wire, rollback, UI broadcasts) is serve-host policy and stays
  downstream until the serve-harness lift. Mirrors the recap/compaction
  summarizer seams.
* **Observe tools** — `omniagents/builtin/tools/sandbox_observe.py`, reading
  the process-global `serve_state.SwitchContext`. Outside a serve process they
  answer "ready, no switching".

The full switch orchestration spec (sequence, rollback, cross-backend
migration, launcher wiring) remains in omni-code's
`docs/sandbox-switch-rpc.md` until the serve harness itself is lifted.
