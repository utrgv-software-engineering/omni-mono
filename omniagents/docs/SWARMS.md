# Task-scoped swarms

`swarms` is an opt-in capability pack built on the existing worker runtime.
Select `swarms` instead of `workers` in an agent's `capabilities` list. Existing
worker tools and schemas remain available. A product must
install its usual `WorkerDefaults`; no second runtime or model configuration
is needed. Regular Omni Code chat continues to select `workers`. Launcher residents
automatically select `swarms` through a session capability profile.

The owner creates a shared objective (`create_swarm`), registers named roles
(`add_swarm_member`), then starts concrete assignments (`dispatch_swarm_member`).
Define the roster first, then dispatch independent assignments in one turn.
`get_swarm` shows member tasks, live worker status/results, and messages.
Dispatching a finished member resumes its existing history and workspace.
Dispatching a running member fails; use `message_swarm_member` to steer it.

Members inherit the normal product worker specification and receive only two
additional tools: `swarm_member_context` and `swarm_member_message`. Their sender
identity and group are host-bound closures, not user-controlled arguments.
They can address other members by name or write to `owner`. Messages to running
members are injected through the normal worker message path. Every message is
also retained in the group's bounded mailbox. Planned/finished members read
mail on their next dispatch; messages alone never wake agents. Owner messages
are visible in `get_swarm`; normal worker completion wakes the owner.

The owner integrates results, calls `close_swarm` with the outcome, and cleans
isolated workspaces with `cleanup_swarm_member` after reviewing/merging changes.
Close refuses running members unless `cancel_running=true`. Close never deletes
workspaces. Cleanup refuses running members and prevents further dispatch of
that member. Failed dispatches retain the planned member for retry.

## Scope and limits

- Swarm records are session-owned and host-process-local, including across
  ordinary worker resumes. They do not survive host restart or resident daily
  session rollover. Finish/integrate before that boundary; put durable outcomes
  in projects or resident memory. Persisted worker transcripts alone do not
  reconstruct a swarm.
- Maximum 8 open groups per owner, 16 members per group, 200 messages per group.
  Exhaustion is an explicit error, never silent mailbox truncation.
- Names and roles are execution labels and instructions, not personalities.
  Skills can be requested in the brief using the existing worker skills tool.
  This version does not introduce per-member permission or capability policies.
- Worker status, transcripts, completion notifications, isolation and approvals
  follow the existing worker implementation. No new UI or resident settings
  selector is needed: residents use swarms and regular chat uses workers.
- Use this pack with a focused worker spec without recruitment/lifecycle tools,
  as Omni Code already provides. Group members are not new residents.

## Resident migration and regular chat

The launcher adds `capability_profile: resident` to resident session variables
on session creation/reconnection and identity refresh. Both Omni Code text and
voice agent definitions declare:

```yaml
capability_profiles:
  resident:
    workers: swarms
```

The framework selects the profile's tools and instructions when building that
session's agent. It copies the spec; it never changes the shared AgentHost's
base definition. Regular sessions omit the profile and retain `workers`.
Nested utility agents with no declared profiles retain their own capabilities.
The profile must preserve the replaced pack's server-function and agent-tool
configuration, since those are registered when the definition loads.

This applies to existing residents on their next connection to an updated
launcher/runtime, as well as newly created residents. No roster DB migration
or user-facing switch is required. Update launcher, omni-code and omniagents
together; this source change does not publish or install a new runtime release.

Other products can still select `swarms` directly instead of `workers` in an
agent's capability list. Worker-specific prompt guidance lives in the
`workers` capability, so selecting `swarms` does not leave instructions for
unavailable worker tools in the main agent prompt.

## Flutter Activity view

A resident's Activity tab shows swarm objectives, named roles and assignments,
member status/results, member messages, and expandable worker transcripts.
Other workers and agent-tool runs appear separately as delegated work.

`server_call("workers.list", session_id=...)` preserves its existing `snapshot`
worker list and adds `subagents` (the merged observability snapshot) and `swarms`
(session-owned group records). `ui.subagents.update` client requests include
`swarms` alongside the existing `snapshot`; swarm mutations publish through the
owning parent service even when a message originated in a member's service.
Worker lifecycle events continue using the existing subagent bus.

Flutter requests client-function notifications, refreshes the overview on
attachment/reconnect, and ignores stale responses from previous attachments.
Expanded worker transcripts use paginated `list_items` reads and refresh on
subagent events; reading them does not attach to worker approvals or start runs.
The view remains read-only. Swarm records remain process-local, as noted above.

## Approvals

Workers and swarm members use Guardian review by default, even when the owner
uses manual approvals or the worker has an offline container. Explicit
`safe_agent_options.skip_approvals: true` on the parent or worker enables
auto-approval; enforced safety takes precedence. The review uses the worker's
actual environment and the parent's configured Guardian model.

Guardian denials return to the worker so it can adapt. Uncertain or failed
reviews appear in the owning conversation's existing approval UI. They stay
pending without a connected client, survive parent run completion for reconnect
replay, and are dismissed when the waiting worker is cancelled. Resuming a
worker retains its parent approval routing. The monitor never approves tool or
MCP requests on the user's behalf.

## Standalone workers

The same capability also offers `dispatch_worker(name, task, role?, isolation?,
allow_dirty?)` for one independent assignment, with no group or mailbox.
Names are scoped to the owning session. Reusing a name resumes the same worker's
history and isolation; optional role instructions persist unless replaced.
Dispatching an already running worker fails. Workers cannot recruit other agents.

`get_worker(name?)` returns one worker or the session's standalone workers,
including results and workspace paths. `close_single_worker(name)` cancels while
preserving history/files for resumption. `cleanup_single_worker(name)` removes
an isolated workspace after review/integration and prevents further dispatch of
that name. At most 16 uncleaned standalone workers are tracked per session.

Standalone workers use the same Guardian policy and completion notifications as
swarm members. Activity shows them in its existing delegated-worker list, outside
swarm groups. Named records are process-local, like swarm records. Ordinary
`workers` capability tools remain unchanged.
