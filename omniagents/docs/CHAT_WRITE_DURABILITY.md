# Chat write durability

Current staged ownership/recovery status is in [RUNTIME_RECOVERY.md](RUNTIME_RECOVERY.md).
Its conservative runtime reservation supersedes the older immediate pending-queue
recovery behavior described below when a prior run's ownership is unresolved.

## Completed model responses and injected messages

New completed, provider-identified assistant messages written by `Session`
carry a versioned projection intent in `history_projection_outbox`. The raw
history row, intent and any active SDK-context append commit in one history
transaction. The intent references the raw row, rather than duplicating its
text; deletion/retention cascades to it.

Canonical item creation uses `ConversationStore.append_item`: provider identity,
sequence allocation, item insertion, thread count and turn count/window commit
together. SQLite uses `BEGIN IMMEDIATE`; PostgreSQL locks the owning thread.
Re-delivery returns the original item without another sequence or count change.

The intent is acknowledged only after canonical insertion. Recovery runs before
subsequent canonical item writes and when an existing conversation is read.
Thus death after the history commit replays the missing item; death after the
domain commit replays its identity without duplicating it. A later checkpoint
cannot overtake an unresolved intent. A failed history commit does not advance
the Session's in-memory history.

Append-only queued messages and mid-run user messages also opt in. Queue IDs
are stable projection identities; mid-run sends reserve their existing
client-derivable occurrence identity after draining older intents. The intent
is separate from model-visible message content. Invalid intents or initial
canonical-owner unavailability reject the write before history acceptance.

This is an outbox, **not** a cross-database transaction. It does not infer
identities for ID-less output, retroactively repair old data, or make arbitrary
tool execution exactly once. Queue acceptance before draining uses the separate
durable inbox described below.

## Durable queued commands

Queue acceptance and its submission receipt now commit in one transaction.
SQLite uses `command_inboxes`; PostgreSQL uses tenant/project/agent-scoped
`conversation_inboxes`, protected by the same thread lock and tenant RLS.
`Session.message_queue` and buffered notifications are runtime projections,
not the source of truth. Notification batching moves the buffer into a queued
command atomically; a full queue retains the buffer for later delivery.

Reopening a session loads its inbox and dispatches unclaimed pending work.
Each command is claimed durably before execution. A claim is not a lease:
process death never grants another runtime permission to repeat an external
effect. A completed canonical append or terminal run proves completion; a
durably rejected start leaves a visible failed item that can be cancelled.
Without such evidence, the UI shows `dispatch_uncertain` and pauses subsequent
queued commands. These items cannot be cancelled or automatically replayed.
There is not yet an operator reconciliation workflow for unresolved claims.

Retries with the original submission identity return the original acceptance,
even after the command has finished. Changed content under that identity is
rejected. Archive checks use current database state, not a stale Session flag.
Deletion removes the inbox along with the thread. Notification buffering is
durable but does not itself offer caller-supplied idempotency keys.

## Canonical lifecycle transactions

`mutate_thread`, `mutate_turn`, and `mutate_item` read current rows under a
database lock and apply pure, no-I/O callbacks before committing. SQLite uses
`BEGIN IMMEDIATE`; PostgreSQL takes thread then turn/item row locks. These
operations protect runtime updates; raw upserts remain for imports/projection.

Turn creation, ordinal allocation, initial prompt, title, item/turn counters
and sequence windows now commit together. Concurrent delivery of the same run
does not add another prompt or increment counters twice. Turn settlement and
thread usage rollup commit together; late token/status events cannot rewrite
a settled turn. Item transitions and artifact revisions merge against the
locked latest version, preserving terminal-once status and revision ordering.
Thread metadata patches merge against current values instead of stale copies.

## Model context versus transcript

`Session.history` remains the append-only raw transcript. The SDK uses a
separate durable context view, stored in `sessions.sdk_context_json` (SQLite)
or `sessions.sdk_context` (PostgreSQL). NULL means use the normal transcript;
an empty array means context was deliberately cleared.

`pop_item`, `clear_session` and `replace_sdk_items` update this view without
erasing transcript records. The Omni SDK compaction wrapper replaces the whole
view in one write, avoiding the SDK's default clear-then-add crash window.
Subsequent history appends also extend the context in the same transaction.
Full forks inherit the current context; historical branch-point forks use
their historical prefix and never inherit the parent's later context.

The wrapper leaves explicitly supplied external session implementations on
their own replacement semantics. Do not call SDK clear/pop to delete a user's
conversation; use the explicit deletion/retention workflow.

## Verification

- `tests/unit/core/conversation/test_write_crash.py`: subprocess death inside
  canonical sequence/item/turn writes, between history/domain/acknowledgment,
  and before/after SDK-context commit. All state is temporary.
- `tests/unit/core/conversation/test_store.py::TestAtomicAppend`: concurrent
  identity claims, distinct messages, counters and idempotence; shared with PG.
- `TestAtomicMutations` in the same file: concurrent turns, appends, metadata,
  artifact revisions, competing terminal states and late token/status events;
  also shared with PG. Turn-start process-death coverage is in `test_write_crash`.
