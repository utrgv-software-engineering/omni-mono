# Capability packs carry server functions

Status: IMPLEMENTED 2026-07-17 (working tree). Open questions resolved
as proposed: inline capability specs support `server_functions:` (free
via the shared parser); the `bj-*` aliases shipped upstream; duplicates
across active capabilities warn and keep the first. One deviation from
§3: the capability layer registers at service construction and the
project registrar (which overwrites unconditionally) runs after — so
project-wins precedence falls out of ordering, with skip-don't-overwrite
needed only between capabilities themselves.

Extends CAPABILITY_PACKS_DESIGN.md: a capability bundles tools +
instructions + approval policy + sampling params — this adds **server
functions** as the fifth member.

## Problem

The autonomy (goal/loop/wakeup) and panel (workers, bash_jobs) machinery
now lives in the framework, but their slash commands and UI endpoints
still require every product to keep a `server_functions/` shim whose
only real content is `@server_function` registration + argument parsing.
That's ~500 lines of boilerplate omni-code keeps solely because server
functions have no builtin/override story:

- **Tools**: builtin dir (`omniagents/builtin/tools/`) + project dirs,
  project wins by name, agents opt in via capabilities.
- **Capabilities**: builtin dir + project dirs/inline, project wins by
  name, agents opt in via `capabilities:`.
- **Server functions**: project dirs only, all auto-registered on the
  service, no builtin source, no per-agent opt-in. (The framework's
  `session.ensure` / `mcp.*` functions are hardcoded in
  `AgentService.__init__` — infrastructure, not an extension point.)

A naive fix — auto-register a `builtin/server_functions/` dir the way
builtin tools are discovered — would give every omniagents product
`/goal` and `/loop` whether or not the agent has the goal/loop
capabilities, and would leave no way to ship a pack's tools without its
commands. Gating server functions on the **capability** keeps activation
where it already lives: an agent that lists `goal` in `capabilities:`
gets the `goal_complete` tool, the instructions fragment, *and* the
`/goal` server functions; an agent that doesn't, gets none of it.

## Current mechanics (what the design builds on)

1. `load_project` discovers project server functions
   (`paths.server_functions` dirs) into `server_functions_registry`
   (name → meta dict with func/description/schemas/on_invoke/strict) —
   `omniagents/core/project/runtime.py` around line 265.
2. `make_server_functions_registrar(functions)` wraps that registry in a
   `registrar(service)` closure; `core/config/loader.py` stores it as
   `spec.register_server_functions`.
3. `AgentService.__init__` registers its hardcoded infra functions and
   invokes `spec.register_server_functions(self)` for the project set.
4. Capability YAMLs are parsed by
   `core/capabilities/parser.py:parse_capability_spec`, which eagerly
   resolves `tools:` names against the tool registry (fail-fast on
   typos). Registry precedence: builtin caps first, project caps
   override by name.

## Design

### 1. Schema: `server_functions:` on a capability

```yaml
name: goal
tools:
  - goal_complete
server_functions:
  - goal          # slash command /goal
  - goal.stop
  - goal.status
instructions: |
  ...
```

- Optional list of server-function **names** (the registered name, i.e.
  `name_override` when present — same key clients call).
- Resolved eagerly at capability parse time against the combined
  server-function registry (see §2), mirroring how `tools:` fail fast.
  `parse_capability_spec` gains a `server_functions_registry` kwarg;
  unknown names raise at project load with the capability name + source
  in the message.
- `_YamlCapability` stores the resolved meta entries and exposes them
  via a new `server_functions()` accessor.
- The base `Capability` contract gets a default `server_functions()`
  returning `[]`, so Python-class capabilities can bundle server
  functions by overriding it (symmetric with `tools()`).
- A capability with `server_functions:` but no `tools:`/`agent_tools:`
  becomes legal (relax the parser's "at least one of tools/agent_tools"
  rule to "at least one of tools/agent_tools/server_functions") — a
  pack may be commands-only.

### 2. Builtin server-function source

New directory `omniagents/builtin/server_functions/`, discovered by the
project loader into the same `server_functions_registry`, **before**
project dirs so project definitions override builtins by name — byte-
consistent with the tools and capabilities precedence story.

**Hard prerequisite:** `discover_server_functions_in_dir` still imports
modules under the bare dirname prefix (`server_functions.goal`). With a
builtin dir added, the builtin `omniagents/builtin/server_functions/`
and every product's `server_functions/` would collide in `sys.modules`
— the exact bug class fixed for tool discovery in the previous batch.
Port that fix first: canonical dotted module names
(`omniagents.builtin.server_functions.goal` vs `server_functions.goal`),
synthetic-prefix fallback on mismatch, and **no eviction** of
canonically-named modules (server-function modules hold live state —
tick loops, prompt-builder installs — and are never evicted today; keep
it that way explicitly).

### 3. Activation

Registration on a service happens in two layers, in order:

1. **Project layer (unchanged, backward compatible):** everything in
   the project's `server_functions/` dirs is auto-registered via the
   existing registrar, exactly as today. Existing projects see zero
   behavior change.
2. **Capability layer (new):** after the project registrar runs, the
   service walks the agent spec's resolved capabilities and registers
   each capability's `server_functions()` entries — **skipping any name
   already registered**. Skip-don't-overwrite means a project shim that
   redefines `/goal` wins over the builtin pack's version, consistent
   with every other override direction in the system.

