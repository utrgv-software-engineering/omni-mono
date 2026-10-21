# Sandbox lifecycle and environment replacement

This historical filename remains as a pointer for older references. In-place
`sandbox.switch` has been deleted. Omni Code declares sandbox capabilities and
a default profile; OmniAgents owns environment-scoped lifecycle behavior.

## What Omni Code declares

- The builtin `sandbox` capability in `omni_code/agents/omni/agent.yml`. It
  contributes read-only observation tools plus operator-only pause, unpause,
  activity, and snapshot-discard server functions.
- `omni_code/sandbox_profile_default.yml`, the packaged base of the profile
  chain resolved when an execution environment is materialized.

## Replacement model

A profile belongs to an immutable live execution environment. Changing profile
does not mutate a process-global sandbox session:

1. The launcher registers the desired Workspace and profile with AgentHost.
2. AgentHost materializes a replacement execution environment.
3. The Thread binding moves atomically to the replacement.
4. The old environment is stopped after the new binding succeeds.

Runs capture an `EnvironmentLease`, so an in-flight Run remains scoped to the
generation it started with. Failed replacement leaves the prior binding live.

See OmniAgents `docs/serve-protocol.md` and
`docs-internal/SANDBOX_LIFECYCLE.md` for the authoritative contracts.
