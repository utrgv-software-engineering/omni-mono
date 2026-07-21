# Spec: in-place sandbox switching over RPC (`sandbox.switch`)

> **Status (2026-07): surface lifted into omniagents.** The RPC surface
> (`sandbox.pause` / `sandbox.unpause` / `sandbox.notify_activity` /
> `sandbox.discard_snapshot` / `sandbox.switch`), the read-only agent tools
> (`sandbox_get_state` / `sandbox_wait_until_ready`), and the authority model
> are now framework builtins: the `sandbox` capability pack
> (`omniagents/builtin/capabilities/sandbox.yml`,
> `omniagents/builtin/server_functions/sandbox_lifecycle.py`,
> `omniagents/builtin/tools/sandbox_observe.py`), with first-class session
> lifecycle proxies in `omniagents/core/sandbox/lifecycle_session.py` and the
> framework doc in omniagents `docs-internal/SANDBOX_LIFECYCLE.md`.
> `sandbox.switch` delegates to the executor the serve host installs via
> `omniagents.core.runtime.sandbox_lifecycle.set_switch_executor`
> (`perform_switch`). The switch *orchestration* below (quiesce, snapshot,
> rebuild, re-wire, rollback) now lives in the framework's serve harness
> (`omniagents/core/runtime/serve_harness.py`), and the serve CLI itself is
> the framework's reserved subcommand (`omniagents/product_serve.py`; wire
> contract in omniagents `docs/serve-protocol.md`) — this repo's
> `serve_cli.py` is deleted. This spec remains the design reference; code
> references below are historical (`omni_code/serve_cli.py` line numbers).

## Goal

Let a user change the sandbox profile of a **running** `omni serve` session
without killing the process. Today the launcher SIGTERM-restarts `omni serve`
with a new `--profile`, which drops the WebSocket, unmounts the conversation UI,
and forces a cold reconnect. Because the agent lives in the serve process *above*
the sandbox, and the SandboxSession is already a swappable dependency of the
agent spec, the switch can happen in-place: snapshot the old sandbox, build the
new one, re-attach it to the live agent, and tell connected clients. The
conversation, WS, and chat history never move.

## Authority model (decided)

Switching the sandbox is a **control-plane operation owned by the human**, not
the LLM — exactly like switching the model (`@server_function name="model"`,
invoked as `/model <name>`). The LLM must not be able to move itself to a less
isolated profile (e.g. `devbox` → `host`), which would be a sandbox-escape /
host-file-overwrite / egress-allowlist-bypass vector.

| Capability | Mechanism | Who calls it |
|---|---|---|
| Change the sandbox | `@server_function` `sandbox.switch` | Launcher UI / `/sandbox <profile>` slash command — **never the LLM** |
| Observe sandbox state | `@function_tool` `sandbox.get_state` | The LLM (read-only) |
| Wait for a switch to finish | `@function_tool` `sandbox.wait_until_ready` | The LLM (blocks the run until ready) |

Server functions are a separate registry (`AgentService._server_functions`) and
are never added to the model's tool schema — confirmed: nothing in
`omniagents/core/agents/builder.py` or `service.py` injects server functions into
the tool list. Tools (`function_tool` / `client_function_tool`) are the only
LLM-reachable surface; `client_function_tool`'s `escalate` is the precedent for a
tool that blocks the run until an external event resolves.

## Current architecture (what we build on)

All in `omni_code/serve_cli.py` unless noted.

- **Session bring-up** (`_serve`, ~lines 720–815):
  `client, options = build_sandbox_client(profile.raw)`; `manifest =
  build_manifest(profile.raw)`; `session = await _create_or_resume_session(...)`
  → `await session.start()`.
- **Swappable handle**: `agent_workspace_state.set_sandbox_session(session)` /
  `get_sandbox_session()` — a process-global slot the `sandbox.*` server
  functions resolve through (`server_functions/sandbox.py:_resolve_inner_session`).
