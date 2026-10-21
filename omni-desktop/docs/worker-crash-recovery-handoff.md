# Worker crash recovery: findings and implementation handoff

Date: 2026-10-16

## User objective

Workers should survive an agent runtime crash in the practical sense: after
restart, the parent can discover the same workers, inspect their recorded work,
and continue them using their existing conversations and workspaces. A crash
must not turn an ongoing multi-worker task into an unrecoverable collection of
orphaned conversations and worktrees.

This note records a read-only source assessment requested in a side conversation.
At the time of that assessment, no recovery implementation or crash-injection tests had been performed. See the implementation update below for the subsequent work. The user
asked to save this so the main agent can pick it up later. This is separate from
the provider-selection and tool-output work already in progress; preserve those
changes.

## Assessment

Omniagents has useful conversation persistence and run-ownership recovery, but
the ordinary `spawn_worker` / `resume_worker` path does not provide cold-start
worker recovery. Its resume feature depends on a worker handle in the same live
process. Persisting a worker's transcript is not equivalent to persisting its
orchestration lifecycle.

Scope: the conventional WorkerManager and worker tools. Do not assume these
findings establish the behavior of every resident/swarm execution path; inspect
those separately when designing shared recovery.

| State                                         | Current behavior found in source                                                                              |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Worker conversation and recorded tool results | Persisted in the parent's conversation namespace                                                              |
| Written files/worktrees                       | May survive if underlying workspace/storage survives; not a guarantee for discarded or ephemeral environments |
| Live worker run and monitor task              | Lost when the hosting process dies                                                                            |
| Worker ID to session/handle mapping           | In-memory dictionary, empty in a new manager                                                                  |
| Isolation/workspace/environment associations  | In-memory registries; no reconstruction path found in worker code                                             |
| Resume by worker ID after process restart     | Lookup fails without the previous in-memory handle                                                            |
| Completion notification                       | Durable after enqueue, but completion-to-enqueue delivery is best-effort                                      |

## Evidence and failure modes

Paths below refer to the sibling `../omniagents` repository. Line numbers are
navigation hints from the assessment, not stable identifiers; use the symbols.

1. **Registry is not reconstructed.**
   `omniagents/rpc/agents/workers.py`, `WorkerManager.__init__` (around line 120),
   initializes `_workers = {}`. `spawn` registers a handle after `_create_worker`
   returns. `resume` (around line 522) requires a handle from that dictionary.
   No startup reconstruction path was found.

2. **The public resume tool has the same dependency.**
   `omniagents/builtin/tools/worker_tools.py`, `resume_worker` (around line 1081),
   immediately returns `not_found` when `_workers()` has no entry. Its description
   promises conversation-history restoration, but that restoration only happens
   after the live handle lookup succeeds.

3. **Workspace and identity metadata are process-local.**
   In `worker_tools.py` (around line 151), `_MANAGER`, `_WORKER_WORKSPACES`,
   `_WORKER_ISOLATION`, `_WORKER_ENVIRONMENTS`, and `_WORKER_META` are in-memory
   state. `workers_snapshot` enumerates live handles. Worktrees can remain on
   disk while their runtime associations disappear. Restoring only the transcript
   would not restore the worker's execution environment.

4. **Underlying session persistence is a useful foundation.**
   `workers.py`, `_create_worker`, stamps the parent's project/agent namespace
   onto the worker spec and passes `worker_id` / `parent_session_id` in context.
   `core/session/manager.py` loads saved history and session metadata. Determine
   which existing records can support migration/reconstruction before adding
   duplicate persistence.

5. **Existing run recovery continues a known session, not a worker graph.**
   `omniagents/core/runtime/ownership.py` uses durable heartbeat reservations
   (`LEASE_TTL = 30` seconds, heartbeat interval 5 seconds at assessment time).
   On a subsequent run, expired ownership can be taken over.
   `AgentService._close_interrupted_run` in `core/agents/service.py` (around
   line 6484) closes interrupted turns. Session history repair marks unfinished
   function calls as outcome unknown and warns against unsafe repetition.
   This does not discover workers or automatically recreate their handles.

