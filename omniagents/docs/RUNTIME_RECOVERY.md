# Runtime ownership and recovery

## Staged work — September 7, 2026

### 1. Exclusive run ownership: implemented, conservative

`core/runtime/ownership.py` reserves one thread in the durable command inbox
before agent preparation or model-context repair. SQLite and PostgreSQL use the
existing transactional inbox lock. An opaque owner, run ID and increasing
generation identify the reservation. Another process's locally idle Session is
not authority to start a run. Queued dispatch and direct starts share the same
exclusion boundary; only the exact queued dispatcher may start its claimed work.

Preparation failure/cancellation releases its reservation. A published worker
owns cleanup, including cancellation before coroutine entry. Release checks the
exact owner/generation and tolerates a lost commit acknowledgment without clearing
another owner's reservation. SDK model requests and local tool starts check the
current ownership token. Custom hooks remain delegated.

**This is not a renewable lease or an external executor fencing protocol.** There
is no expiry, force-release or takeover endpoint. Process death keeps the durable
reservation unresolved. A missing local task, terminal UI row, or elapsed time
does not prove that external actions or another process have stopped. Direct SDK
calls outside AgentService do not acquire this session reservation.

Unstarted queued messages behind an unresolved runtime remain pending and
cancellable, with an explanatory notice. Already-dispatched uncertain messages
remain non-cancellable and non-replayed. This intentionally supersedes the older
crash test's assumption that a replacement runtime could immediately drain the
queue behind a killed run. Completed clean runs still permit ordinary restart.

### 2. Model context reconciliation: partly implemented

Session construction no longer appends synthetic missing tool outputs merely
because someone reads history. After obtaining ownership, a run reloads both raw
history and the saved SDK context, including a deliberately empty context. This
prevents a previously loaded second backend from continuing with stale memory.

New function-call/output history rows carry an internal originating run ID; it is
stripped before provider requests. Missing outputs with exact run/call/tool/input
identity recover the actual completed canonical result. Unknown outputs remain
explicitly unknown, with a warning against repeating the action. Repair does not
execute tools. Repeated provider call IDs across recorded runs receive distinct
synthetic identities in model input so pair cleanup cannot discard a later result.
Persisted history is not rewritten by that input-only normalization.

Still open: recovery when **both** raw call and output are absent, durable proof
that SDK context incorporated each canonical result, and a fully atomic recovery
checkpoint spanning context replacement and acknowledgment. Do not reconstruct a
missing provider reasoning chain or undo intentional compaction by guessing.

### 3. Remaining tool families: audited, per-tool recovery still open

The repository's SDK-native factory is `core/tools/hosted.py`:

| Family | Current boundary | Remaining requirement |
| --- | --- | --- |
| Function tools / local MCP adapters | Durable call claim and result; ownership checked before invocation | External effect reconciliation and context acknowledgment |
| Web/file search, code interpreter, image generation, hosted MCP | Ownership checked before model request, not each provider-side effect | Durable provider request/response identity and supported outcome lookup |
| Hosted shell environments | Provider request boundary only | Provider execution/outcome reconciliation |
| Python-supplied computer, local shell, apply-patch and other SDK-native tools | Ownership checked when SDK emits local start hooks; function-call claims not assumed | Family-specific invocation identity, schemas and execution guards |

The YAML factory rejects computer/local-shell/apply-patch configurations that need
Python executors; this does not mean custom agents cannot supply those tools.
Never treat a provider timeout or a cancelled coroutine as proof of no effect.

### 4. Verification

Permanent tests cover competing database reservations, stale generations, lost
release acknowledgments, process death, shared-session services racing during
preparation, cancelled preparation, stale SDK callbacks, queue exclusion, loading
without repair, stale context refresh, deliberate context clearing, exact durable
result recovery and repeated call IDs in model input. Ownership store contracts
run on both SQLite and PostgreSQL.

Launcher proof commands, results and visual artifacts are recorded in the sibling
launcher repository's `docs/chat-adversarial-audit.md`. Azure remains excluded.
All crash tests use disposable state; original historical databases are untouched.

Final results: 1,261 broad backend tests passed (no skips); 97 final focused
tests passed; 124 PostgreSQL domain/history tests passed. Launcher passed 93
targeted tests plus TypeScript/ESLint, five browser/server proofs and two native
Electron proofs. The broad run preceded the final non-function repair guard;
that guard and its regression were included in the final focused suite.
Both production bundles built successfully. Proof screenshots were inspected.
The disposable PostgreSQL container was removed after verification; no user
database was touched and nothing was staged or committed.

## Executor retirement continuation — September 7, 2026

Implemented cooperative executor admission at concrete Workspace mutation,
exec/spawn and ProcessHandle stdin methods. Subclass overrides and cached object
references pass through the same guard; checking only the SDK's tool-start hook
was insufficient for delayed work. A closing run rejects new operations immediately
but retains its durable reservation until admitted operations settle. Host
filesystem writes using `to_thread` are awaited through repeated cancellation;
cancelling an await is not proof that its underlying write stopped.