- **Agent binding (idempotent, verified)**: `_attach_session_to_spec(spec,
  session, profile.raw)` sets `spec.sandbox_run_config.session = session` (it has
  a *replace* branch, `existing.session = session`). This swap is picked up by the
  next run — confirmed end-to-end through the SDK:
  - `omniagents` `service.py:2596` passes `getattr(self.spec,
    "sandbox_run_config", None)` into **every** run — read fresh off the
    long-lived spec each turn, so a mutated `.session` is visible next run.
  - `agents` SDK `run.py:639` constructs a **per-run** `SandboxRuntime` (cleaned
    up at `run.py:1531`), so its `SandboxRuntimeSessionManager._resources_by_agent`
    cache starts empty each run.
  - `runtime_session_manager._create_resources` reads `sandbox_config.session`
    and, when set, returns it directly as `_SandboxSessionResources(session=…,
    owns_session=False)` — the injected live session, not a recreated one.
  - **Therefore the swap takes effect strictly *between* runs, never mid-run** (an
    in-flight run already captured its session into its per-run runtime). This makes
    quiescing before the swap a correctness requirement, not a courtesy.
  `set_orchestrator_spec(spec)` republishes it for workers.
- **Services & terminal**: `_run_init` → `_start_services` →
  `_resolve_service_urls` produce `service_urls` (code-server/VNC);
  `_install_session_terminal_manager(app, session, profile.terminal)` binds the
  terminal backend to the session.
- **Snapshot = migration vehicle**: `LocalSnapshot(id=session_id,
  base_path=snapshot_dir)`, keyed by `session_id`. `session.stop()` persists the
  snapshot internally (`_persist_snapshot`); a later `client.create(snapshot=...)`
  with the same id rehydrates the workspace.
- **Teardown** (`_teardown`, ~959): `pty_terminate_all` → `unpause` →
  `session.stop()` (persists snapshot) → `_scrub_session_snapshot` →
  `session.aclose()` → `set_sandbox_session(None)`.
- **Readiness** (~863): a one-shot JSON payload to **stdout** with `sandbox_url`,
  `ws_url`, `ui_url`, `services`, `container_id`, `resume`. The launcher reads
  this from the child's stdout on first launch only.
- **Launcher → server-function call**: `oneShotServerCall(wsUrl, fn)`
  (`launcher/src/main/agent-process.ts:158`) opens a short-lived WS and invokes a
  server function by name; `callSandboxLifecycle` wraps it for pause/unpause.

## Refactor required first

`_serve` is monolithic — bring-up, wiring, run, and teardown are inlined. Extract
three reusable, idempotent helpers so initial start *and* switch share one path:

```
async def bring_up_session(profile, *, session_id, snapshot_dir,
                           prior_container_id) -> BroughtUp:
    # build_sandbox_client + build_manifest + _create_or_resume_session
    # + idle threshold + session.start() + _run_init + _start_services
    # + _resolve_service_urls
    # returns: session, started_services, service_urls, resume_tier, container_id

def wire_session(app, spec, session, profile, started_services) -> None:
    # _attach_session_to_spec(spec, session, profile.raw)
    # set_sandbox_session(session); set_orchestrator_spec(spec)
    # _install_session_terminal_manager(app, session, profile.terminal)

async def teardown_session(session, started_services) -> None:
    # the body of today's _teardown, MINUS set_sandbox_session(None)
    # (switch re-publishes immediately; only process exit nulls the slot)
```

`_serve` becomes: `bring_up_session` → build app/spec → `wire_session` → serve →
`teardown_session`. No behavior change; this is a pure extraction with tests.

## The `SwitchContext` (state the server function needs)

A server function only receives `(service, session)`. To rebuild a session for a
new profile it needs the bits `_serve` computed. Publish them once at startup via
a new `agent_workspace_state` slot (mirrors `set_orchestrator_spec`):

```python
@dataclass
class SwitchContext:
    app: Any                 # FastAPI app (app.state.agent_service, spec)
    spec: Any                # the live AgentSpec
    session_id: str
    snapshot_dir: Optional[Path]
    profile_inputs: ProfileInputs   # project_id, sources, workspace, agent paths,
                                    # auth_token, host/port — everything resolve_profile
                                    # + bring_up_session need to rebuild for a new name
    started_services: list          # mutable; switch replaces it
    lock: asyncio.Lock              # serializes switch vs. switch and switch vs. run
```

