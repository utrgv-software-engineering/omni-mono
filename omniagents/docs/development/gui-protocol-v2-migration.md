# GUI protocol v2 migration design

Status: new-major direction approved by the user on 2026-09-07; backend candidate
and bundled clients implemented, Desktop sync and release review pending.
This document is not approval on behalf of either
repository's required reviewer. No v2 release baseline has been created.

## Implementation record (2026-09-07)

Implemented in the backend working tree:

- The generator now defaults to `--major 2`, with separate canonical v2,
  Python, web and Ink output paths. `--major 1` retains byte-identical legacy
  candidate generation. The immutable v1 baseline was not modified.
- The server, Python RPC client, bundled web UI and Ink client use generated
  v2 types. Generated version constants avoid independently hard-coded client
  versions. The common operation catalog remains shared; major-specific schema
  generation and output paths separate the contracts.
- Both ends validate the version. The server rejects incompatible requests
  before initialization; clients reject incompatible successful replies before
  sending `initialized`. Stable same-major newer minor/patch servers are accepted.
  Noncanonical/prerelease versions are rejected rather than guessed compatible.
- Clients validate required handshake fields before using host/capabilities.
  Invalid replies are permanent structured failures, not reconnect loops.
  Python clears old initialization state on a new connection and tolerates
  additive response fields without relaxing server request validation.
  The bundled clients also clear old negotiation state on reconnect. Incoming
  events are gated until handshake acceptance; detached sockets cannot deliver
  events into a replacement connection in the bundled TypeScript clients.
- The compatibility reader selects artifact filenames from the manifest's
  major, checks its version against OpenRPC and validates the canonical digest.
  Active-schema tests now target v2. The former v1-breaking-change exception
  test now explicitly compares released v1 with v2 and asserts both known
  migration breaks. Legacy candidate reproduction and v1 baseline digest checks
  remain separate; neither certifies the legacy candidate for a v1 release.
- Shared integration handshakes and active-client fixtures now use v2. Negative
  tests retain v1 intentionally. A real `/ws` regression proves that a v1 peer
  cannot bypass rejection with `initialized` and cannot invoke `start_run`.

This checkout is **not compatible with the current v1 Desktop build**. Do not
deploy it as a paired release yet. Desktop files/provenance were not synced from
the dirty backend tree. The user subsequently authorized preparing a backend
commit for review; that authorization is not release approval or permission to
publish/sync an unreviewed contract.

Verification:

- V2 boundary/schema/compatibility/server group: 101 passed, including generation
  consistency and unchanged v1 candidate reproduction.
- Full bundled web suite: 77 passed. Full Ink suite: 362 passed.
- Real WebSocket conversation/plan/elicitation integration group: 41 passed.
- Bundled web and Ink builds pass. Ink TypeScript passes. Web TypeScript still
  reports the existing `VoiceModal.tsx:679` disjoint-state comparison; voice
  behavior was left outside this migration. Attachment discriminants were given
  literal types without changing runtime behavior.
- Broad RPC/service enqueue suite: 819 passed. The last oversized-version and
  pre-handshake-event regressions were subsequently verified in a focused client
  group: 100 passed. Earlier exploratory runs overlapped generator edits and
  reported stale generated artifacts; those runs are not the final verification.

Commands (backend root, activate `.venv` or prepend its `bin` directory to PATH):

```bash
python -m scripts.protocol.generate_gui_protocol --major 2 --check
python -m pytest tests/unit/rpc tests/unit/core/agents/test_service_enqueue.py -q
python -m pytest tests/integration/test_server_conversation.py tests/integration/test_server_plans_and_diffs.py tests/integration/test_elicitation_ws.py -q
npm --prefix omniagents/backends/web/ui test
npm --prefix omniagents/backends/ink/tui test
npm --prefix omniagents/backends/web/ui run build
npm --prefix omniagents/backends/ink/tui run type-check
npm --prefix omniagents/backends/ink/tui run build
```

Remaining gates: reviewed-source Desktop sync/tooling and imports, Desktop
handshake guards and mismatch UI proof, final paired release review, and only
then publication of a v2 baseline. The snapshot/ownership semantics described
below remain requirements for the final contract review; this implementation
does not add distributed event fanout or a storage migration. No new Desktop
visual proof is claimed for this backend-only stage.

## Decision

Introduce GUI protocol `2.0.0` for explicit environment selection and structured
message content. Keep environment safety intact. Do not make the existing v1
baseline accept these breaking changes, and do not pretend that changing a
version string alone migrates the protocol.

The initial v2 runtime will accept v2 connections only on the GUI RPC endpoint.
It will not emulate v1 by guessing an execution environment. Existing v1
deployments remain paired with v1 clients during rollout. Preserving v1 release
artifacts does not mean a v2 server promises to execute v1 requests.

This is a GUI RPC version, not the JSON-RPC envelope version, app version,
database format version, MCP version, or real-time voice protocol version.
`jsonrpc` remains `"2.0"`. Existing conversation data is not renamed or deleted.

## Evidence from the current implementation

- `rpc/server.py::_initialize` already compares protocol major/minor/patch and
  returns `-32012` with `kind: protocol_version_mismatch` before marking a
  connection initialized. Reuse this boundary rather than adding another
  session lifecycle or selecting a version from whichever tile is active.
- Desktop `initializeConnection`, web `ws.onopen`, Ink `performInitialize`, and
  Python `_connect_and_handshake` all currently request `1.0.0`. Successful
  results are not independently checked for a compatible returned version.
- The generator, compatibility reader, generated import paths, Desktop sync
  tooling, and several tests are hard-coded to v1. They need explicit major
  selection; a runtime constant change would leave the contract inconsistent.
