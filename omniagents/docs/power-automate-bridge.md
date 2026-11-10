# Power Automate bridge backend

## Inbound protocol

Power Automate gathers data using its existing connectors and inserts one row in
`omni_bridge.inbound_events` through the private gateway. An isolated bridge service
accesses PostgreSQL; Factory polls that service over private, mutual-TLS HTTPS.
There is no public inbound endpoint, Graph client, multipart upload or sealing step.
No new inbound schema migration is needed for an existing bridge installation.
Fresh installations use `power_automate.sql` followed by `power_automate_roles.sql`.
Do not rerun those creation scripts on an existing installation.

Fields: stable `id`, authorized `source`, JSON object serialized as text in
`payload`, and connector-required `created_at` / `available_at` timestamps.
The existing database payload constraint is 1 MiB. There is no additional inbound
message-size or reply-count cap. Payload values are not truncated.
Flow logins inherit `omni_bridge_flow`; each source equals its login name.
Worker credentials must remain outside both Factory and agent sandboxes. A separate
container on Factory's Docker daemon is not a credential boundary. Use a separate
VM/trust domain, bridge-only secret access, and verified PostgreSQL TLS. Factory's
host and sandbox networks must deny PostgreSQL access.

## Source configuration

```yaml
integrations:
  - type: power-automate
    bridge_url: https://bridge.internal.example
    bridge_ca_file: /run/omni-private-bridge/server-ca.crt
    bridge_cert_file: /run/omni-private-bridge/factory.crt
    bridge_key_file: /run/omni-private-bridge/factory.key
    not_before: "2026-11-08T12:00:00Z"
    sources:
      connector_login:
        handler: json
        event: submitted
        required_fields: [record_id]
```

Choose `not_before` once at rollout and preserve it across restarts. It filters
queue creation timestamps, leaving older rows untouched. The JSON handler passes
arbitrary payload fields unchanged, emits provider `power-automate`, and applies
only optional top-level required-field checks. It does not infer conversations.
Route its configured event and source filter to the intended agent.

The Teams handler additionally validates the configured tenant/team/channel,
sender policy, message identity and supplied thread context. Use `handler: teams`,
exact destination IDs, `senders: channel_humans`, and `thread_activation` containing
`factory_id`, literal `marker`, and timezone-aware `not_before`. This latter cutoff
uses the original message creation time, not delivery time. Only a marker in the
current event activates a thread; later eligible human replies need no marker.
Existing historical messages remain context, never separate requests.
Allowlist mode instead uses `senders: allowlist` and nonempty `users`.

Teams context lives inside `payload.thread_history`: raw `root` object and raw
`replies` array. Root/trigger inclusion, unique IDs, and matching destination/root
are checked; metadata is preserved. Any number of supplied replies is accepted.
Context is explicitly untrusted and may represent only the latest replies.
The runtime does not retrieve omitted messages or attachment contents.
See [the flow recipe](power-automate-flow.md) for the exact connector expressions.

## Self-service Factory settings

Administrators open **Account → Factory settings**, or **Factory → menu →
Factory settings**. Display name and each Teams source's activation marker are
independent fields. Markers use `@` followed by 1–64 letters, digits, underscores
or hyphens; matching remains case-insensitive. The marker is literal text, not a
Teams account mention. No Power Automate action, payload or PostgreSQL queue
schema changes are required.

Saving updates the visible name without changing Factory IDs, project slugs,
storage paths, channel permissions or approval policies. Workplace persists the
desired revision and an audit record, then applies it through the existing private
administrative connection. The UI distinguishes **Applied to Factory** from
**Saved · waiting for Factory to apply**. Workplace reconciles every five seconds,
including after either process restarts. Concurrent edits return a conflict
instead of overwriting another administrator's changes.

Workplace must have `factory.admin_auth_token` matching the runtime's
`OMNIAGENTS_AGENT_HOST_CONTROL_TOKEN`. The ordinary chat token does not authorize
settings changes. The new `GET`/`PUT /integrations/settings` endpoint accepts only
display name, revision and the configured sources' marker settings, never
credentials, arbitrary YAML, destinations or SQL. Source identities must match.
Workplace's admin-only API is `GET`/`PUT /workplace/api/factory/settings`.

The runtime persists applied settings in
`<home>/integrations/<product-slug>-settings.db` and loads them before intake
starts. Preserve this file alongside the existing intake/spool databases.
`project.yml` provides initial defaults; saved operational settings take
precedence. Changing source destinations/cutoffs remains deployment work and
must reconcile those source-bound settings rather than silently carrying marker
overrides to a different channel.

