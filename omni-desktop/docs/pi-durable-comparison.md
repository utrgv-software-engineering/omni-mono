# Pi Durable and Omniagents worker recovery

Date: 2026-10-16

Pi Durable persists the execution plan as well as the conversation. Regular Pi
persists conversations and can load them again, but its ordinary agent loop does
not reconstruct a durable graph of unfinished work. Omniagents already has
conversation persistence and run ownership leases; its conventional workers
still depend on process-local handles. The highest-value change for Omni is to
persist worker identity, lifecycle, environment associations, and pending parent
notifications, then reconcile these records after restart.

This is a source assessment, not a tested crash-recovery certification. It extends
[the worker recovery handoff](worker-crash-recovery-handoff.md), which documents
the Omni findings and acceptance tests. No application implementation was changed.

## Projects and versions

The upstream project found through internet search is
[earendil-works/pi](https://github.com/earendil-works/pi). Its monorepo contains
both regular Pi (`packages/coding-agent` and `packages/agent`) and
`@earendil-works/pi-durable` (`packages/durable`). One clone covers both; this
assessment does not substitute the similarly named Rust port or an application
built on top of Pi.

- Local clone: `/tmp/omni-pi-durability-research`.
- Pi commit: `8efd314fa3d3232ce197d8d7a6a5660530b9d83e`.
- Omniagents checkout: `/home/emm/Omni/Workspace/omniagents`, HEAD
  `89bb1cea6117c8662e88715a52523d05aabc7871`, with ongoing local changes.
- Pi Durable describes its API as experimental. Treat it as a source of design
  patterns, not a reason to replace Omni's Python runtime wholesale.

The clone is temporary and may be cleaned up. The source links below pin the
reviewed revision so the research remains usable without it.

## What durability means here

After a process dies, a replacement process should know which work was accepted,
which worker owns it, what was committed, what remains pending, and which external
actions have an uncertain outcome. Recovery requires surviving storage and the
code/configuration needed to interpret it. It does not restore a suspended stack,
recover every uncommitted streamed token, or guarantee an external action happens
exactly once.

| Capability | Regular Pi core | Pi Durable | Omniagents conventional workers |
| --- | --- | --- | --- |
| Saved conversation | JSONL session tree | Immutable entries in storage | Persisted sessions/history |
| Resume mechanism | Load transcript and continue agent interaction | Reopen task checkpoints and schedule pending phases | Warm resume reconstructs agent from a live worker handle |
| Pending input | Agent steering/follow-up queues are in memory | Persisted submissions/inbox with request identities | Existing durable notification infrastructure; worker lifecycle needs separate recovery |
| Child identity and relationships | Application/extension responsibility | Persisted task/conversation ownership; subagent examples build on it | Worker registry and workspace sidecars are in memory |
| Interrupted tool | No equivalent durable task policy in the ordinary loop inspected | Stored intent plus explicit safe/unsafe replay policy | Existing interrupted-run/history repair preserves uncertainty |
| Child result delivery | Application/extension responsibility | Example uses a persistent reporter task and idempotent submission | Completion callback can fail before durable enqueue without a retry obligation |
| Multiple runtime owners | Not established by this assessment | Storage explicitly assumes one process; no cross-process locking | Existing durable run leases help prevent concurrent ownership |

The Pi column describes its standard runtime, not every third-party extension.
The Omni column describes `WorkerManager` and the worker tools, not all resident
or swarm paths.

## Regular Pi saves conversation history

`SessionManager` appends session entries to JSONL. A new session is flushed once
it contains a user or assistant message, so even the initial user prompt can
survive an incomplete first answer. `AgentSession` persists ordinary messages on
`message_end`. These are useful persistence guarantees; calling regular Pi
entirely non-durable would be misleading.

The distinction is execution state. The agent's current run, abort controller,
and steering/follow-up queues are live objects. `Agent.continue()` continues from
the current transcript; it does not load a stored task checkpoint or rediscover
an unfinished worker graph. Persisting an assistant tool-call message alone does
not establish whether its external effect happened before a crash.

Sources: [session persistence](https://github.com/earendil-works/pi/blob/8efd314fa3d3232ce197d8d7a6a5660530b9d83e/packages/coding-agent/src/core/session-manager.ts),
[message persistence](https://github.com/earendil-works/pi/blob/8efd314fa3d3232ce197d8d7a6a5660530b9d83e/packages/coding-agent/src/core/agent-session.ts),
[ordinary agent loop](https://github.com/earendil-works/pi/blob/8efd314fa3d3232ce197d8d7a6a5660530b9d83e/packages/agent/src/agent.ts).

## What Pi Durable adds

### Persistent execution checkpoints

Tasks have stored input, definition/version, ownership, status, and a checkpoint
identifying the next phase. The built-in generation task coordinates model
requests and tool tasks. Waiting for child tasks is represented in storage rather
than depending solely on an in-memory promise.

On open, the scheduler scans live records and changes surviving running tasks to
pending while retaining their checkpoints. It reconciles cancellation and held
outcomes. `resume()` enables dispatch; submitting or waiting also starts
scheduling. Reopening storage alone does not imply work has started executing.

Definitions remain application code: the host must reinstall the relevant
extensions after restart. Versioned tasks and migration support allow the runtime
to handle code changes explicitly rather than interpreting old checkpoints under
arbitrary new code.

Sources: [scheduler](https://github.com/earendil-works/pi/blob/8efd314fa3d3232ce197d8d7a6a5660530b9d83e/packages/durable/src/harness/scheduler.ts),
[generation task](https://github.com/earendil-works/pi/blob/8efd314fa3d3232ce197d8d7a6a5660530b9d83e/packages/durable/src/harness/generation.ts),
[recovery and migration tests](https://github.com/earendil-works/pi/blob/8efd314fa3d3232ce197d8d7a6a5660530b9d83e/packages/durable/test/harness-tasks-recovery.test.ts).

### Atomic state transitions and retriable admission

A commit can include transcript entries, documents, and task changes together.
The session commits storage before adopting and publishing the changes. That
allows an application to create a child, record its identity, and schedule the
work that will supervise it as one durable decision.

Submissions support `requestId`: retrying admission with the same identity finds
the existing submission. This handles the common failure where storage accepted
the request but the caller never received confirmation. It is admission
deduplication, not universal deduplication of external side effects.

Sources: [session commit implementation](https://github.com/earendil-works/pi/blob/8efd314fa3d3232ce197d8d7a6a5660530b9d83e/packages/durable/src/session/session.ts),
[submission implementation](https://github.com/earendil-works/pi/blob/8efd314fa3d3232ce197d8d7a6a5660530b9d83e/packages/durable/src/harness/submissions.ts).

### Explicit treatment of interrupted tools

Before executing a tool, Pi Durable commits the final arguments and replay
policy. Recovery reruns the tool only if both the saved policy and its current
definition say replay is safe. Otherwise it settles with an interrupted error,
including committed output, because the call may have partially run.

A tool marked safe still needs appropriate idempotency or reconciliation for
external effects. The recovery tests illustrate this with a fake transfer
service that deduplicates using a stable task-derived request key. Neither a
database checkpoint nor a safe label makes an arbitrary shell command exactly
once. Saving a memo before an effect can avoid duplicates while losing the
effect if the process dies between the two operations.

Source: [tool intent and recovery implementation](https://github.com/earendil-works/pi/blob/8efd314fa3d3232ce197d8d7a6a5660530b9d83e/packages/durable/src/harness/tool.ts).

### Persistent worker supervision and reporting

The most relevant reference is the background subagent example. It implements
application policy using the durable primitives; it is not a promise that any
arbitrary Pi subagent extension becomes durable automatically.

The example stores a mapping from subagent name to child conversation ID, tracks
reported answer IDs, and creates a persistent reporter task for each message.
Spawning commits the child conversation, its ownership, registry entry, and
reporter together. The reporter has two phases:

1. Submit to the child using a stable request ID, wait for the answer, then
   atomically save both the reporting decision and its next checkpoint.
2. Submit the report to the parent using another stable request ID, then mark
   the reporter complete.

If the process dies after report admission but before reporter completion, the
retry finds the same parent submission. The obligation to report survives; it
is not only a callback attached to a live child. Reports remain subject to the
example's explicit cancellation semantics, including withdrawal of queued input.

The example intentionally marks its combined subagent tool unsafe to replay:
repeating a stop action could stop newer work. Persistent child supervision can
therefore coexist with a caller receiving an interrupted-tool result.

Source: [background subagent example](https://github.com/earendil-works/pi/blob/8efd314fa3d3232ce197d8d7a6a5660530b9d83e/packages/durable/test/examples/23-subagent-background.ts).
Its restart demonstration closes and reopens a harness; that is not a separate
process killed with SIGKILL. Omni should add genuine abrupt-death tests.

## Limits to preserve in the comparison

Pi Durable's SQLite adapter uses WAL and synchronous NORMAL: its documented
guarantee covers process crashes, while recent commits may be lost on power or
host failure. JSONL has optional fsync and retains history in memory; MemoryStorage
is not persistent. Storage selection and deployment matter as much as API names.

Its storage contract assumes one process owns a store and provides no cross-process
locking. A restarted desktop can overlap with an old runtime, so Omni should keep
its ownership leases and validate fencing rather than copy that assumption.

Durable conversations cannot by themselves preserve a deleted worktree, vanished
sandbox, credentials, or an external subprocess. Those resources need stable
identities and reconnection/reconciliation rules. Pi Durable's code-mode journal
is a separate, more advanced mechanism for deterministic script replay; it is not
necessary to implement Omni's initial worker recovery.

Source: [Pi Durable storage and recovery contract](https://github.com/earendil-works/pi/blob/8efd314fa3d3232ce197d8d7a6a5660530b9d83e/packages/durable/README.md).

## What Omniagents should adopt

### First make workers discoverable after restart

Persist a worker lifecycle record before dispatch. Include stable worker and
parent/child session IDs, owner/namespace, original task, versioned reconstructible
agent configuration, environment/worktree identity, lifecycle state, and timestamps.
Persist references to configuration and credentials, not live Python callbacks or
secret values. A `configure_spec` closure cannot serve as a restart recipe.

On startup or parent-session load, rebuild discovery from these records and
reconcile each worker with its session and environment. Claim expired ownership
before continuation. Surface missing environments as blocked; never silently
resume an isolated worker in a different directory. Preserve completed workers
and their results too, so a crash does not erase useful status history.

This directly addresses `WorkerManager.__init__` initializing `_workers = {}`,
`WorkerManager.resume` requiring a live handle, and `resume_worker` looking up that
same registry. The detailed source pointers are in the existing handoff.

### Then make dispatch and completion recoverable

Use persisted phases for the boundaries where work can otherwise disappear:
accepted, provisioning, ready, running, interrupted, and terminal. Exact names are
an implementation choice. Give spawn/dispatch a stable operation identity and
reconcile partial provisioning, rather than creating another child after every
uncertain acknowledgement.

Record worker completion and an obligation to notify the parent in one transaction
where possible. Otherwise use a recoverable reconciliation rule that derives the
missing obligation from durable terminal state. Deliver through Omni's existing
durable notification queue with a stable completion identity, and deduplicate
parent admission/processing. Include a run or completion generation in that
identity: the same worker can be resumed and finish more than once.

This is the Pi reporter pattern adapted to Omni. The present queue is already
durable after enqueue; the gap is that `_on_worker_complete` can fail before
enqueue and swallow the exception without leaving a durable retry obligation.

### Preserve uncertainty and ownership

Keep interrupted-tool history repair. Introduce replay policies only for tools
whose implementations satisfy them, defaulting to unsafe for unknown effects.
Use external idempotency keys or inspect external state where supported. Do not
make a crash automatically repeat every unfinished command.

Keep existing run leases and ensure stale owners cannot commit newer lifecycle
state or duplicate terminal delivery. Define restart behavior separately from
explicit user cancellation: stopping the host should leave recoverable work,
whereas cancelling a worker should durably record that decision and its scope.

### Expose and test the contract

Recovery should first restore the same worker IDs, histories, results, and
environment mappings. Automatic continuation is a separate product decision;
an interrupted worker can be discoverable and safely resumable without silently
rerunning it. The launcher should distinguish interrupted, resuming, blocked,
completed, and pending-report states where they matter to the user.

Implement the abrupt-death acceptance matrix in the
[existing handoff](worker-crash-recovery-handoff.md): kill during spawn, model
generation, tool execution, completion, enqueue, and acknowledgement; restart
in a fresh process; verify identity, workspaces, eventual reporting, deduplication,
and competing-owner behavior. Add cancellation and task-version migration cases.

Most changes belong in omniagents. Omni Code should supply reconstructible
worker configuration and product recovery policy. Launcher should display the
restored lifecycle and verify the restart flow from the user's perspective.
Adopt these patterns incrementally; replacing the entire agent loop with a
general durable workflow engine is not a prerequisite for fixing lost workers.


## Implementation follow-through

The incremental worker journal, reconstruction, completion-report and explicit
recovery work described above is implemented across omniagents, Omni Code and
launcher. See the [implementation update and verification contract](worker-crash-recovery-handoff.md#implementation-update--2026-10-16).
The implementation reuses Omni's canonical conversations, command inbox and tool
claims. It does not add general workflow replay or promise automatic retry of
arbitrary external effects.
