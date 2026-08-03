# Execution-environment sandbox lifecycle

The framework owns sandbox lifecycle as an environment-scoped control-plane
surface. An AgentHost may own many execution environments; no sandbox session
or lifecycle controller is process-global.

## Authority model

Changing sandbox state is an operator action. Server functions are registered
separately from model tools, so the LLM can observe lifecycle state but cannot
pause, replace, or weaken its own environment.

| Capability | Mechanism | Authority |
|---|---|---|
| Pause / unpause / activity / snapshot discard | `sandbox.pause`, `sandbox.unpause`, `sandbox.notify_activity`, `sandbox.discard_snapshot` | Launcher control surface |
| Replace the environment profile | Materialize a new environment, atomically bind the Thread, then stop the old environment | AgentHost control plane |
| Observe lifecycle state | `sandbox_get_state` | LLM, read-only through the current Run lease |
| Wait until ready | `sandbox_wait_until_ready` | LLM, scoped to the current Run lease |

There is no `sandbox.switch` operation. Profiles and live sandbox sessions are
immutable properties of an execution environment. Replacement is explicit
resource orchestration, not mutation of a shared process slot.

## Ownership

- `EnvironmentLease` captures the Workspace adapter, sandbox runtime, terminal
  defaults, capabilities, services, and lifecycle controller for one live
  environment generation.
- `ServeEnvironmentProvisioner` materializes host or sandbox environments from
  registered Workspace and profile definitions.
- `ServeEnvironmentLifecycleController` exposes lifecycle state and delegates
  pause/unpause/activity/discard operations to its environment's session.
- `AgentHostResources.environments` owns provisioning, stopping, and shutdown
  drain. Environment stop tears down services and the sandbox session before
  publishing `stopped`.
- Run-scoped observation resolves through the captured `EnvironmentLease`; it
  never consults a process-global fallback.

The launcher replacement sequence is documented in
`docs/serve-protocol.md`: register resources, materialize the replacement,
bind the Thread atomically, and retire the previous environment.
