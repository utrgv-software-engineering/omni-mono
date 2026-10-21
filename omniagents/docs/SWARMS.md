# Workers and optional peer communication

All new delegation uses the `workers` capability. `swarms` is a compatibility
alias for that same toolset. Omni Code regular chats and residents both use it;
there is no separate swarm creation, roster, shared objective or closure step.

Use `spawn_worker(task, name?, role?, peer_communication=false, isolation?, allow_dirty?)`.
Names are optional, unique within the parent session, and identify assignments.
Keep the returned worker ID for status, messages, cancellation, resumption and
workspace cleanup. Message the existing worker with `send_worker_message` for follow-up
work; spawning the same name again is an error.

Work directly by default. Delegate when the assignment benefits from independent
execution or review. The owner remains responsible for authorization, scope,
review, integration and the final result. Parallel work alone does not require
peer communication.

## Communication

Every new worker receives two host-bound tools. The parent can additionally
opt individual workers into peer communication at spawn time:

- `worker_context()`: discover opted-in siblings, including their names, roles,
  assignments and status, and read your incoming/outgoing mailbox.
- `worker_message(recipient, message)`: send to a discovered worker ID or
  `"owner"`. The model cannot supply the sender or parent identity.

The boundary is the owning parent conversation, not the whole project or app.
Workers that have not opted in and cleaned workers are unavailable as peers.
Workers appear as they are spawned; a worker cannot address a future sibling
before it exists. There is no shared goal requirement or separate team object.
These tools do not grant recruitment or lifecycle control.

Messages are stored atomically in the parent's durable worker journal before
best-effort delivery through the recipient's existing message queue. Retrying
the same tool invocation deduplicates both admission and queue delivery.
Delivery failure leaves the mailbox readable. Messages to the owner atomically enqueue a parent notification and wake it
after its current run. Ordinary worker text and normal turn endings do not
notify the parent. Failures still notify it automatically. Messages addressed
to workers automatically start an idle recipient or wait until its active run
ends, then execute in arrival order. No separate resume call is required.
`close_worker` cancels active and queued work and rejects future messages.
`resume_worker` remains available for explicit interrupted-run recovery.

Use `get_worker_status()` without an ID to discover all owned workers and their
mail. With an ID it returns that worker's state, recent history and relevant
messages. Parent guidance continues to use `send_worker_message`.

Mailboxes admit at most 500 messages per parent, with at most 16,000 characters
per message. Exhaustion returns an explicit error; it does not silently discard
messages. Share useful findings and dependencies, not acknowledgments.

## Persistence and compatibility

Worker identity, child conversation, name, role, communication setting, mailbox
and workspace association survive runtime restarts. Resumption reconstructs the
bound communication tools from the durable recipe. Missing environments or
changed worker definitions block resumption instead of silently replacing them.

Existing swarm records are not rewritten into a broader parent-wide group.
Their recovered children retain the original swarm tools, membership and
automatic end-of-run reports. New project-worker recipes use explicit messages.
The owner can inspect historical membership and messages through
`get_worker_status`, and use the ordinary worker ID lifecycle to resume, cancel
or clean up existing workers. Existing standalone named-worker records remain
recoverable too. Old tool implementations/registry names remain for legacy
configurations; neither standard capability exposes them for new delegation.

Existing `workers.list` and subagent events retain their legacy swarm fields
for compatible clients. No new launcher settings or collaboration mode selector
is needed. Update framework, Omni Code and launcher together; source changes
do not publish or install a runtime release.

## Isolation and approvals

Peer communication does not synchronize isolated files or merge worktrees.
Assign non-overlapping file ownership. Review and integrate each worker's
changes before explicit workspace cleanup.

Workers keep the existing provider scope, Guardian review and parent approval
routing. Role instructions do not grant permissions or override credentials.
Interrupted tool effects may be uncertain: inspect recorded results and files
before deciding whether an action is safe to retry.
