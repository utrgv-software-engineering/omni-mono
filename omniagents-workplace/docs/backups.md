# Backups under the strict policy

`omniagents-workplace backup` copies a running Workplace's state consistently
(online SQLite copies; repositories refs before objects). Under
`WORKPLACE_POLICY=strict`, or with the flags below elsewhere:

- **No encryption keys** (`--without-keys`). Keys unlock every stored
  credential, so they are never in a data backup. Escrow them apart:
  `omniagents-workplace keys export --data <data> --to <empty dir>` copies every
  key by its path, for the deployment to put in Key Vault. A production restore
  takes them back: `restore --keys <dir>`.
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
