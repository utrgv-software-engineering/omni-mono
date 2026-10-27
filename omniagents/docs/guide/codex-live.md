# Codex Live voice

An ordinary text agent can expose subscription-backed GPT Live voice alongside
its existing conversation and tools:

```yaml
voice_backend: codex-live
voice_instructions: Keep spoken replies brief; detailed results belong in chat.
```

Keep `realtime_mode` false for a text agent. The server installs a separate
signalling service at `/ws/realtime`. It uses the existing Codex OAuth store
(`OMNIAGENTS_CODEX_HOME`) and requests `gpt-live-1-codex` through the ChatGPT
Codex backend. This is the subscription transport ported from the Picture Frame
prototype, not the public API-key-based GPT Live endpoint. Credentials stay on
the server. Capability probes check the local sign-in state without refreshing
credentials or opening a call.

The shared chat UI negotiates the advertised WebRTC transport using its existing
voice controls. Browsers require microphone permission and HTTPS or localhost.
Audio travels over WebRTC; signalling, delegation and context sync use the
existing authenticated runtime WebSocket. Standard WebSocket/PCM voice backends
continue to use their existing transport.

Live handles conversation and delegates actions to the agent's durable queue.
All requests, including follow-ups, queue in order; voice cannot redirect an
unrelated active run or bypass tool approvals. Delegation IDs deduplicate requests
within a call. Results are matched to the delegated run, not other work sharing
the conversation. Stopping voice detaches observers and stops microphone capture,
but does not cancel accepted agent work. Use the chat's Stop control for that.

Completed speech and the final unfinished transcript are saved into the paired
conversation without enqueuing work. New text-chat context flows back to the call.
The CC control beside the voice controls shows or hides spoken transcripts,
including saved speech after a refresh. Captions default off in Workplace Chat;
the browser remembers changes. Hiding captions does not remove agent context or
hide delegated task results, tool activity, approvals, or questions.
Only the originating WebSocket can control a live call, and only one call may be
active per conversation. Disconnecting closes that call; it is not reclaimed by
another browser automatically.

The Workplace Personal gateway binds all operations to `factory-personal`, checks
admin access and allowed browser origins, and attributes voice requests and spoken
user turns to the authenticated account. It supports `capabilities`,
`start_session(session_id, sdp)`, `delegate(session_id, delegation_id, text)`,
`sync_context(session_id, turns)`, and `stop_session(session_id)` only.

Tests use mocked SDP negotiation, media and agent execution. They do not verify
subscription entitlement or microphone/speaker quality; those require a real call.
