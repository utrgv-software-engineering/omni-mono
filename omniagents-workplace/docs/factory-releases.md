# Factory self-improvement releases

An improvement PR changes the factory's versioned agents, instructions, skills,
automations, or evaluation fixtures. It does not change its running version.
A reviewer merges the PR; CI validates the source and publishes an image built
from an immutable framework image. CI then requests activation of that exact
commit and image digest. A deployment operator separately approves both.

The controller in `scripts/factory_release_controller.py` verifies the current
linked repository policy, default-branch revision, approved workflow hash, and
successful named jobs using its own provider credentials. GitHub and the
Workplace GitHub stand-in use the same verifier. Configure branch protection
where supported; disabling that check does not disable the separate operator
approval. The CI credential cannot invoke the approval command.

The current runtime implementation targets a Docker-hosted **personal** factory.
The operator hooks use the existing generalist outcome scenario and authenticated
Personal chat connection. This is not yet an implementation of pipelined runtime
promotion or replacement of the entire Workplace installation.

## Activation

1. CI sends only `REVISION`, `RUN_ID`, and `IMAGE_DIGEST` to a fixed SSH command.
2. The controller verifies evidence and records `awaiting_approval`. It does not
   stop the factory. `factory_release_approve.py CONFIG REVISION DIGEST` records
   the operator's exact approval; invoke the controller with its saved request.
3. A busy factory records `deferred`. `factory_release_retry.py CONFIG` can run
   from a timer; it retries only this state, not failed evaluations or rollbacks.
4. The candidate must match the pinned framework and revision, parse successfully,
   and pass the existing `factory_clear_request` generalist outcome scenario in
   isolated fixtures. Evaluation results are cached only for the exact source,
   artifact, framework, and operator evaluation version.
5. The controller takes a backup, rechecks evidence and policy, and acquires an
   idle lease. New chat work cannot enter during the lease; incoming integration
   events are recorded durably and resumed after restart.
6. It retains the old container and mounts, starts the candidate paused, and
   checks the authenticated runtime version and actual chat history. Only after
   those checks does it commit the restart manifest and admit new work.
7. Failure restores the retained container and previous manifest. It does not
   delete or restore customer data volumes. An interrupted release blocks normal
   runtime management until `factory_release_recover.py CONFIG` recovers it.

A factory runtime restart has a brief chat reconnect window. This is not a
zero-downtime replacement of the factory control plane. Application deployments
and databases are separate from this runtime and are not restarted by it.
Backward-incompatible data migrations are outside this release path: retaining an
old container cannot reverse a destructive data migration.

## Operator installation

Install the controller, approval, retry, recovery, hooks, chat, and eval scripts
in a root-owned directory outside the factory's credentials and writable source.
Install `omniagents_workplace/deployments/releases.py` alongside the controller as
`release_evidence.py`. Configure a dedicated SSH user with `restrict` and a forced
command, and permit sudo only for the fixed wrapper with no arguments. No shell,
Docker access, arbitrary config paths, or approval command belongs in that user’s
permissions. Keep model, registry, and provider credentials out of Git and logs.

Controller configuration contains these operator-owned values:

- Identity/evidence: `factory_id`, `provider`, `repository`, `branch`, `workflow`,
  `workflow_sha256`, `required_checks`, `require_branch_protection`, and
  `evidence_token_file`. Stand-in additionally needs its scoped `api_url`.
- Runtime: `container`, `network`, `runtime_env`, `state_directory`
  (the managed factory's `releases` directory), `runtime_token_file`, and
  `runtime_control_token_file`.
- Policy/approval: `policy_url`, `policy_token_file`, and `approval_file` outside
  the runtime's mounted state.
- Artifacts: `image_repository`, `framework_image`, `docker_config`.
- Fixed commands: `validate_command`, `evaluation_command`, `backup_command`,
  `snapshot_command`, `chat_check_command`, and `evaluation_version` (change it
  whenever operator evaluation code or policy changes).

The provided hook adapter also needs `operator_directory`, `session_id`,
`workplace_container`, `backup_container`, and `eval_codex_directory`. It runs
agents outside their tool sandboxes, using isolated fixture services and only a
read-only model credential mount. Evaluation temporary paths are mounted at the
same host/container path so sibling Docker tool sandboxes can access them.

Keep backups and operator records separately from application data, with restricted
permissions. Off-server backup scheduling remains an installation responsibility.
The retained stopped container is a rollback artifact, not a substitute for backup.

The legacy template supervisor follows a branch only with explicit
`FACTORY_PROMOTION_MODE=branch`; release mode must have one promotion authority.
Do not enable branch-following alongside this controller.

## What survives a runtime release

| State | Runtime release behavior | Recovery coverage |
| --- | --- | --- |
| Factory source and framework | Exact image digest; previous container retained | Git plus registry artifacts |
| Chat, memory, queues, repository settings and credentials | Original factory directory is retained | Factory backup uses SQLite snapshots and copies config/credentials |
| Tool sandbox filesystem and worktrees | Separate persistent sandbox is retained | Not included in this runtime backup; preserve/snapshot its storage separately |
| Application databases, uploads and Coolify | Not touched by factory runtime replacement | Separate application and Coolify backup policies |
| In-flight model work | Activation waits for idle | Not resumed from an arbitrary instruction boundary |
| Webhooks received during the release pause | Recorded before acknowledgment, dispatched after restart | Durable integration event store |

The backup hook intentionally covers the whole managed factory directory. Never
put its backup destination inside that directory. Operator credentials, controller
configuration, and approval/audit records also need their own restricted backup.