6. **Completion delivery has a crash/failure window.**
   `worker_tools.py`, `_on_worker_complete` (around line 351), invokes
   `service.enqueue_notification` with source `worker.complete`, but catches
   exceptions without a durable retry obligation. `WorkerManager._finalize`
   also treats callback failure as best-effort. `AgentService.enqueue_notification`
   (around line 6887) durably buffers a notification once reached. A crash after
   recording worker completion but before durable enqueue can leave the parent
   uninformed. Avoid describing the notification queue itself as memory-only.

7. **Current resume coverage does not demonstrate crash recovery.**
   `tests/unit/rpc/agents/test_workers.py`,
   `test_worker_manager_send_close_and_resume` (around line 127), creates,
   closes, and resumes through the same manager instance. Other inspected
   resume tests check context, environment selection, configuration, and model
   propagation; they do not prove a fresh process can recover the worker.

Concrete failure sequence: parent spawns A and B; workers record progress;
hosting process dies; replacement process starts with an empty worker registry;
parent attempts to inspect/resume A or B and receives not-found despite saved
worker conversations and possibly surviving files.

## Proposed implementation direction

1. Persist worker lifecycle records with stable worker ID, parent and child
   session IDs, namespace/ownership, original task, agent/spec identity,
   lifecycle state, and workspace/isolation identity. Persist intent before
   dispatch and reconcile partially completed spawns. Do not try to serialize
   live Python objects or callbacks; define reconstructible configuration.
2. Reconcile records on restart or parent-session load. Distinguish live remote
   ownership from expired runs, restore worker discovery/status, and reconstruct
   the correct environment before permitting continuation. Never silently resume
   in the parent's directory when the worker's isolated workspace is unavailable.
3. Make completion and its pending parent notification part of one durable
   transition (or an equivalent recoverable outbox). Retry with stable identity
   so a crash cannot silently lose delivery or cause duplicate parent processing.
4. Expose honest recovered states to the agent and launcher: interrupted,
   resumable, blocked by missing environment, completed, and delivery pending
   as appropriate. Decide explicitly whether recovery is automatic or requires
   a follow-up instruction; do not equate registry restoration with autorun.
5. Preserve tool-action uncertainty. Recover from recorded state; do not blindly
   replay unfinished external actions. Idempotency/reconciliation is needed
   where an action may have succeeded before its result was persisted.

The intended guarantee is continuation from the last recorded state with
interrupted actions identified, not resumption of the exact suspended Python
coroutine, model request, or unrecorded token stream.

## Acceptance tests

Use persistent temporary test storage and a genuinely fresh process/manager.
Include abrupt process death, not only graceful cleanup or a UI reconnect.

- Spawn multiple workers, kill the hosting process mid-work, restart, and verify
  the parent discovers the same IDs and child sessions with recorded history.
- Resume a recovered worker and verify it continues the original conversation
  and workspace, without creating a replacement worker or losing prior edits.
- Repeat for shared workspace and isolated worktree execution; verify missing
  or discarded environments produce a clear blocked state rather than fallback.
- Kill during spawn at the boundaries between durable intent, child-session
  creation, environment provisioning, and acknowledgement. Reconciliation must
  avoid duplicate children and identify partial provisioning.
- Kill after a tool dispatch and before its result is committed. Recovery must
  report uncertain outcome and must not automatically repeat that action.
- Kill after worker completion is recorded but before parent notification enqueue,
  then after enqueue but before delivery acknowledgement. Verify eventual delivery
  and idempotent parent processing.
- Recover two parents/users concurrently and verify ownership and isolation;
  one parent must not adopt another's worker, history, or environment.
- Verify the launcher worker panel reflects recovered records after restart,
  including unfinished and already-completed workers.

## Coordination

Most lifecycle work belongs in omniagents. Omni Code should supply declarative
product wiring/instructions where needed; launcher changes should expose recovery
state and exercise the user-visible restart flow. Follow the repositories' release
order and keep this fix separate from unrelated in-flight changes.

## Implementation update — 2026-10-16

The worker recovery path now uses the existing transactional conversation store
(SQLite and PostgreSQL). `core/workers/durable.py` stores a versioned worker
journal in the parent's command inbox, so terminal state and the obligation to
report completion commit together. It does not introduce another transcript or
copy tool output into the journal.

- Admission allocates the worker and child conversation identities before
  provisioning or dispatch. Stable operation IDs deduplicate admission and resume.
- Records preserve the original task, owner, generation, reconstruction recipe,
  workspace/environment identity, provisioning checkpoints, lifecycle and report
  state. Credentials and Python closures are not serialized.
