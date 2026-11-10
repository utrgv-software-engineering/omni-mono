# Backups under the strict policy

`omniagents-workplace backup` copies a running Workplace's state consistently
(online SQLite copies; repositories refs before objects). Under
`WORKPLACE_POLICY=strict`, or with the flags below elsewhere:

- **No encryption keys** (`--without-keys`). Keys unlock every stored
  credential, so they are never in a data backup. Escrow them apart:
  `omniagents-workplace keys export --data <data> --to <empty dir>` copies every
  key by its path, for the deployment to put in Key Vault. A production restore
  takes them back: `restore --keys <dir>`.
  Additional directories passed with `backup --include NAME=PATH` obey the
  same `.key` exclusion, including nested paths. Escrow those directories with
  `keys export --include NAME=PATH`, using the same names. Their keys are stored
  under `additional/NAME/` in escrow and restored into the corresponding
  `restore --restore-include NAME=EMPTY_PATH` destination, not into Workplace's
  primary data directory. Root keys retain their existing relative paths;
  `additional/` is reserved in escrow and root keys cannot use that namespace.
  Escrow destinations must be empty and outside all source directories.
  Export does not follow key symlinks. Production restore rejects symlinked
  escrow and unsafe destination paths before changing the destination.
  Staging restore does not import escrow keys.
- **No Confidential working copies** (`--skip-confidential`). A Confidential
  factory's Shared uploads and its sandboxes' disks are working copies of data a
  system of record holds, recovered by extracting it again. Its database (the
  conversations, journals and settings) is still backed up. What was left out is
  listed in the backup's `backup-metadata.json`.
- **Blob Storage** (`--upload <container URL with a SAS token>`). The backup is
  uploaded under its name, in 64 MiB blocks; `--upload-only` removes the local
  copy once it is uploaded. The container's lifecycle rule deletes backups after
  30 days, so nothing deleted survives longer.

The personal workspace's VM backup (`omniagents.integrations.personal_workspace
backup`) is a sandbox disk: don't run it for Confidential factories.

For a recovery bundle with additional runtime state:

```sh
omniagents-workplace backup --data /data --to /backups --without-keys \
  --include runtime=/runtime --include config=/config
omniagents-workplace keys export --data /data --to /empty-private-escrow \
  --include runtime=/runtime --include config=/config
omniagents-workplace restore --from /backups/workplace-SELECTED \
  --data /empty-restored-data --mode production --confirm-production-stopped \
  --keys /private-escrow --restore-include runtime=/empty-restored-runtime \
  --restore-include config=/empty-restored-config
```

These are operator examples, not commands to run against an active deployment.
Keep escrow separate from data backups and use a controlled key-rotation or
quiescence procedure so a recovery bundle and its escrow are consistent.
The `.key` convention is not a general secret-content scanner: credentials in
other file types or repository history still require a deployment-specific
backup policy. Already-created backups are not retroactively cleaned by this
change; review their access and retention if they included key-bearing paths.

## Deletion

Deleting a conversation removes its history, its conversation records and
replay journal, its tool outputs, its worker journal, the sessions of the
workers it started, and its traces.

Deleting a factory (Factories page → Delete, typing its name; installation
administrators) stops its runtime and previews and removes their containers,
its conversations, files, settings and memberships, at once; it is audited as
`factory.deleted`. Its backups expire on their own schedule (30 days on Azure).
An adopted factory, whose data a deployment owns, is retired by that deployment
instead.
