# Worker orchestration upstreaming: pluggable isolation

Status: IMPLEMENTED 2026-07-17 (working tree). Open questions were
resolved per the recommendations below, with one deviation: the
framework default `spec_loader` raises a clear "call set_worker_defaults"
error instead of deriving a spec from the entrypoint agent — deriving
was speculative machinery; revisit if a second product needs it.

## Problem

`WorkerManager` (spawn/wait/close/resume/message + on_complete) already
lives in the framework (`omniagents/rpc/agents/workers.py`) and is cleanly
parametrized by `spec_loader`/`on_complete`. But the product-side
`tools/worker_tools.py` in omni-code tangles three concerns:

1. **Generic worker lifecycle tools** — `spawn_worker`, `wait_for_workers`,
   `get_worker_status`, `send_worker_message`, `close_worker`,
   `resume_worker`, UI snapshot broadcasting. Domain-agnostic; belongs in
   the framework next to the task-list and bash-jobs builtins.
2. **Git-worktree isolation** — `_create_worker_worktree*`,
   `_remove_worker_worktree`, dirty/ready checks, branch naming
   (`worker/<parent>-worker-<id>`), `cp -a` fallbacks. Coding-specific
   mechanics, but the *concept* (give each worker an isolated copy of the
   workspace) is general.
3. **omni-code project/source/model resolution** —
   `omni_code.agent_workspace_state` (sources, manifest root, mounts),
   `omni_code.models` model resolution, pinned `project_slug="omni_code"`,
   worker agent YAML path. Pure product concerns.

## Proposed seam: `IsolationStrategy`

New module `omniagents/core/workers/isolation.py`:

```python
@dataclass
class WorkerWorkspace:
    root: str                    # workspace_root the worker runs in
    metadata: dict[str, Any]     # strategy-private (branch name, source map, …)

class IsolationStrategy(Protocol):
    name: str

    async def prepare(
        self,
        *,
        worker_id: str,
        parent_workspace_root: str,
        allow_dirty: bool = False,
    ) -> WorkerWorkspace: ...

    async def cleanup(self, ws: WorkerWorkspace, *, force: bool = False) -> bool:
        """Remove the isolated workspace. Returns False (and leaves it in
        place) when the workspace has un-integrated changes and not force."""

    async def is_dirty(self, ws: WorkerWorkspace) -> bool: ...

def register_isolation_strategy(strategy: IsolationStrategy) -> None: ...
def get_isolation_strategy(name: str) -> IsolationStrategy: ...
```

Framework ships two strategies:

- `none` — worker shares the parent workspace root (no isolation). The
  default when a product registers nothing.
- `copy` — `cp -a`-style copy under `await scratch_dir("workers")`,
  workspace-abstraction-safe (exec-based, like worktrees today). Works for
  any directory workspace, no git required.

omni-code registers `worktree` (its current git-worktree logic, including
the `agent_workspace_state` multi-source handling) and sets it as its
default via the provider seam below. `spawn_worker(isolation=...)` keeps
accepting a strategy name; unknown names error with the registered list.

## Worker spec/config provider

The lifted `spawn_worker` must not import `omni_code.*`. Following the
`set_skill_roots_provider` pattern:

```python
# omniagents/core/workers/config.py
@dataclass
class WorkerDefaults:
    spec_loader: Callable[..., AgentSpec]   # builds the worker AgentSpec
    default_isolation: str = "none"
    history_loader: Callable | None = None  # for resume_worker

def set_worker_defaults(defaults: WorkerDefaults) -> None: ...
def worker_defaults() -> WorkerDefaults: ...
```

Framework default `spec_loader`: derive a headless sub-agent spec from the
current project's entrypoint agent (same capabilities minus UI-coupled
packs, `use_safe_agent=False`, bounded `max_turns`). omni-code overrides
with its `omni_agents/worker/agent.yml` + model resolution +
`project_slug` pinning.

## What moves, what stays

| Piece | Destination |
|---|---|
| lifecycle tools (7) + snapshot broadcast | `omniagents/builtin/tools/worker_tools.py` |
| `workers.yml` capability pack | builtin (instructions rewritten domain-neutral; the current text is heavily coding-flavored) |
| `none` + `copy` strategies | `omniagents/core/workers/isolation.py` |
| worktree strategy (git + sources/manifest) | omni-code (registered at startup) |
| worker agent YAML, model resolution, slugs | omni-code (`WorkerDefaults` provider) |
| `server_functions/workers.py` UI panel | omni-code (thin, imports framework snapshot helpers) |

## Open questions for review

1. Should `WorkerManager` itself move from `omniagents/rpc/agents/` into
   `omniagents/core/workers/` as part of this (rpc → core is a public
   import-path change), or stay put with `core/workers` importing it?
   Recommendation: stay put for now; re-export from `core/workers`.
2. `task_scope` injection: `spawn_worker` sets the child's `task_scope` to
   the worker id so each worker gets a private task list. Codify this in
   the framework run-context contract (one sentence in
   `core/tools/run_context.py` docs) — already half-done.
3. Whimsical worker names (`pip`, `blix`, …): keep in the framework (it's
   harmless, product-neutral fun) or make it a provider knob?
   Recommendation: keep, allow override via `WorkerDefaults.namer`.
4. The worktree strategy's `allow_dirty` semantics and the
   `cleanup_worker_worktree` tool: `cleanup` maps 1:1 onto
   `IsolationStrategy.cleanup(force=...)`; the tool stays generic.