On first startup with this version, `bridge_thread_namespaces` pins each source's
original marker as an immutable namespace component in the local spool database.
This adopts existing activation, delivery, pending and parked keys in place:
their hashes do not change and no queue rows are replayed. Subsequent marker
edits change only the activation rule, not that namespace. Existing activated
threads and in-flight replies continue normally; new unactivated threads require
the newly applied marker. Updates take effect between intake batches, so a batch
already being processed finishes under its original policy.

## Durability and authorization

Rows become available atomically on insert. The poller locks only authorized,
due, unacknowledged rows; local intake persists before acknowledgment. Stable
identities deduplicate retries. Unexpected failures roll back acknowledgment;
invalid JSON objects reject as `invalid_delivery`, domain failures as
`accept_rejected`. Ignored events may be acknowledged without dispatch.
Preserve local intake and thread-activation SQLite state across deployments.
Run one active Factory consumer per client certificate and durable intake store.
The private service does not reserve rows on a read: until acknowledgment, they
remain available for redelivery. Local intake identities provide deduplication.
The embedded PostgreSQL adapter retains an advisory lease for isolated development
use; it must not be configured in the privileged Factory deployment.

## Private service contract

Run `python -m omniagents.integrations.power_automate_gateway --config /etc/bridge/config.json`
on the isolated host. Configuration requires `listen_host`, `certificate`, `key`,
`client_ca`, a fixed `not_before`, and explicit `sources`. Each source has an
`operations` allowlist; permitting `teams.reply` also requires exact `tenant_id`,
`team_id`, and `channel_id`. Only the service receives the database connection
environment variable. Its listener requires client certificates; no public ingress
or plaintext listener is provided. Keep CA signing keys off both workload hosts.

- `POST /v1/poll` returns `{deliveries: [{id, source, payload}]}` without acknowledging.
- `POST /v1/ack` takes `{id, source, disposition}` (`accepted` or `rejected`).
- `POST /v1/requeue` takes `{id, source}` for locally parked deliveries.
- `POST /v1/actions` takes `{id, source, operation, payload}`; identical retries reuse
  the action, while conflicting IDs fail. Source, operation and Teams destination
  authorization are enforced server-side.
- `GET /health` checks database connectivity and also requires a client certificate.

There is no SQL, shell, file-path, or credential API. Factory keeps its policy,
approval model, activation state and reply spool. Lost acknowledgments are retried;
failed local persistence cannot acknowledge a delivery. The private service performs
the existing stale-send/transfer maintenance. Remote mode rejects `agent_access`
configuration: on-demand action/download tools require a separately designed private
API and are not enabled by this transport. Automatic Teams replies remain supported.

Rotate workload certificates before expiry. Restrict bridge ingress to the Factory
host, and allow Factory's existing egress proxy only the bridge's HTTPS destination.
Do not dual-home Factory to obtain database access. Preserve source cutoffs and
SQLite state on cutover; rotate old database passwords and revoke/retire obsolete
secret access. Rollback must not restore direct Factory database access.

Application/bot events and configured ignored actors cannot trigger responses.
If outbound posting uses a human identity, verify loop suppression without
excluding genuine users who share that identity. Intake does not grant action
approval: existing Guardian/human approval rules still apply.

## Outbound replies

Completed successful Teams runs place final-answer replies in a persistent local
SQLite spool. Polling forwards them to `outbound_actions` with deterministic IDs.
Failed forwarding retains local rows; retries do not duplicate the PostgreSQL
action. Preserve the spool with runtime state/backups. Output over 6,000
characters is shortened to that prefix plus an explicit notice; full output
remains in Factory. Automatic spooling is separate from action-tool permissions.

The dispatcher must have concurrency one across all instances sharing a source.
Read pending rows, update to `sending`, execute `teams.reply` against the stored
tenant/team/channel/root, then set `succeeded` and record returned message IDs in
`result`. Render plain text safely, not unescaped HTML. PostgreSQL connector CRUD
is not a transactional external-send API. A failed claim must not send; lost
posting acknowledgment requires `uncertain` and reconciliation, never blind
resending. Inbound intake alone cannot deliver a response to Teams.

For PostgreSQL connectors requiring all non-null fields on Update row, apply
`omniagents/integrations/power_automate_connector_updates.sql` to an existing
installation. Fresh installations include these permissions and guards in
`power_automate_roles.sql`. The migration replaces the action guard and grants
column-level UPDATE on source, operation, payload, created_at and state_changed_at
in one transaction; no tables or columns are added.

Resubmit those fields unchanged from the selected row when claiming it. For the
success/uncertain update, use the row returned by the claim. The guard ignores
connector-supplied action metadata and timestamps, preserving their stored values;
valid transitions generate state_changed_at in PostgreSQL. Timestamp precision
changes during connector serialization therefore cannot reject a valid claim.
Keep copying the required fields rather than generating replacement values.
Row IDs, agent and session fields
remain non-updatable by the connector. Duplicate claims, terminal replay and
cross-source access remain blocked. Use Flow bot for Teams posting and disable
automatic Teams action retries; ambiguous failures require reconciliation.