`set_switch_context(ctx)` at the end of startup; `get_switch_context()` in the
server function. `started_services` and the current session are updated in place
on each successful switch.

## `sandbox.switch` — server function

```python
@server_function(
    name_override="sandbox.switch",
    description="Switch the sandbox to a different profile (operator command).",
    params_schema={"type": "object",
                   "properties": {"profile": {"type": "string"}},
                   "required": ["profile"], "additionalProperties": False},
    result_schema=_SWITCH_RESULT_SCHEMA,  # { ok, profile, backend, container_id, services, reason? }
    strict=True,
)
async def sandbox_switch(service, session, profile: str) -> dict:
```

Sequence (all under `ctx.lock`):

1. **Resolve** the target profile through the same layered chain
   (`resolve_profile(profile, project_id=ctx.profile_inputs.project_id, …)`).
   Reject unknown names with `{ok: false, reason}`. No-op (same profile) returns
   early `{ok: true, ...}`.
2. **Quiesce the agent**: cancel or await any in-flight run for this session
   (see Concurrency). The lock blocks new runs from starting mid-swap.
3. **Set state `switching`** and broadcast `sandbox:switching` (see Events) so the
   UI can show the transition and the `wait_until_ready` tool has something to
   await.
4. **Snapshot + tear down the old session**: `await teardown_session(old_session,
   ctx.started_services)`. `session.stop()` persists the workspace tar under
   `session_id`. (Does **not** null the global handle.)
5. **Bring up the new session**: `brought = await bring_up_session(target_profile,
   session_id=ctx.session_id, snapshot_dir=ctx.snapshot_dir,
   prior_container_id=None)`. Same `session_id` + same `snapshot_dir` → the new
   `client.create(snapshot=...)` rehydrates the workspace from the tar step 4 wrote.
6. **Re-wire**: `wire_session(ctx.app, ctx.spec, brought.session, target_profile,
   brought.started_services)`. This swaps `spec.sandbox_run_config.session`,
   republishes the handle + orchestrator spec, and rebinds the terminal manager.
   Update `ctx.started_services`.
7. **Set state `ready`**, broadcast `sandbox:changed` with the new `container_id`,
   `services`, `backend`, `profile`.
8. **Return** `{ok: true, profile, backend, container_id, services}`.

**Rollback**: if step 5/6 fails, attempt to bring up the *previous* profile from
the same snapshot (the workspace tar is intact), re-wire it, broadcast
`sandbox:changed` back to the old profile, and return `{ok: false, reason}`. If
even rollback fails, broadcast `sandbox:lost` and return the error — the launcher
then offers a hard restart. The conversation/WS still survive; only the sandbox
is down.

## Agent tools

```python
@function_tool   # omniagents.core.tools
async def sandbox_get_state(ctx) -> dict:
    # reads agent_workspace_state: { profile, backend, ready: bool,
    #   switching: bool, paused: bool, container_id }
```

```python
@function_tool
async def sandbox_wait_until_ready(ctx, timeout_seconds: int = 120) -> dict:
    # awaits an asyncio.Event flipped by sandbox.switch on ready/failed.
    # returns { ready: bool, profile, reason? }. Blocks the run like escalate.
```

Both are read-only/observational and safe for the LLM. `wait_until_ready` awaits
an `asyncio.Event` on the `SwitchContext` (set on `ready`, set-with-error on
failure) — no client round-trip needed since the state is in-process. A bare
`function_tool` suffices; `client_function_tool` is unnecessary here. Register
them in the agent's tool capability set (same place omni-code adds its other
built-in tools), gated so they only appear when running under `omni serve` (the
`SwitchContext` exists) — otherwise return `{ready: true, profile: "host"}` so a
non-serve invocation degrades gracefully.

## Client/UI notification (events, not stdout)

The stdout readiness payload is read only on first launch; a switch happens while
the launcher is connected over WS, so it must **broadcast** over the existing
session channel. Reuse the broadcast pattern already used by
`server_functions/bash_jobs.py` (`_broadcast_snapshot`) and `recap`:

- `sandbox:switching` → `{ profile }`
- `sandbox:changed`   → `{ profile, backend, container_id, services, ports }`
- `sandbox:lost`      → `{ reason }`

Launcher side: `AgentProcess` subscribes to these on its live WS and updates
`AgentProcessData` (new `container_id`, new `services` for the VNC/code-server
panes) without restarting. The conversation pane does nothing; only the service
iframes reload to the new URLs. The launcher's `defaultProfileName`/per-tab/sticky
binding is updated to the new profile so a *future* cold start matches.

## Concurrency & in-flight runs

- `ctx.lock` serializes `sandbox.switch` against itself and against run starts.
- A run in progress when a switch lands: **cancel it** before teardown via
  `service._session_manager.cancel(run_id)` (verified: `service.py:2822`), emit a
  system line into the transcript ("Sandbox switched; the previous task was
  interrupted"), and let the user/agent re-issue. This is mandatory, not optional:
  per the re-attach analysis above, the session swap only lands between runs, and
  an in-flight run holds its own per-run `SandboxRuntime` bound to the old session
  — so the run must end before the new session is wired. Awaiting instead risks a
  long-running tool blocking the swap indefinitely; cancel matches today's SIGTERM
  behavior (which also kills the turn) but cleanly.
- New runs that arrive during `switching` block on the lock and proceed once
  `ready` — or the agent can call `wait_until_ready` explicitly.

## Cross-backend migration (verified — works via the host snapshot)

`LocalSnapshot` is a host-side tar (`base_path=snapshot_dir` on the machine
running `omni serve`) and is **backend-agnostic**, because every backend
implements the same workspace tar interface. Confirmed in the SDK/omniagents:

- `omniagents/core/sandbox/aci.py` `AciSandboxClient.create(snapshot=…)`
  (line 464–479) accepts a snapshot identically to docker, via
  `resolve_snapshot(snapshot, session_id)`.
- `AciSandboxSession` implements `persist_workspace()` (tar the root, line 386)
  and `hydrate_workspace(tar)` (line 393) — the same interface
  `snapshot_lifecycle` drives on `session.stop()` (persist) and on create
  (restore). On hydrate, ACI extracts the tar **straight onto the Azure Files
  mount** (fast path, line 399–403).

So a **docker ↔ aci** switch needs no special handling: the old session's
`stop()` persists the workspace tar to the host `snapshot_dir` (keyed by
`session_id`); the new backend's `create(snapshot=…)` with the same id+dir
rehydrates from that tar. Pass `prior_container_id=None` on switch (different
backend = no warm reattach), so it always takes the create+rehydrate path. The
new backend's client is built from the target profile (`build_sandbox_client`),
which for `aci` carries the Files-mount config from the profile, so ACI hydrate
lands on the share.

**The only special case is `unix_local` (`host`)**: `_build_snapshot` returns a
noop snapshot for `client_type == "unix_local"` — the host filesystem *is* the
persistence layer, there's nothing to tar. Switching **into** host hydrates the
snapshot onto the user's real files (overwrite risk), and switching **out of**
host has no host-side container state to snapshot. This is exactly the case the
launcher already guards with the host-ward `window.confirm`; the switch function
must (a) when leaving host, capture the host workspace into the snapshot before
bring-up, and (b) when entering host, hydrate onto the host dir only after the
confirm. Cloud never offers `host`, so this only matters on desktop.

**v1 scope**: docker↔docker, aci↔aci, **and** docker↔aci are all in scope (the
mechanism is uniform). host↔container transitions ship with the explicit
overwrite confirm and the leave/enter host handling above.

## Launcher-side changes

- `agent-process.ts`: add `switchSandbox(profile)` → `oneShotServerCall(wsUrl,
  'sandbox.switch', { profile })` (extend `oneShotServerCall` to forward params).
  Subscribe to `sandbox:switching/changed/lost` on the live session WS and update
  `AgentProcessData` instead of restarting.
- `use-auto-launch.ts`: the `profileNameOverride`-change effect (lines ~270–291)
  no longer SIGTERM-restarts when the session is `running` — it calls
  `switchSandbox` and lets the broadcast drive the UI. The SIGTERM path stays only
  for idle (pre-launch, where there's no live session) and as a hard-failure
  fallback if `sandbox.switch` returns `{ok:false}` and rollback also failed
  (`sandbox:lost`).
- Chat/Code render the friendly in-place transition (overlay + service-pane
  reconnect) from the `sandbox:switching`/`changed` events — the conversation
  surface stays mounted.

## Error handling / edge cases

- **Unknown profile** → `{ok:false, reason}`, no teardown.
- **Snapshot persist fails** in step 4 → abort before tearing down further; keep
  the old session if possible.
- **Bring-up fails** → rollback to previous profile (workspace tar intact).
- **Switch during pause (idle)** → `unpause` first (teardown already does).
- **Concurrent switches** → serialized by the lock; the second sees the new
  current profile and may no-op.
- **`host` semantics in cloud** → `host` isn't an offered profile in cloud, so the
  destructive host-ward path only exists on desktop and keeps its confirm.

## Phasing

1. **Refactor** `_serve` into `bring_up_session` / `wire_session` /
   `teardown_session` (+ `SwitchContext`, `set_switch_context`). Pure extraction,
   green tests.
2. **`sandbox.switch`** server function (docker↔docker, aci↔aci, docker↔aci — the
   mechanism is uniform), with lock + run-cancel + rollback + events. Unit +
   integration test (docker image A→B mid-conversation; assert WS stays up,
   transcript intact, tools run in new sandbox).
3. **`sandbox.get_state` / `sandbox.wait_until_ready`** tools.
4. **Launcher**: `switchSandbox` RPC + event subscription + in-place UI; SIGTERM
   retained only for pre-launch and hard-failure fallback.
5. **host↔container** transitions: leave-host snapshot capture + enter-host
   hydrate-after-confirm (desktop only; cloud never offers `host`).

## Tests (omni-code)

- `bring_up_session`/`teardown_session` extraction parity (start flow unchanged).
- `sandbox.switch` happy path (docker A→B): same `session_id`, snapshot rehydrate,
  `spec.sandbox_run_config.session` rebound to the new session, services
  re-resolved, `sandbox:changed` emitted.
- Re-attach actually rebinds tools: after switch, an agent bash/fs tool call
  executes in the **new** sandbox (assert via a marker file written pre-switch and
  read post-switch from the rehydrated workspace).
- Rollback on bring-up failure (inject a failing client) → previous profile
  restored, `{ok:false}`.
- `wait_until_ready` blocks then resolves on `ready`; times out cleanly.
- Authority: `sandbox.switch` is **not** present in the agent's tool schema
  (assert it's only in the server-function registry).

## Verified against the code (no open questions)

The three load-bearing assumptions were checked end-to-end in `omniagents`, the
`agents` SDK, and `omni-code`; all hold:

1. **The session swap is picked up on the next run.** `omniagents`
   `service.py:2596` reads `self.spec.sandbox_run_config` fresh every run; the
   `agents` SDK builds a per-run `SandboxRuntime` (`run.py:639`, cleaned up
   `:1531`); `runtime_session_manager._create_resources` returns the injected
   `sandbox_config.session` (`owns_session=False`) with no cross-run cache. It
   lands strictly **between** runs — hence the mandatory quiesce.
2. **Cross-backend docker↔aci works via the host `LocalSnapshot`.**
   `omniagents/core/sandbox/aci.py` `AciSandboxClient.create(snapshot=…)` (464–479)
   plus `AciSandboxSession.persist_workspace`/`hydrate_workspace` (386/393) give the
   same tar interface as docker; ACI hydrate writes onto the Files mount. Only
   `unix_local`/host is special (noop snapshot — host fs is the state).
3. **Run cancellation is reachable from a server function.**
   `service._session_manager.cancel(run_id)` (`service.py:2822`); the server
   function receives `service`.

The only thing deliberately left to implementation choice (not a correctness
unknown) is the host↔container leave/enter handling in Phase 5.
