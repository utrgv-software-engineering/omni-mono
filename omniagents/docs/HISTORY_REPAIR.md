# Historical transcript audit and repair copies

`enqueue_message(trigger_run=False)` formerly persisted legacy history without
recording a canonical item. The runtime fix prevents new omissions. Existing
history cannot safely be merged wholesale: legacy rows lack queue/run identity,
assistant chunks may differ, and compaction/forks change what remains in context.

## Read-only audit

Run from the backend repository with its Python environment:

```bash
.venv/bin/python -m omniagents.core.conversation.history_audit \
  --history /absolute/path/sessions.db \
  --domain /absolute/path/sessions.db.domain
```

Optional `--thread SESSION_ID` limits the scan. Both inputs must already exist.
The tool opens SQLite in read-only/query-only mode, without application-store
initialization, schema migrations or thread materialization. It reports counts,
thread IDs and candidate row IDs, never message text. It supports plaintext
SQLite only; encrypted/unreadable content is refused, not treated as empty.

Statuses:

- `matched`: legacy and canonical **message/checkpoint sequences** match. This is not a
  completeness verdict on tools, approvals, artifacts or other item types.
- `legacy_only`: no canonical thread; normal legacy projection owns this case.
- `active`: a running turn exists; do not repair an active session.
- `review_required`: ambiguous difference, not proof of corruption. Compaction
  and non-user message differences can be legitimate.
- `repairable_suffix`: every canonical message matches the legacy prefix and
  the remaining raw history is exclusively nonempty plain user messages, with
  no later non-message canonical item that would make ordering ambiguous.

Internal `omniagents.kind` checkpoint rows are compared with canonical
compaction items, not counted as assistant messages. Counts and separate
`message_sequences_match` / `checkpoint_sequences_match` flags distinguish
content differences from relative checkpoint-order differences. Matching
messages with a shifted checkpoint still require review; the audit does not
silently discard checkpoints or declare their order correct.

## Repair to a new copy only

Stop the owning server first and retain the original history/domain databases.
Audit again after shutdown. For one `repairable_suffix` thread:

```bash
.venv/bin/python -m omniagents.core.conversation.history_audit \
  --history /absolute/path/sessions.db \
  --domain /absolute/path/sessions.db.domain \
  --thread SESSION_ID \
  --repair-copy /absolute/path/new-repaired-domain.db
```

The output must not exist (including symlinks). It is created with mode `0600`.
The tool copies the canonical database with SQLite backup, then atomically adds
only the suffix and updates its thread counts/cursor. Existing item identities,
revisions, turns, controls, search data and sidebar timestamps are preserved.
Recovered records are explicitly `source=adapter`, have no inferred run ID,
and carry `history_repair` provenance with their source history row ID. Creation
time is recovery time, not a fabricated original timestamp.

Re-auditing the copy reports `matched`; rerunning repair on that copy returns a
no-op. A repair failure never modifies the inputs, but may leave a partial output
copy: discard/quarantine that output, do not install it. Original databases are
never replaced automatically. Inspect the repaired copy before considering any
installation, and never swap database files under a running server or ignore WAL
sidecars. This utility is not a PostgreSQL migration or a general repair for
missing middle messages, system/assistant injections, multimodal content, or
non-message canonical objects.