Completion status/final output commit in one EventStore transaction. At startup
and each poll, up to 100 completed Teams outputs without recovery receipts are
revalidated against current source/actor/destination and activation policy and
spooled. A receipt follows the spool write; callback/recovery share action
identity. Revoked or invalid events get suppression receipts. Pending activation
receipt cases can wait for intake recovery rather than being prematurely sent.
This is not exactly-once Teams delivery: outputs lost before durable completion,
lost storage, interrupted runs, or an uncertain external send are not solved by
automatic rerunning/reposting.

## Separate attachment worker and transport

Only deployments using the existing Blob-backed file features need a supervised,
non-root worker:

This worker must run in the isolated bridge trust domain, not on Factory's host.

```bash
python -m omniagents.integrations.power_automate_files
```

Provide `OMNI_POWER_AUTOMATE_DSN`, `OMNI_BRIDGE_BLOB_URL`,
`OMNI_BRIDGE_BLOB_CONTAINER`, and optionally `AZURE_CLIENT_ID` for user-assigned
managed identity. The worker neither creates the container nor grants itself
permissions. Use container-scoped Blob data access, private DNS/networking and
an environment with managed identity available only to trusted services. Reserve
temporary disk for the largest accepted file per worker.

In `omni_bridge.attachments`, insert UUID `id`, `source`, `filename`,
`content_type`, `byte_size`, `chunk_count`, and optional lowercase hex
`expected_sha256`. State defaults to `uploading`. Bounds: 1 GiB and 1–2,048
chunks, not a promise of cloud connector file-size support. Insert
`attachment_chunks` with deterministic `id` (for example `<UUID>:<index>`),
`attachment_id`, zero-based `chunk_index` and base64 `content`. Each field is
bounded to 1,048,576 ASCII characters (786,432 decoded bytes). Split an existing
base64 encoding on multiples of four characters without re-encoding. Empty files
use one empty chunk. Set the attachment manifest `complete` only after all chunks
are acknowledged. These tables/states are separate from inbound JSON events.

The worker streams ordered rows through a temporary file, checks count, order,
decoded length and optional checksum, then uploads to
`attachments/<UUID>/<SHA256>` with content type `application/octet-stream`. It
always computes SHA256, but without a supplied hash this is not independent
source-integrity proof. It marks `ready` and removes chunks only after upload.
A crash retries the same Blob key and checks existing metadata/length; invalid
transfers are rejected. Runtime/download credentials remain outside sandboxes.

`power_automate_download_attachment` authorizes ready same-source attachments
recorded in the current Teams conversation's attachment metadata or returned by
its successful SharePoint file requests. It validates Blob size/SHA256 in private
host staging, then streams into a new random directory in the active host/sandbox
workspace. It takes no model-supplied host path and does not overwrite files;
staging is removed after success/failure. Unsupported workspace backends fail
explicitly. Generic inbound content and raw `attachment_ids` are not automatically
converted into this metadata; use the separate authorized request/result path
when an on-demand download is intended.

## Agent actions (separate from generic inbound content)

The following APIs describe the embedded adapter. They are deliberately disabled
in private-bridge mode; do not add database credentials to Factory to enable them.

Action tools require a currently authorized Teams conversation and explicit
per-agent source permissions. They are not automatically enabled for `json`
deliveries. Add under the Teams source, with the real project agent key:

```yaml
agent_access:
  assistant:
    operations: [teams.reply, sharepoint.get_file, sharepoint.update_item, outlook.send_email]
    download_attachments: true
    sharepoint_sites:
      site-id:
        libraries: [library-id]
        lists:
          list-id: [Status]
    mailboxes: [factory@example.org]
    recipients: [authorized-recipient@example.org]
```

Enable `power_automate_request`, `power_automate_status` and
`power_automate_download_attachment` in the intended agent's tool selection.
Tools bind the actual service/agent/session; the model cannot choose another
source/conversation. Destination, site/library/list/field and mailbox/recipient
allowlists are checked before insertion, and conversation policy is revalidated.
Permissions authorize operations, not a per-action human approval workflow; keep
writes narrow and existing Guardian/approval controls intact.

`power_automate_request(operation, payload_json, idempotency_key)` supports:

| Operation | Payload fields | Dispatcher responsibility |
| --- | --- | --- |
| `teams.reply` | `text` | Plain-text reply to stored tenant/team/channel/root, not a model-selected destination |
| `sharepoint.get_file` | `site_id`, `library_id`, `file_id` | Scoped connector retrieval; create separate attachment manifest/chunks and return `result_attachment_id` |
| `sharepoint.update_item` | `site_id`, `list_id`, `item_id`, `fields`, `etag` | Enforce explicit item version/ETag, never wildcard or unconditional fallback |
| `outlook.send_email` | `mailbox`, `to`, `subject`, `body` | Authorized mailbox and exact recipients (case-insensitive matching); plain-text body |

Reuse the same idempotency key and identical content on retries; changed content
under the same key fails. Keys are at most 256 UTF-8 bytes, scoped by
source/agent/session. Payload/result text is bounded to 128 KiB. Requested reply
text is at most 16,000 UTF-8 bytes; email subject/body at most 998/65,536 bytes,
with 1–50 recipients. Status and file-result access share agent/session scope.
Connector results are untrusted data, not instructions.

The dispatcher validates operation/payload, uses least-privilege connections,
serializes claims per source, and records
`pending → sending → succeeded/failed/uncertain`. For successful get-file
requests, also set `result_attachment_id`; status exposes that UUID, but download
requires its same-source attachment manifest to be `ready`. Do not mark success
merely because work was queued, or invent a new key to bypass uncertainty.

## Recovery and cleanup operations

The poller runs maintenance immediately at startup and then every
`maintenance_seconds`. Settings alongside `poll_seconds`:

```yaml
maintenance_seconds: 60
action_timeout_seconds: 3600
transfer_expiry_seconds: 604800
```

These must be integer seconds: maintenance 10–3,600; action timeout
60–2,592,000; transfer expiry 3,600–7,776,000. Choose deadlines longer than
legitimate processing before activation.

- Stale `sending` actions become `uncertain`, retaining payload/result. Nothing
  automatically returns them to pending or repeats their external effect.
- Database triggers timestamp transitions; claiming an already-sending action
  fails. Flow-side late acknowledgment cannot resolve uncertainty or reopen a
  terminal action. An authorized operator must verify the external message,
  email, item or file, then use a trusted role inheriting `omni_bridge_worker` to
  set `succeeded`/`failed` and record evidence. If unverifiable, leave uncertain.
- Expired `uploading`/`complete` **attachment** manifests become `rejected` with
  `transfer_expired`; their chunks are removed. Expiry is since the last state
  transition, not last chunk activity; completion starts a fresh deadline.
  Already-rejected transfers lose remaining chunks but retain their error.
- Each pass locks/skips busy rows and handles at most 100 actions and 100
  attachment manifests for configured sources. Workers share manifest locks;
  late chunk inserts into rejected transfers fail. Cleanup retains events,
  manifests, action records and ready Blob objects; it scans no arbitrary paths.
- Inbound event retention is separate from attachment cleanup.

Maintenance logs counts only and requires a running poller. Monitor uncertainty,
expired transfers and worker availability. Upload success followed by failed
manifest acknowledgment may leave an orphan Blob after expiry; orphan lifecycle
and long-term sensitive-data retention remain deployment responsibilities.

## Verification and rollout

Run the `test_power_automate*` suites, including the disposable PostgreSQL suite
with `OMNI_BRIDGE_TEST_DSN`. Never use a production database for those tests.
Verify trusted runtime private DNS/routing, scoped credentials, dependencies,
persistent state, automation routing and outbound loop suppression before enabling.
Update the flow to include context, set rollout cutoffs, then insert a fresh test.
Code tests alone do not prove the backend is deployed or connected.

## Agent-posted Adaptive Card conversations

When a run uses `teams_notify`, Factory records an opaque notification correlation
identifier in its existing local Power Automate SQLite database, together with the
trusted originating agent/session and configured source. The posted card text
includes `[omni-conversation:<identifier>]`. Preserve that footer when forwarding
or rendering the card. It is a correlation identifier, not an approval token.

The existing inbound flow supplies the card message/root IDs and raw thread
history, including Adaptive Card attachments. Factory recognizes a registered
identifier only on an application-authored root in the configured destination.
It binds that root to the originating conversation without dispatching the bot
message. Subsequent authorized human replies need no Factory marker and run as
the originating agent in its existing session, rather than the channel default.
Root history also permits registration when a human reply is delivered before
the card event. A registered identifier cannot bind a second root.

No Power Automate flow changes, PostgreSQL schema changes, new bridge endpoints,
or database access from Factory are required. Existing final replies still use
`teams.reply` and the current outbound flow. Preserve both the intake database
and Power Automate spool database across restarts; the new local table is created
idempotently on startup. Existing cards posted before this release have no
correlation footer and retain the existing marker activation behavior.

This does not turn ordinary Teams text into an approval or resolve a pending
`ask_user`/escalation response. Existing authenticated Workplace controls remain
required for those operations. Other application/bot messages remain suppressed.