- Parent-session loading reconciles records with canonical child turns, dispatch
  receipts and existing run leases. A watcher waits for old leases to expire.
  Rediscovery is automatic; continuation requires explicit resume.
- Resume uses the existing child conversation and original environment. Host
  directories must retain their recorded identity. Other environments require
  the original backend and generation; unavailable environments block recovery.
  Isolated provisioning currently requires a host environment; a non-host request
  is rejected before preparing a workspace rather than converted to host execution.
- Versioned recipes reconstruct project workers, named single workers and swarm
  members. Roles, membership, mailboxes and cleanup state survive restart.
  Unsupported recipe versions or missing definitions block continuation.
- Completion reports retain a per-generation identity through the existing
  notification queue, including admission and acknowledgement. Dismissed reports
  are distinguished from delivery. This is deduplication of runtime admission,
  not a claim that arbitrary external effects run exactly once.
- Existing tool dispatch claims and history repair remain authoritative. Neither
  worker reconciliation nor resume automatically retries an unfinished tool.
- Legacy worker conversations with a verified owner and persisted parent link
  are imported as blocked history. Missing workspace/configuration information
  is not guessed. Unlinked or unowned historical conversations are not adopted.
- Launcher exposes interrupted/blocked states, explicit Resume/Retry recovery,
  report status, and refreshed transcripts after the worker advances. Omni Code
  instructs agents to inspect recorded work and workspace changes before resuming.

The durability boundary is the configured conversation store and surviving
workspace/backend. Recovery cannot restore deleted worktrees, a discarded remote
backend, unrecorded model tokens, or a suspended Python/JavaScript stack. Failed
or partial provisioning remains inspectable and blocked instead of rerunning
preparation automatically. Worker history is bounded to 512 records per parent.

### Verification

Permanent coverage lives in `omniagents/tests/unit/core/workers/test_durable.py`
and `launcher/tests/e2e/specs/worker-recovery.spec.ts`. The storage tests run
against both SQLite and an isolated PostgreSQL instance. Fresh-process SIGKILL
cases cover admission/provisioning/dispatch, completion, report admission,
acknowledgement and a tool effect whose result was never committed. Additional
checks cover competing owners, parent/namespace separation, legacy import,
worktree edits, unavailable environments, adapter restoration and recipe versions.

The Playwright story kills the launcher/runtime process tree, reopens the parent,
finds its interrupted worker, explicitly resumes it, reads the original workspace
file and verifies the resumed response in the visible transcript. It runs with
normal tool approval enabled in browser/server and Electron modes. Proof artifacts
are generated by:

```bash
OMNI_CLI_PATH=/home/emm/Omni/Workspace/omni-code/.venv/bin/omni \
VISUAL_PROOF=1 E2E_CHROMIUM_EXECUTABLE=/usr/bin/chromium \
E2E_ELECTRON_X11=1 DISPLAY=:0 \
npx playwright test tests/e2e/specs/worker-recovery.spec.ts \
  --project=server-local --project=electron-local
```

Build both bundles first with `npm run build` and `npm run build:server`.
Report: `artifacts/playwright-proof-report/index.html`.
Screenshots, traces and videos: `artifacts/playwright-proof-results/`.

Final validation: the recovery/regression suite passed **167 tests**, with targeted
follow-up worker tests also passing. Launcher passed **17 UI/session tests**, and
Omni Code passed its **2 capability integration tests**. Both visual-proof projects
passed after genuine process-tree kills, including a visible delivered completion
report. Python typing, TypeScript, targeted ESLint, whitespace checks, and both
production builds passed. Desktop/browser execution was verified on Linux; these
runs do not constitute Windows/macOS runtime validation.

## Unified worker communication update — 2026-10-17

New delegation now uses one worker lifecycle across regular chats and residents.
The optional `name`, `role` and `peer_communication` spawn arguments replace the
separate swarm setup. Opted-in children get host-bound discovery and messaging
for opted-in siblings in the same parent conversation. Messages persist before
delivery, deduplicate on retry, and do not start idle recipients.

The `swarms` capability name is retained as an alias. Existing swarm recipes
still reconstruct their original member tools and membership boundary; their
historical mail is available through `get_worker_status`. Legacy records are
not silently widened to the new parent-scoped communication boundary.
See `../omniagents/docs/SWARMS.md` for the current contract.