- `tests/unit/core/session/test_projection_outbox.py::TestProjectionOutbox`:
  rollback, scoped acknowledgment, recovery, durable clear/pop, real SDK
  replacement and fork behavior; shared with PG history tests.

The offline `history_audit` tool remains strictly read-only for its inputs;
it does not run this runtime recovery or initialize/migrate the original stores.

## Remaining work

- Uncertain queue claims are conservatively retained, not automatically retried.
  A safe operator reconciliation workflow still needs an authoritative runtime
  ownership/outcome check before releasing a claim or permitting a retry.
- Human and automatic approval decisions now use strict commits, and local
  function-tool dispatch/results use the durable claim boundary below. External
  side effects, SDK history/context, event journaling and canonical recording
  still are not one transaction. A committed approval does not prove execution;
  a claimed invocation does not prove whether an external effect happened.
- Hosted/provider-side tools and non-function SDK tool families do not share the
  local function-tool hook contract. Do not infer coverage from their UI rows.
- Raw historical projection/fork imports are not covered by the runtime mutation
  contract; the separate historical repair restrictions still apply.

## Human approval commit boundary — September 7, 2026

`_claim_approval` commits the complete normalized response and the approval
item's completed status in one `mutate_item` transaction, before resolving the
live waiter or granting an always-allow permission. Storage failures propagate
to the RPC caller and leave the waiter, permissions and local retry cache alone.
Identical retries accept a committed decision (including after a lost database
acknowledgment); conflicting retries fail under the same database lock. Missing
or retired prompts cannot authorize execution.

If the earlier best-effort request projection failed, the service can recreate
only the currently live request from its server-owned replay payload before
committing the answer. This is not historical repair. Restarted processes do
not reconstruct waiters or execute tools merely because a decision is stored.

Verification includes both function-tool and MCP failure/retry tests, SQLite
and PostgreSQL competing-writer tests, terminal retries, and subprocess death
inside/after decision commit. The same run exposed a SQLite history first-use
race: initialization now publishes one cached connection under a mutex, and
schema inspection/migration runs in `BEGIN IMMEDIATE` across connections.

## Automatic approvals and local tool execution — September 7, 2026

Completed stages in this continuation:

1. Guardian and sandbox-policy reviews commit a terminal approval record before
   returning their decision, broadcasting it, or incrementing review counters.
   Failed persistence fails closed. Each review gets a server-owned approval ID;
   the provider call ID and reviewed arguments remain recorded as evidence, not
   as a globally unique review identity. Preconfigured safe-tool/always-allow
   policy grants are not new individual reviews.
2. `DurableToolHooks` wraps the SDK's local function-tool callbacks, including MCP
   adapters. A locked canonical claim commits before invoking the tool. A strict
   result write commits before the completion hook returns to the SDK. Competing
   dispatchers cannot claim the same thread/run/call identity. Duplicate identical
   completion is idempotent; foreign owners and conflicting outputs fail.
3. Cancellation/abandoned calls are explicitly outcome-unknown. A read-only runtime
   snapshot retires orphaned started rows without rewriting another runtime's
   persisted state. A genuine late result is retained without reopening the
   already-terminal item. Claimed or uncertain calls cannot be implicitly retried.
4. Tool identity includes the run ID in the canonical recorder and launcher live
   transcript/React keys. A reused provider ID in a later run stays separate.
   Legacy projection retains its original identity for the first occurrence and
   uses history-position identity for repeats. Legacy history has no authoritative
   run ID and is a fallback, not a merge into an existing canonical transcript.
5. MCP UI resources, tool-name lookups and resource catalogs are run-scoped.
   Exact server/tool/invocation correlation handles out-of-order results without
   borrowing another tile's UI. The existing SDK metadata resolver is preserved;
   internal correlation is not sent as extra MCP wire metadata. UI metadata is
   persisted with the strict result and remains available for stream delivery.

Coverage includes real SDK function execution with injected claim/result write
failures; the actual MCP adapter invocation and original metadata resolver;
concurrent/out-of-order MCP UI results; SQLite/PostgreSQL competing claims and
late completion; and subprocess death during claim, after claim and after result
commit.

These guards are deliberately conservative, not an exactly-once external-effect
protocol. Death after claim but before invocation is indistinguishable from death
after an external effect without a result commit. No automatic retry or operator
release API was added. Safe release still requires authoritative runtime fencing
and outcome reconciliation. Durable canonical output also does not guarantee the
SDK context/history was saved before a crash. Original historical databases were
not repaired; Azure verification remains excluded.

Verification totals: 1,249 broad backend tests passed; one legacy MCP test was
skipped because its stub was not a protocol server. That invalid fixture and its
catch-all success/skip behavior were subsequently replaced with an actual local
FastMCP stdio server; both lifecycle cases passed. The PostgreSQL domain/history
suite passed 120 tests. Launcher verification passed 173 focused tests and nine
visual proof stories across browser/server and native Electron.