- The current compatibility test intentionally accepts the required environment
  selection but fails on the additional `content` restriction. That exception
  conflicts with the v1 additive-only governance text. Resolve it by separating
  majors, not by expanding the v1 test's list of tolerated breaks.

## Wire behavior

Use the existing `initialize` / `initialized` exchange. A connection becomes
ready only after both peers agree on the GUI version and required capabilities.

| Client | Server | Result |
| --- | --- | --- |
| v2 | v2 | Validate handshake, send `initialized`, then attach/replay sessions |
| v1 | v2 | Server returns `-32012`; no session creation or dispatch |
| v2 | v1 | Reject with an update-required error; do not retry as v1 |
| v2 | Missing/malformed/foreign-major successful result | Client rejects before `initialized` or session replay |

Generate one version constant per major and consume it everywhere; do not
independently hard-code four client constants. Client checks must accept a
compatible newer minor/patch in the same major, reject a server below the
requested version, and reject another major. Repeat validation on every new
connection, including reconnect to a different backend.

Treat mismatches as permanent for that connection attempt. Do not loop reconnects
or downgrade silently. Preserve drafts and pending command identities. Display
the client/server protocol versions and a clear instruction to update the older
component. User-initiated reconnect after an upgrade is allowed.

Optional experimental capability reduction may remain. It must not remove the
major version requirement or a capability necessary for correct chat behavior.
Notifications and session replay must not mutate UI state before readiness.

## V2 contract requirements

1. Preserve required `start_run.environment_selection`, validating placement
   before any session/run execution effects. Preserve structured content
   validation rather than relaxing it to get a compatibility check to pass.
2. Specify the snapshot contract used by Desktop: session identity, run state,
   canonical ordered items, queue, pending requests, stream epoch and watermark.
   Display-state events are a restricted presentation projection, not executable
   client commands. Make the required snapshot behavior a v2 guarantee or an
   explicitly required named capability, not an untyped best-effort extra.
3. Specify durable submission identity and payload-conflict behavior. Retrying
   after a lost reply must reuse the command identity; upgrade/reconnect must not
   silently resubmit with a new key.
4. Keep session/run ownership independent of window/tile identity. Require the
   identity needed to attribute delayed output and repeated provider call IDs.
5. Explicitly distinguish durable display items from requests backed by live
   waiters, and document attach/reconciliation effects of queue/resume methods.
6. Do not claim distributed live-event fanout or change unrelated protocols.

## Implementation stages

### 1. Separate source and generated artifacts

- Make catalog/schema generation major-specific. Preserve reproducible v1
  release evidence and introduce an explicit v2 source entry point.
- Add `protocol/openrpc/v2/` containing the v2 OpenRPC document, schema,
  inventory and manifest, plus Python `rpc/generated/gui_v2/` and web/Ink
  `protocol/generated/gui-v2.ts` outputs. Generated files come from the generator.
- Keep existing v1 files/baseline intact during migration. Audit the dirty v1
  candidate separately; do not overwrite accumulated changes or equate its
  present contents with the released v1 contract.
- Parameterize compatibility tooling by major/artifact paths. Comparing v1 to
  v2 should report intentional migration differences, not certify compatibility.
  Keep the v1 digest/compatibility gate distinct from v2 generation/schema gates.
- Do not create `baseline/2.0.0` until the actual release has been reviewed.
  After release, v2 changes must pass compatibility against that immutable baseline.

### 2. Move server and maintained backend clients together

- Server imports v2 request/result/notification models and advertises v2.
- Python RPC, bundled web, and Ink use generated v2 types/constants and check
  handshake responses before readiness. Preserve non-GUI transport modes.
- Update protocol-specific fixtures deliberately: keep explicit v1 mismatch
  fixtures and add v2 success cases. Do not globally replace every `1.0.0` string.
- Add boundary tests before switching the default runtime: mismatch, malformed
  success, future major, compatible minor, reconnect, and absent capabilities.

### 3. Migrate Desktop with truthful provenance

- Add a v2 consumer directory and major-aware sync/check commands; preserve v1
  provenance. Switch imports only with corresponding generated v2 artifacts.
- The governance requires a clean, reviewed backend source commit whose checks
  passed. Do not copy this dirty working tree and label it with the existing HEAD.
  The user has now authorized preparing the backend commit for review. Desktop
  sync still requires review of that source commit.
- After that source-review gate, sync v2, switch the handshake and client guards,
  and route mismatch errors through the existing permanent connection-error UI.
- Do not migrate conversation storage or discard drafts as part of this change.

### 4. Prove the paired migration and release

- Backend tests: v2 accepted; v1 denied; pre-initialize calls have no side
  effects; failed handshake does not attach a session, register tools, or drain
  queued work. A later valid handshake follows the documented lifecycle.
- All four clients: reject bogus successful responses; never send `initialized`
  after rejection; no replay, capability registration, automatic sends, or retry
  storm; stale connections cannot complete a newer handshake.
- Desktop permanent browser and native proofs: two tiles, separate artifacts
  and plans, reconnect, preserved draft/command identities, and visible mismatch
  messaging. Also exercise a real v1/v2 mismatch, not just a mocked success.
- Run generation consistency, major-specific compatibility, TypeScript/Python
  checks, relevant protocol/session tests and builds. Record exact proof commands
  and reports before review.
- Release paired client/server versions. Remote v1 hosts stay on compatible v1
  clients until upgraded. A deployment rollback must restore the matching pair;
  no server-side implicit environment fallback is introduced.

## Completion and handoff

Design completion is not migration completion. The migration is complete only
when all maintained clients use the reviewed v2 contract, mixed-version behavior
is proven, generated provenance is valid, and the release documentation states
the supported pairs. Baseline publication is a release action, not a local fix
for a red test. See `gui-protocol-governance.md` for the two-repository review gate.