Regression coverage includes persisted mail after reopening SQLite, scoped peer
visibility, sender identity, concurrent name admission, message deduplication,
real recipient-queue delivery without an implicit run, limits, recipe recovery,
and legacy mailbox inspection. The framework recovery suites and product pack
resolution tests pass; PostgreSQL variants were skipped in this run because no
test database URL was configured. Launcher resident tests (77), targeted lint,
type checks, and both production builds pass.

The permanent crash proof now spawns an opted-in named worker and, after a real
process-tree kill, calls its reconstructed `worker_context` and
`worker_message` tools before completing the original assignment.

Proof command:

```bash
OMNI_CLI_PATH=/home/emm/Omni/Workspace/omni-code/.venv/bin/omni VISUAL_PROOF=1 E2E_CHROMIUM_EXECUTABLE=/usr/bin/chromium E2E_ELECTRON_X11=1 DISPLAY=:0 npx playwright test tests/e2e/specs/worker-recovery.spec.ts --project=server-local --project=electron-local
```

Artifacts: `artifacts/playwright-proof-report/index.html` and
`artifacts/playwright-proof-results/`.

Both proof projects passed (server-local and electron-local, 2 tests).

### Explicit worker-to-parent messaging

New project workers now receive `worker_message` and `worker_context` even
without peer communication. `worker_message(recipient="owner", message=...)`
atomically saves the mailbox entry and enqueues a parent notification. Retried
tool calls do not enqueue duplicate notifications. Normal worker turn endings
retain their transcript/result but do not send an automatic completion message.
Errors still generate automatic failure reports.

The parent keeps `send_worker_message(worker_id, message)`. Messages addressed
to workers now enqueue a turn and start idle workers automatically. Busy
workers process messages in arrival order after the current run. Closing a
worker cancels active and queued work and rejects future messages. Explicit
resume remains available for interrupted-run recovery.

Recipes from before this change retain their automatic end-of-run reporting,
so a recovered worker that lacks the new reporting instructions does not
silently stop communicating. New recipes persist the explicit-message policy.

Additional tests cover silent normal turn endings, automatic failure reports,
parent messaging without peer opt-in, and transaction rollback across mailbox
admission and parent notification. The permanent crash proof now checks that
the worker's explicit message reaches a parent model request after recovery,
rather than checking delivery of an automatic completion report.

The explicit-message proof also exposed a forwarded-approval lifetime bug:
a parent turn finishing could mark the worker's still-pending approval failed
in the canonical transcript. Worker identity is now preserved on those approval
items, and the parent run-end sweep leaves them for the worker's resolution
event. Existing runtime reconciliation still disables controls whose waiter
was lost. Tool and MCP approval regression tests cover this boundary.

Validation for explicit messaging: 53 worker/recovery tests and 103
recorder/runtime-control/worker-approval tests passed; 10 PostgreSQL variants
were skipped without a configured database. Product capability tests and type
checks passed. The updated browser and Electron visual proofs both passed
(2 tests), using the command and artifact paths above.

## Automatic message dispatch

The public enqueue API no longer accepts `trigger_run` / `triggerRun`.
Legacy queued records are decoded without that flag; already recorded effects
are not replayed. Worker monitoring remains attached across successive runs,
including runs started by ordinary queued messages. Reattachment preserves the
child conversation and workspace. Explicit worker messages notify the parent;
ordinary assistant responses remain local to the worker transcript.

Validation: 84 framework tests passed, with 10 PostgreSQL variants skipped.
The permanent crash recovery proof also covers a parent-to-idle-worker message
and the worker's explicit reply, without a resume operation for that follow-up.
Both server-local and electron-local passed using:

```bash
OMNI_CLI_PATH=/home/emm/Omni/Workspace/omni-code/.venv/bin/omni VISUAL_PROOF=1 E2E_CHROMIUM_EXECUTABLE=/usr/bin/chromium E2E_ELECTRON_X11=1 DISPLAY=:0 npx playwright test tests/e2e/specs/worker-recovery.spec.ts --project=server-local --project=electron-local
```

Proof report: `artifacts/playwright-proof-report/index.html`.
Screenshots, videos and traces: `artifacts/playwright-proof-results/`.

Protocol artifacts are synced from the committed framework (omniagents
`556c87ed`) and pass release source verification. The sync script's
`--working-tree` flag is for development snapshots only; release verification
rejects them.
