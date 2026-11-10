# Releases from GitHub or the stand-in

Both providers can build an image in an ordinary workflow and request a Coolify
deployment through an independent controller. The reference application has been
deployed through GitHub Actions and Workplace's real `act` runner. This does not
switch an existing application's repository provider or deployment path.

## Trust boundary

The workflow sends a commit SHA, workflow run ID, immutable image digest, and
(for private GitHub) its short-lived read token over restricted SSH. The forced
command chooses a root-owned configuration; callers cannot choose the repository,
required checks, workflow hash, Coolify application, host, or route.

`deployments/releases.py` retrieves evidence from either GitHub or the stand-in
and requires all of the following:

- The configured repository, default branch, push event, workflow path and
  approved workflow content match.
- The run's commit is still the default branch's current commit.
- Every configured required check completed successfully. Missing, skipped,
  failed and unfinished checks cannot authorize deployment.
- The request names an immutable `sha256:` image digest.
- Branch protection is enabled, unless the operator explicitly configured a
  provider without that feature. The other controller checks remain mandatory.

The reference controller also verifies the image's revision label, stages it,
and rechecks source evidence before replacement. A separate fallback container
serves the previous image while Coolify deploys. Failed replacement health checks
restore the previous image. An interrupted recovery leaves a durable
`recovery_required` record and blocks further releases for operator inspection.

The runnable operator scripts are `scripts/release_controller.py` and
`scripts/reference_release_host.py`. Install `deployments/releases.py` beside
them as `release_evidence.py`. The host script is specifically for the reference
counter application: its data snapshot and persistence checks are **not** a
general database migration or application deployment implementation. Extend the
host steps for an application's own health checks, storage and workers.

Controller configuration selects the provider, repository, branch, workflow,
approved SHA-256, required check names, state directory, Coolify endpoint and app,
registry and target image, app host, SSH key, data directory and allowed route.
Credentials are file references owned by the operator. Install one forced SSH
command per configuration, with forwarding, PTY, arbitrary commands and password
authentication disabled. Do not give CI a general-purpose server SSH key.

## Stand-in workflow configuration

In **GitHub → repository → Settings → Workflow secrets and variables**, an
administrator can set variables and secrets and approve an exact workflow version.
Saved secret values are never returned to the browser. Settings are encrypted
with the factory's own key, separate from GitHub connection credentials.

Secrets are supplied only to approved workflow content on a protected default
branch push. PRs and feature branches receive no deployment secrets. Workflow
edits require another approval. Approval does not permit a stale commit to deploy.

For initial setup, merge the reviewed workflow, cancel any unconfigured bootstrap
run, approve its default-branch version, then rerun. The first workflow PR may
need an explicit administrator bootstrap merge when no separate reviewer is
available; that is not evidence of independent review.

Variables for the reference workflow are `REGISTRY`, `REGISTRY_USER`,
`IMAGE_NAME`, `DEPLOY_HOST`, `DEPLOY_PORT`, and `DEPLOY_USER`. Secrets are
`REGISTRY_PASSWORD`, `DEPLOY_KEY`, and `KNOWN_HOSTS`. GitHub uses its native
repository variables/secrets; the stand-in provides the same workflow contexts.

An administrator creates a separate, repository-scoped evidence credential with
`POST /workplace/api/github/repos/{owner}/{name}/workflow-settings/release-token`.
Keep the returned token on the controller, not in the workflow. It can only read
`GET /github/release-evidence/{owner}/{name}/{run_id}`. Prefix both URLs with the
installation's `/factories/{id}` scope. Rotating it revokes its predecessor.

Completed non-matrix jobs are published while the workflow is running, allowing
the deployment job to verify its dependencies. Matrix instances remain aggregated
until workflow completion; use a separate non-matrix gate job depending on the
matrix for an in-workflow deployment check. Its script must explicitly fail when
the matrix dependency failed or was skipped.

## Recovery boundaries

`omniagents-workplace backup --include NAME=PATH` adds runtime/configuration/source
directories to the Workplace snapshot. SQLite files use online snapshots,
including uncheckpointed WAL data. Stand-in Git repositories, library data,
installation records, GitHub connection keys and workflow encryption keys are
included. Additional directories and exclusions are recorded in
`backup-metadata.json`.

The factory template's daily backup includes runtime home, configuration, model
sign-in, shared files, live checkout, framework source and factory source. The
persistent personal sandbox has a separate image backup. Disposable CI containers
and caches are not durable application state.

Coolify's live Docker storage directory is deliberately excluded: copying a live
Docker engine is not a consistent database backup. Save its PostgreSQL database
with a logical dump and retain its configuration, encryption key and resources.
Application databases and uploads on deployment targets need their own backups.
Keep operator release-controller configuration, keys and release records too.
Neither a code repository nor an image rollback restores customer data.

For production recovery, stop the original installation and restore into separate
storage first:

```sh
omniagents-workplace restore --from /backups/workplace-TIMESTAMP \
  --data /restore/workplace --mode production --confirm-production-stopped \
  --restore-include factory-home=/restore/factory-home \
  --restore-include factory-config=/restore/factory-config \
  --restore-include model-sign-in=/restore/model-sign-in \
  --restore-include factory-live=/restore/factory-live \
  --restore-include shared=/restore/shared \
  --restore-include framework-source=/restore/framework-source \
  --restore-include factory-source=/restore/factory-source
```

Supply a destination for **every** additional directory listed by that backup.
These must be empty and must not overlap each other or the backup. Production
mode preserves integration identities; default staging mode disconnects saved
GitHub and workflow credentials and retains the installation registry as
`installation.snapshot.db` for inspection. A staging host starts with a fresh
registry instead of adopting production paths/container IDs or autostart settings.
A staging copy is not permission to start
copied production runtimes, workers or deployment services: use isolated storage,
networking and configuration during recovery checks.

Encrypt archives before transferring them off the server, keep the recovery key
separately, and actually decrypt and restore a copy. Daily snapshots on the same
Droplet do not survive loss of that Droplet. The live proof performed a manual
off-server copy; recurring off-server delivery and Coolify dump scheduling are
separate operational configuration.

## Factory self-improvement

An improvement PR and deployment of the factory itself are separate operations.
The UI's self-improvement switch remains off by default and requires a linked
factory repository. These example releases do not enable factory self-promotion.

For Docker-hosted personal factories, [factory runtime releases](factory-releases.md)
provides an exact-artifact approval gate, focused outcome evaluation, idle lease,
authenticated chat/history verification, retained-container rollback, and managed
restart manifest. It replaces only the selected factory runtime. Replacing the
whole Workplace host and promoting pipelined runtimes are separate work.
The legacy supervisor does not follow branches in the default release mode;
explicit development branch-following must not run alongside this controller.

When updating a hosted installation, preserve its complete Compose configuration,
including `installation.compose.yml`. A healthy single-factory server is not a
successful multi-factory-host update. Check `/workplace/api/factories` as well as
the health endpoint after activation.
