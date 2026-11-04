# Azure deployment: build plan

Status: proposed; not implemented. Recorded 2026-11-04.

What has to be built in the codebases to carry out the
[Azure deployment plan](azure-web-compute-plan.md). Each workstream names what
already exists, what to build, and where. Unless marked *Azure profile*, a
feature ships in every profile: the 80/20 deployment dogfoods it and local
installations keep working without Azure.

Repositories:

- **omni-mono**: `omniagents/` (runtime, approvals, sandboxes, runtime audit) and
  `omniagents-workplace/` (Workplace: settings, UI, backups, event gateway).
- **omni-factory**: the factory's definition (`project.yml`, agents, evaluations)
  and its deployment (`deploy/`), including the new Azure infrastructure.
- **quick-sandbox**: the VM sandbox package omniagents depends on, for the egress
  mode in workstream 4; not in these repositories.

Sizes are relative: S is days, M is about a week, L is several weeks.

## Order

1. **Kill switch.** Texas already requires it (1 TAC §219.11, in force since
   March 18, 2026), and it is useful everywhere. Build it first.
2. **Audit log and deployment policy.** The foundation for every later setting.
3. **Classification, approved list and forced review.**
4. **Egress control.**
5. **Human approvals for named tools.**
6. **Backups.**
7. **Azure front door and infrastructure.** Can run in parallel with 3 to 6.
8. **Transparency and oversight features.**
9. **Pilot**: an example factory with no sensitive data, registered in the AI
   Inventory, before any real work.

## 1. Kill switch (M)

Exists: installation administrators can stop one factory
(`POST /workplace/api/factories/{id}/stop`, `Installation.stop`), which
terminates its runtime (45-second grace) and turns off autostart.

Build, in omniagents-workplace:

- **State.** A durable stop on each factory record and one for the whole
  installation: `{stopped_by, stopped_at, reason}`. Stopping all does not change
  any factory's own state.
- **Permissions.** Factory administrators stop and resume their own factory;
  installation administrators any factory and all factories. A stop made by an
  installation administrator needs one to resume. Stop and resume require a
  reason.
- **Stopping.** Cancel in-flight turns, workers and automation runs, stop the
  runtime with a 10-second grace instead of 45, and suspend the factory's
  QuickSand VMs so no sandbox command keeps running. This covers both the
  in-process runtime (`factory_process.py`) and the Docker-hosted factory
  (`docker_factory.py`) the 80/20 deployment uses.
- **Gates.** While stopped, Workplace refuses to start the runtime, refuses chat
  messages (with who stopped it and why), and skips automation dispatch. The
  event gateway records incoming events with a new `skipped` status instead of
  leaving them pending, so `deliver_pending` never replays them after resume.
- **Runtime check (omniagents).** Workplace writes a stop marker into the
  factory's runtime directory; the runtime checks it before every model call and
  ends the turn if present. This stops a loop that escaped cancellation even
  when Workplace is unreachable.
- **Fail closed.** If Workplace cannot read a factory's stop state, it treats the
  factory as stopped.
- **UI.** A Stop/Resume control on the Factory tab, Stop all/Resume all for
  installation administrators, and a banner in the factory's chats.
- **Audit.** Every stop and resume (workstream 2).

Tests: stop during an active turn, a worker and an automation; events received
while stopped are skipped and not replayed; stop survives Workplace and runtime
restarts; resume rules by role; a turn halts at its next model call when only
the marker is present.

## 2. Audit log and deployment policy (M)

Exists: Workplace's `security.audit()` writes JSON log lines for sign-ins,
invites and tokens, and keeps nothing. omniagents has a structured,
hash-chained runtime audit (`core/audit`: run, tool, approval and incident
events), off by default, which records up to 4096 characters of tool input and
output.

Build:

- **Workplace audit store.** An append-only table in `workplace.db` for every
  security and settings event: actor, factory, event, before and after values,
  reason. Also written to stdout as JSON so the VM's monitoring agent ships it to
  Log Analytics (six-year retention is set there, not in Workplace). Migrate the
  existing `audit()` callers to it.
- **Runtime audit for factories.** Enable omniagents' audit in factory runtimes
  with `max_detail_chars: 0`, so the audit records who did what without
  conversation content and does not become a store of sensitive data. Add the
  approving person's identity to approval events.
