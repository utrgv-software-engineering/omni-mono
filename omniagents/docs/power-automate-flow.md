# Power Automate single-row event flow

Power Automate supplies the data to the existing PostgreSQL queue. Factory polls
an isolated private bridge service, which owns database access. This separation
does not change the flow's Insert row action or payload schema.
Each event is one JSON object in `inbound_events.payload`. No manifest tables,
chunks, encoding step, Update row action, or schema migration is required.
The generic transport does not require any Teams fields. Teams is one configured
handler; the following recipe applies only to that handler.

## Teams actions inside the existing loop

Keep `Teams_message` and `List_replies_of_a_channel_message`.
For the replies action use the team/channel from `Teams_message.channelIdentity`
and this root message expression:

```text
if(empty(body('Teams_message')?['replyToId']), body('Teams_message')?['id'], body('Teams_message')?['replyToId'])
```

Set Latest replies count to 50 as selected for this flow. The backend does not cap
or truncate the supplied replies and does not require pagination or a completeness
flag. This supplies the root plus the connector's returned replies, not necessarily
every message in a thread longer than the selected connector window.

Add a Teams Get message details action named `Thread_root` with channel thread
type, the same team/channel, the root expression above as Message ID, and no
parent/reply message ID. It retrieves the root even when the trigger is a reply.
Run this before `Bridge_event`. Keep the existing normalized fields in Compose;
add these entries to its inputs:

```json
{
  "thread_history": {
    "root": "@body('Thread_root')",
    "replies": "@body('List_replies_of_a_channel_message')?['value']"
  }
}
```

These are object/array expressions, not interpolated strings (`@{...}`). In the
designer Expression editor omit the leading `@`. Confirm the replies action's
actual output contains `value`; do not substitute an empty array if retrieval
failed. Both root retrieval and reply listing must succeed before Compose.
No normalization of individual history messages is required: retain raw objects.
The triggering message must appear in the root/replies snapshot; retry retrieval
if connector timing causes it to be absent.

## Existing PostgreSQL Insert row action

Keep table `[omni_bridge].[inbound_events]` and these fields:

```text
id: concat('teams_intake:', outputs('Bridge_event')?['tenant_id'], ':', outputs('Bridge_event')?['channel_id'], ':', outputs('Bridge_event')?['message_id'])
source: teams_intake
payload: string(outputs('Bridge_event'))
created_at: utcNow()
available_at: utcNow()
```

`source` is literal text; other values use the Expression editor. Keep the
original message timestamp in Compose's `created_at`, distinct from queue times.
Retry with the same identity and payload; a duplicate key is not permission to
overwrite or create a different delivery identity. Check the existing row before
treating a duplicate as success. The original database 1 MiB payload constraint
still applies; there is no added handler reply-count or inbound-text-size cap.

## Factory configuration and testing

Factory chooses the source handler, destination, marker and agent; do not put
Factory-specific filtering in the flow. Keep stable rollout cutoffs so old queued
events are not replayed. Test a fresh root, reply, marker in an existing thread,
and unmarked follow-up. History is reference context, not independent requests.
Verify acknowledgments and actual intake separately from outbound posting.
See [the backend guide](power-automate-bridge.md) for deployment prerequisites.
