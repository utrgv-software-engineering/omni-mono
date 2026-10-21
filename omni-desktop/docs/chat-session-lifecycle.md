# Chat session lifecycle

## Ownership

Session state is no longer owned by a switchable chat component:

```text
Shared runtime connection (endpoint + credentials; reference-counted provider leases)
└─ SessionRegistry (one event router)
   ├─ ConversationSession A → subscribed views of A
   └─ ConversationSession B → subscribed views of B
```

Separate `RPCClientProvider` wrappers for the same endpoint and credentials share one client, management cache, and session registry. Closing one provider does not disconnect other views. Different endpoints, credentials, or injected client factories are never pooled together. The last provider lease releases the connection and session controllers; StrictMode's immediate cleanup/setup pair does not disconnect them.

Within that connection, the registry key is the session ID: two runtimes with the same session string do not share a controller. A controller's identity never changes. Selection subscribes to another controller; it does not retarget an actor. The App boundary also keys session views by connection and session, keeping transient layout state and host callbacks out of the next conversation.

Controllers own transcript actors, replay registration, hydration, serialized submission, slash-command results, client-function handling, pending escalations, and revisioned panel/settings state. Model controls subscribe to that session-owned state. View effects only publish host-specific notifications or manage presentation; they do not acknowledge session client calls or disconnect the shared connection.

Controllers remain alive when their views unmount, so an outstanding request for A still updates A while B is displayed. The provider disposes them when the connection owner goes away. Deferred disposal tolerates React StrictMode's cleanup/setup cycle. This is in-memory ownership, not persistence across an application restart.

The event router requires an explicit session/thread identity and rejects conflicting or missing identities. It never falls back to the selected chat. Client-tool fulfillment happens once per request on the owning controller; another session cannot acknowledge an offscreen escalation.

## Lifecycle rules

Conversation identity, connection readiness, and execution readiness are separate:

- A launcher conversation can own a draft and model choices before its workspace starts.
- `ConversationComposerProvider` keeps the same composer mounted while its DOM host moves from the startup shell to the live conversation. Selection, focus, and local input history survive that transition.
- `conversation-drafts.ts` owns unsent text, File objects, and pre-launch choices by conversation ID; nothing about in-flight sends lives there. IndexedDB persists them in the current origin/profile, including attachment bytes. A component unmount or renderer restart is not a draft deletion. Storage failures keep the in-memory draft; clearing app/browser storage still deletes local drafts.
- The product management catalog supplies pre-launch models. Controller hydration applies explicit choices through `prepareConversation` before becoming ready. Refused choices block the first prompt and remain available for retry.
- Hydration coalesces concurrent reads, registers replay, and restores history, queue contents, pending requests and active-run state from one atomic snapshot. There is no intermediate ready/idle state before an active run is restored. A connection without the snapshot read is an internal error, not an assumption that the session is idle; hydration errors are retried automatically.
- `queue_status(include_snapshot=true)` returns canonical history, queue, pending client requests, run state, and the journal watermark without yielding the runtime's session-owning event loop. The client fences older replay requests during the read, adopts that boundary, and applies only newer sequenced events. Reconnect replay resumes at the actual snapshot watermark, not an earlier error cursor; a stale replay result or error cannot replace it. An older runtime without this extension reports that it needs updating.
- Panel and model-catalog reads are plain sets: the latest response applies, and an effect that has been cleaned up drops its late response.
- Live model controls remain mounted through connection drops. Mutations are disabled until reconnection and capability discovery complete.
- Direct and queued sends retain structured attachments. A queue refusal is a failed submission, not success.
- Sends are at-most-once from the client's point of view. Each send carries one submission ID and is attempted once; if the transport fails before the reply arrives, the client asks the server once for that ID's receipt, and a completed receipt means the message landed, so the transcript is reloaded. Anything else is reported as a failed send and the text returns to the composer; a resend is a new submission, while the server's receipt still dedupes a retry of the same ID. The client never blocks typing because it is unsure.
- The Send button remains available during a response and queues the follow-up, matching Enter. Stop remains a separate action.
- Submission is serialized per controller, including across two views of the same session. Queue refusal leaves an existing run active. Model-setting mutations block new submissions until the selection has settled.
- URL initial prompts belong to the initially selected session and are claimed once. Failure restores the prompt into that session's draft instead of silently consuming it.
- Escalation replies carry structured image/file content through `client_response`. The server validates it before consuming the question and returns SDK-native text/image/file tool outputs, so the model can actually inspect attachments. Resolved-question events dismiss the owning session's banner across views. A lost answer acknowledgement is reported as a failed send; a resend answers whichever question is pending, and the server's per-request receipt acknowledges an exact resend without consuming the question twice.