- **Deployment policy.** One place in Workplace that holds what the profile
  requires, set in `workplace.yml` (`policy: strict` for Azure): classification
  required, review forced, approved list enforced. Every rule in workstreams 3–6
  reads it there, never from scattered flags.

## 3. Classification, approved list and forced review (M)

Exists: per-factory settings in each factory's store; `factory.definition.require_review`
(off by default); omniagents' `security.mcp_require_allowlist` and
`mcp_allowed_servers`; the Factory model page writes the factory's `models.json`.

Build, in omniagents-workplace:

- **Factory settings.** `classification` (Published, Controlled, Confidential),
  `heightened_risk`, `purpose` and `owner`. Required under the strict policy;
  a factory without a classification does not start. Every change is audited.
- **Approved list.** An installation-level list of model endpoints, integrations
  and MCP servers, each with its assessment reference. Enforcement:
  - models: under the strict policy every factory's `models.json` may name only
    approved endpoints; the Factory model page refuses others and the runtime
    refuses to start with one;
  - MCP servers: for Controlled and Confidential factories, Workplace sets
    omniagents' `mcp_require_allowlist` and `mcp_allowed_servers` from the list,
    in a configuration layer the factory's project cannot override;
  - integrations: Workplace refuses to connect an unapproved one.
- **Forced review.** For Controlled and Confidential factories `require_review`
  is on and the setting is locked.
- **The factory cannot merge its own changes.** The stand-in GitHub already
  refuses self-approval. Verify that branch protection on the factory repository
  requires an approving review, in the stand-in and on GitHub, and that the
  factory's token cannot merge without one; fix whichever does not.
- **Self-improvement containment.** For Confidential factories, refuse any path
  that publishes improvement output beyond the factory's own repository, and add
  a required check on improvement PRs that scans the diff with omniagents'
  content scanner (Presidio or regex) for personal data.

Verify: that omniagents' layered configuration can hold a layer the project
cannot override; build one if not.

## 4. Egress control (L)

Exists: QuickSand network modes `none`, `mounts_only` and `full`; Docker sandbox
network modes. Nothing filters destinations.

Build:

- **Deployment allowlist** (omni-factory, *Azure profile*): Azure Firewall rules
  in the infrastructure code, denying outbound by default and allowing model
  endpoints, package registries and approved integrations.
- **Per-factory allowlist for Confidential factories**:
  - an allowlisting forward proxy on the VM, one configuration per factory,
    whose denials are logged and audited;
  - QuickSand: a network mode where the guest reaches only that proxy. QEMU's
    user networking supports this (`restrict=on` with a `guestfwd` to the
    proxy); add it to quick-sandbox, or set it from omniagents'
    `core/sandbox/quicksand.py` if the package lets it pass QEMU options;
  - the factory runtime's container: a Docker network whose only route out is
    the proxy;
  - Workplace: the allowlist as a factory setting, audited, with widening it a
    material change.

## 5. Human approvals for named tools (L)

Exists: per-tool `needs_approval` predicates and MCP approval keyed by
`(server, tool)` (`core/agents/approval.py`); routes HUMAN, GUARDIAN and
AUTO_APPROVE (`approval_policy.py`); omni-factory sends every approval to the
Guardian; Workplace shows and answers approvals only in a person's own chat
(`personal.py` `decide`).

Build:

- **omniagents.** `security.human_approval_tools` in `project.yml`: tool names,
  patterns and MCP `(server, tool)` identities. For those tools the route is
  always HUMAN: autopilot's allow-all pattern, session always-allow, the
  Guardian, `skip_approvals` and contained-sandbox auto-approval never cover
  them. Because the list lives in the factory repository, changing it is a
  reviewed PR.
- **Workplace approval inbox.** A factory-wide list of pending human approvals
  from every conversation and automation run, visible to the factory's
  approvers (its administrators by default). Approvers are notified in Slack or
  by email. A decision goes through the existing `tool_approval_response` and
  `mcp_approval_response` calls and is audited with the approver. A pending
  approval expires after seven days, rejecting the call.
- **Durable waiting.** Verify that a run paused on an approval survives a
  runtime restart; if not, persist the paused run state so approvals are not
  lost.

Tests: a named tool pauses under autopilot, Guardian and always-allow; an
automation run's approval appears in the inbox and resumes on approval; expiry
rejects; approvals survive restart.

## 6. Backups (M)