HostWorkspace now owns a separate POSIX process group for each exec/spawn. Stop,
timeout and explicit termination signal that group, escalate if necessary, and
wait for output draining. Cancellation while asyncio is publishing the process
handle also retrieves and cleans up the child. Background wait deadlines cover
both leader exit and inherited output pipes, and a timed-out wait does not cancel
the shared reader. Background jobs deliberately may outlive a clean run; stale
tasks cannot send new stdin commands, while a current owner can manage the job.

An additional safe recovery path retries this process's own failed database
release only after its worker is done, its ownership is closing, and admitted I/O
has settled. Foreign or restarted runtime owners are never released this way.
This fixes a recoverable database-outage wedge without adding a force-takeover API.

The real-process tests verify parent/child retirement, SIGTERM-resistant children,
spawn-publication cancellation, output-drain deadlines, delayed writes, stale
stdin, and rejection before a sandbox RPC is sent. The permanent launcher story
verifies actual child PIDs stop, the owner chat can start a fresh turn, and another
tile's pending approval remains intact.

**Remaining boundary:** process groups are not cgroups/containers or a durable
remote executor. A child can deliberately detach, a killed Python parent cannot
run cleanup, and an already-sent remote request may have taken effect. Operations
outside the cooperative Workspace/SDK contract are not intercepted. These changes
do not authorize automatic replay of unknown effects or recover missing model
context when both raw call and output are absent. The prerequisites below remain.

Verification: 1,566 expanded backend tests passed before the final stdin guard;
the final workspace/job/lifecycle suite passed 150 tests including that guard and
sandbox-dispatch refusal. PostgreSQL passed 125 domain/history tests. All five
browser/server and three native Electron proofs passed, including real process
retirement and independent tile approvals. The native screenshot was inspected.
TypeScript, targeted ESLint/Prettier, Python formatting and diff checks passed.
The disposable PostgreSQL container was removed; no user data was removed.

## Remaining prerequisites for safe operator recovery

### Linux command supervision — September 7, 2026

Implemented a separate, stdlib-only supervisor for Linux HostWorkspace exec and
spawn. It has its own process session and owns the command's process group. The
backend alone holds the write end of a lifetime pipe; commands cannot inherit it.
Backend death closes the pipe and the supervisor kills the group without relying
on the dead backend's event loop or cleanup callbacks. A Linux subreaper adopts
ordinary orphan descendants. Completion waits for them, including a child left
behind when its original parent exits. The command leader remains unreaped while
signalling is possible, preventing its process-group ID from being recycled.

Each execution gets a private directory under `scratch_root/executions/` with an
atomically replaced, fsynced `state.json`. The record includes a unique execution
ID, the run owner/generation when present, supervisor/command PIDs, lifecycle and
return status. It does **not** contain command text, environment values, stdin or
output. PID fields are diagnostic only; recovery must never signal a PID obtained
later from one of these records. These records are retained, not automatically
purged or used to authorize takeover. Journal preparation runs off the backend
event loop; subprocess publication remains cancellation-safe.

Missing, malformed or unconfirmed completion evidence blocks release of the
current run, including the same-process release-retry path. An already-closed
lifetime pipe refuses initial dispatch. Spawn errors preserve their OSError
category. Foreground command completion now includes ordinary descendants even
when they close their output streams; use explicit `spawn` for managed jobs that
should outlive a foreground call. Explicit background handles can still outlive
a clean run while the backend lives; they are not automatically reattached after
backend death.

**Limits remain:** this supervisor is Linux-specific. Other platforms retain the
previous cancellation/process-group implementation. Deliberately detached process
groups, supervisor SIGKILL, machine failure, arbitrary same-user code and remote
effects are not contained by this boundary. A detached survivor is explicitly
tested to produce `cleanup_unconfirmed`, not successful retirement. Even confirmed
retirement does not prove that a command had no effects. Interrupted run ownership
therefore stays reserved after restart; no force-release, replay or retry UI was
introduced. A background job whose run was already cleanly released is not a new
exclusive run reservation. Invocation-level outcome/context reconciliation and
stronger executor isolation are still prerequisites for general recovery.

Verification: 1,581 expanded backend tests passed (three existing datetime
deprecation warnings). After the final startup/error-path hardening, 294 focused
checks passed, including all 126 PostgreSQL domain/history tests, the workspace
and job/lifecycle suite, and SQLite ownership contracts. Real-process regressions
cover backend SIGKILL during exec/spawn, a previously exited leader, refused
dispatch after owner death, cancellation during supervisor publication, stdio,
cwd/env/exit-code preservation, missing executables and detached survivors.
The sibling launcher audit records browser/Electron proof commands and artifacts.

### Still required

Before adding retry/release controls, introduce an authoritative executor boundary
that rejects obsolete generations (including child processes and remote tools),
then reconcile provider/executor outcomes and SDK context. Only an operation
proven not to have executed, or protected by a verified external idempotency key,
may be retried. A database generation alone cannot fence an already-started
external request. These prerequisites remain open, not silently bypassed.