## Implementation map

Paths are under `src/renderer/omniagents-ui/` unless shown in full.

- `session/session-registry.ts`: connection-owned routing and lifetime.
- `session/conversation-session.ts`: fixed-identity session operations and state ownership.
- `session/session-transcript.ts`: protocol-event to transcript-machine adapter.
- `session/session-panels.ts`: session panel and model-control state.
- `hooks/use-chat-session.ts`: React subscriptions and fixed-owner action adapters; no connection listeners or identity mutation.
- `rpc-context.tsx`: pooled authenticated connections and reference-counted provider leases.
- `App.tsx`: selection boundary, session-bound presentation, and host integration.
- `conversation-drafts.ts` / `draft-storage.ts`: per-conversation drafts and recovery records in IndexedDB.
- `src/shared/chat-commands.ts`: authority-side chat-list mutations (main process or launcher server).

## Server contract

`server_call(function="session.ensure")` creates or retrieves a session and applies supplied metadata. It does not mean the workspace is ready, the connection is ready, or a run has started. Standalone terminals legitimately use it before a run.

`start_run` and the session model/reasoning/approval setters also create sessions when necessary. Ordinary chat does not need a defensive `session.ensure` call before each operation. Preserve the same conversation ID across these operations.

`enqueue_message.content` is the text prompt/display summary. The additive `input_content` array carries structured input parts to `start_run.content` when the queue drains. It is deliberately omitted from queue broadcasts so file data is not duplicated into every queue snapshot.

The generated `start_run.content` schema also accepts structured input arrays, matching the existing server implementation, while retaining string compatibility.

## Recovery and concurrency rules

- Accepted-send reconciliation keeps its submission identity until the authoritative history refresh succeeds, so a failed refresh cannot turn the next retry into a new execution.
- Interrupted submission records include their staged context; a retry after restart reuses it and does not clear newer staged context from the composer.
- Client-tool execution and response delivery are separate. The controller caches the result, marks it acknowledged only after delivery, and resends it on pending-request replay without re-running the tool. This is not exactly-once across application crashes.
- Submission rechecks session readiness after asynchronous preparation and checkpoint saves, immediately before issuing the RPC. Disposal during a save rejects the operation without sending it.
- Draft writes apply field patches in one IndexedDB read/modify/write transaction. Recovery fields carry a compare-and-swap revision, so a stale window cannot claim or clear newer recovery state. BroadcastChannel only announces the changed conversation ID; correctness does not depend on receiving it. Concurrent edits of the same ordinary field are last-write-wins.
- Hosts install client-tool capabilities with `ConversationSession.setToolHandler`. View cleanup does not revoke an active background session's tools.
- Chat-list state (creation, selection, archive, removal, reorder, layout) changes only through the commands in `src/shared/chat-commands.ts`, applied by the desktop main process or launcher server against the authoritative store. The renderer's `Code/state.ts` is a typed command client; process and terminal teardown run after the committed removal.
- Browser transport IDs belong to one window's emitter lifetime. A reconnect keeps the ID; a new document gets a new one and rehydrates durable chat state.
- Approvals use opaque per-session tokens, separate from provider tool call IDs (kept as `source_call_id` / `source_request_id` metadata). Decisions are first-writer-wins; an exact retry succeeds without repeating effects, a conflicting decision errors. The client requires an explicit `true` acknowledgement before clearing a decision.
- Server-to-client requests (questions, approvals) install their waiter and replay payload before delivery. Timeout, cancellation and run end remove every index; resolution events are journaled so a reconnect does not revive an abandoned question. After a lost acknowledgement the client retries the original question ID.
- After a hard runtime crash, snapshot approvals and questions without a live waiter render as historical rather than actionable.