Exists: `omniagents-workplace backup` (online SQLite copies, `--include`,
`--exclude` patterns, `--keep`), run nightly by the Compose `backup` service;
the personal workspace's VM backup. Backups currently include
`factory-secrets.key` and `github-connection.key`.

Build:

- **Keys out of data backups.** Exclude the encryption keys; under the strict
  policy Workplace reads them from Key Vault (*Azure profile*), and restore
  takes them from there.
- **Confidential exclusions.** Exclude Confidential factories' Shared uploads
  and agent workspaces, and skip their personal workspace VM backups.
- **Blob Storage target** (*Azure profile*): upload each backup to a Blob
  container with a 30-day lifecycle rule; keep the local copy only until the
  upload succeeds.
- **Deletion cascades.** Deleting a conversation removes its worker journals
  and traces too; verify today's behavior and fix what is left behind.
- **Restore drill.** A scripted restore to a fresh VM (omni-factory), run
  quarterly; its result is recorded.

## 7. Azure front door and infrastructure (L)

Exists: the Easy Auth adapter (`easy_auth.py`), which assumes Workplace runs on
App Service itself; the 80/20 Compose stack and its DigitalOcean overlay
(`deploy/digitalocean.compose.yml`).

Build:

- **Easy Auth behind a proxy** (omniagents-workplace): accept identity headers
  only from configured trusted proxy addresses (the App Service integration
  subnet), and refuse them from anywhere else, including sandboxes and preview
  containers. Previews get the same rule plus factory access.
- **App Service proxy** (omni-factory `deploy/azure/`): a reverse-proxy
  container for App Service with WebSockets, streaming and forwarded headers.
- **Infrastructure code** (omni-factory, Bicep): VNet and subnets, NSGs, the VM
  (a nested-virtualization SKU) and its managed disk, App Service with VNet
  integration, Key Vault, Blob Storage with a private endpoint and the lifecycle
  rule, Azure Firewall, Log Analytics with six-year retention and alert rules,
  and App Service's health-check alert.
- **Azure Compose overlay** (omni-factory): `deploy/azure.compose.yml`, the same
  stack with no public ports, `policy: strict`, keys from Key Vault and backups
  to Blob Storage.
- **Runbook**: out-of-band stops (disable the model deployment's keys, stop the
  VM), restore, and who holds which access.

Setup only: Entra user assignment limited to the AI-training group, factory
membership from Entra groups, Conditional Access MFA, Azure Update Manager.

## 8. Transparency and oversight (M)

Build, in omniagents-workplace unless noted:

- **Material changes.** Workplace compares each settings change, and each change
  to the factory repository's models and `project.yml`, against the definition
  of a material change; it flags the factory "reassessment needed" with what
  changed, until an administrator records a reassessment.
- **System card.** A page and export on the Factory tab, generated from the
  factory's settings and configuration: purpose, owner, classification, risk,
  models, integrations, MCP servers, egress allowlist, retention, tools needing
  approval and the last material change.
- **AI attribution.** Verify that every integration posts as the factory's own
  app identity and that the identity's name and description say it is AI; add
  an `AI-Generated-By: <factory>/<agent>` trailer to agent commits and
  generated-by metadata to Shared documents.
- **Factory tab metrics.** Add error rate across runs, tool-failure rate and
  runtime uptime (record runtime up and down transitions).
- **Heightened-risk review.** For heightened-risk factories, sample
  conversations at the owner's rate into a review list on the Factory tab, and
  remind owners when the six-monthly reassessment is due.
- **Adversarial evaluations** (omni-factory): scenarios for prompt injection
  through fetched content, data sent to a destination not on the allowlist, and
  data crossing between factories; required on every material change.
- **Incident alerts.** Kill-switch use, blocked egress from Confidential
  factories, repeated sign-in failures and policy-rejected changes are alert
  events, sent to configured Information Security Office contacts by email or
  Teams webhook.

## Verifications before building

Facts this plan assumes that must be checked in code first:

- the layered configuration can lock a layer against the project (workstream 3);
- branch protection requires an approving review and the factory cannot merge
  alone (workstream 3);
- a run paused on an approval survives a restart (workstream 5);
- deleting a conversation removes its journals and traces (workstream 6);
- every integration posts as an identifiable app identity (workstream 8);
- QuickSand VMs can be suspended and resumed by the kill switch (workstream 1).