Notes:

- Registration is per-service, and a service hosts one agent spec, so
  "per-agent opt-in" and "per-service registration" coincide. Handoff/
  sub-agents built from the parent spec inherit the parent's registered
  set (they share the service), which matches today's behavior.
- A client calling a server function whose capability the agent doesn't
  have gets the existing "unknown server function" error — no new error
  surface.
- Duplicate names across two *active* capabilities: first registration
  wins, and the second logs a warning (mirrors the duplicate-tool
  warning in tool discovery). Two capabilities sharing a pack of
  functions should reference the same names, which is then a no-op.

### 4. What ships in `builtin/server_functions/`

Migrated from the omni-code shims, minus product content:

| Module | Functions | Attached to pack |
|---|---|---|
| `goal.py` | `goal` (slash), `goal.stop`, `goal.status` | `goal.yml` |
| `loop.py` | `loop` (slash), `loop.status/stop/clear`, `cron.create/list/delete` | `loop.yml` |
| `wakeup.py` | `wakeup.stop`, `wakeup.status` | `wakeup.yml` |
| `workers.py` | `workers.list`, `workers.kill` | `workers.yml` |
| `bash_jobs.py` | `bash_jobs.list/kill/tail` (+ `bj-*` aliases), sweeper bootstrap | `shell.yml` |

All of these delegate to `omniagents.core.autonomy.*` /
`omniagents.builtin.tools.worker_tools` / `._bash_jobs` — the
implementations are already framework-side; only registration + slash
argument parsing moves.

### 5. Product seams the migration exposes

Deleting the omni-code shims removes two side effects that must move,
not vanish:

1. **Prompt-builder installation.** The goal/loop shims install
   omni-code's coding prompt builders at import. New install point:
   `omni_agents/omni/context.py`, next to `set_skill_roots_provider`
   (it already runs in every agent process before first use). The
   workers shim's `workers_runtime.install()` moves there too.
2. **Loop default prompt.** `resolve_default_loop_prompt` reads
   `<workspace>/.omni/loop.md` and `~/.config/omni_code/loop.md` —
   product paths consumed by the `/loop` slash handler. Add
   `set_loop_default_prompt_resolver(fn)` to `core/autonomy/loop.py`
   (same seam family as the prompt builders); the framework default
   returns None (meaning "loop requires an explicit prompt" — the
   current error path when no prompt and no file). omni-code installs
   its resolver from `context.py`.

## Migration plan

Framework (one PR):
1. Port the canonical-naming/no-eviction fix into
   `discover_server_functions_in_dir` (+ regression tests mirroring the
   tool-discovery ones).
2. Parser + `_YamlCapability` + `Capability.server_functions()` +
   loader threading (`server_functions_registry` into capability
   discovery; builtin SF dir discovered before project dirs).
3. Capability-layer registration in `AgentService` after the project
   registrar.
4. `builtin/server_functions/*.py` + `server_functions:` lists added to
   the five builtin packs. `set_loop_default_prompt_resolver` seam.
5. Tests: builtin SF discovery precedence; capability-gated
   registration (agent with pack gets `/goal`, agent without doesn't);
   project-shim override wins; commands-only capability parses.

omni-code (companion PR):
6. Move prompt-builder + workers installs to `omni_agents/omni/context.py`;
   install the loop default-prompt resolver.
7. Delete `server_functions/{goal,loop,wakeup,workers,bash_jobs}.py`.
   Survivors (genuinely product-side): agentio feedback, artifact,
   compact, filesystem, help, model, recap, sandbox, terminal,
   voice_model.
8. Keep omni-code's `wakeup.yml`/`workers.yml`/`shell.yml` overrides —
   they must now also carry the `server_functions:` lists (a project
   override replaces the builtin pack wholesale; document this
   loudly, it is the one migration footgun).
9. Gate with the eval suite (skill activation, autopilot, worker
   scenarios) — the capabilities migration is already the active eval
   churn area, so run the affected scenarios before/after.

## Edge cases & decisions

- **Asymmetry is deliberate:** project server functions stay
  auto-registered (backcompat); only builtin ones are capability-gated.
  A future `settings: server_functions_activation: capability` opt-in
  could make projects fully pack-gated; out of scope here.
- **Override granularity is the whole pack.** Overriding a builtin
  capability YAML replaces its `server_functions:` list too (see §8).
  This is the price of override-by-name simplicity; `extends:` /
  `exclude_tools:` subsetting (already on the follow-up list) would
  soften it for tools and server functions alike.
- **Security posture:** unchanged in kind — server functions already
  execute host-side with service privileges; this narrows exposure, since
  packs an agent doesn't list are no longer registered at all. The
  platform overlay's `security:` section applies as before.
- **Voice/realtime and headless runs:** registration path is identical
  (service init); no special-casing.

## Open questions

1. Should `agent_tools`-style *inline* capability specs in `project.yml`
   support `server_functions:` too? (Proposed: yes — same parser, free.)
2. Do the `bj-*` compatibility aliases move upstream or stay an
   omni-code override? (Proposed: upstream, they're two lines and the
   TUI depends on them.)
3. Warning vs error for duplicate SF names across two active
   capabilities? (Proposed: warning, matching duplicate-tool behavior.)
