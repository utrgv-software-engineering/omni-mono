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
3. **Classification, allowed services and forced review.**
4. **Egress control.**
5. **Human approvals for named tools.**
6. **Backups.**
7. **Azure front door and infrastructure.** Can run in parallel with 3 to 6.
8. **Transparency and oversight features.**
9. **Pilot**: an example factory with no sensitive data, registered in the AI
   Inventory, before any real work.
10. **Later: Microsoft Teams and Azure DevOps.** The Azure deployment starts on
    stand-ins; these replace them once the deployment is running.

The 80/20 deployment adopts each workstream as it lands, under the same
`strict` policy, so it dogfoods everything except the Azure-only parts.

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
- **Stopping, in two steps.** First, within 10 seconds, all AI activity ends:
  in-flight turns, workers and automation runs are cancelled and every sandbox
  command is terminated (`pty_terminate_all`). Then the runtime shuts down
  gracefully, which already suspends its QuickSand VMs (`suspend_durable`:
  flush the disk, stop the VM; `resume` restarts it later). Only if that takes
  more than 60 seconds is the runtime killed, and then Workplace kills any of
  the factory's remaining QEMU processes. Killing earlier would cut off the disk
  flush. This covers both the in-process runtime (`factory_process.py`) and the
  Docker-hosted factory (`docker_factory.py`) the 80/20 deployment uses.
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

Tests: stop during an active turn, a worker and an automation, with a sandbox
command running; the VM resumes from its disk after resume; events received
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
  requires, set in `workplace.yml` (`policy: strict`, on Azure and 80/20):
  classification required, review forced, allowed services enforced. Every rule in
  workstreams 3–6 reads it there, never from scattered flags.
- **Sign-in and sessions under the strict policy** (UTS 165):
  - Workplace's own password sign-in is off; people sign in through Entra (or,
    on 80/20, the existing sign-in until Easy Auth is there), and machines use
    tokens;
  - sessions end after 30 minutes without activity and 12 hours in total;
  - with Easy Auth, Workplace checks the identity headers on every request and
    ends its session when they are missing or name someone else, so removing a
    person in Entra takes effect immediately.
- **Access review export.** Every factory's members and roles, with the date of
  each grant, for the annual access review.
- **Teams notifications.** Workplace sends no email. Add an installation
  setting for a Microsoft Teams incoming webhook; approval requests (workstream
  5) and alerts (workstream 8) post to it.

## 3. Classification, allowed services and forced review (M)

Exists: per-factory settings in each factory's store; `factory.definition.require_review`
(off by default); omniagents' `security.mcp_require_allowlist` and
`mcp_allowed_servers`; the Factory model page writes the factory's `models.json`.

Build, in omniagents-workplace:

- **Factory settings.** `classification` (Published, Controlled, Confidential),
  `purpose` and `owner`. Required under the strict policy; a factory without a
  classification does not start. Every change is audited.
- **Allowed services.** An installation-level list of model endpoints,
  integrations and MCP servers that administrators keep, audited like any
  setting. It is a technical control, not an approval workflow. Enforcement:
  - models: under the strict policy every factory's `models.json` may name only
    allowed endpoints; the Factory model page refuses others and the runtime
    refuses to start with one;
  - MCP servers: for Controlled and Confidential factories, Workplace sets
    omniagents' `mcp_require_allowlist` and `mcp_allowed_servers` from the list,
    in the managed configuration layer (`OMNIAGENTS_PROJECT_OVERLAYS`), which
    the factory's `project.yml` cannot override. The allowlist matches servers
    by name only today, so a factory could give an outside server an
    allowed name: change it to match the name and the server's URL or
    command;
  - integrations: Workplace refuses to connect one not allowed.
- **Forced review.** For Controlled and Confidential factories `require_review`
  is on and the setting is locked.
- **The factory cannot merge its own changes.** In the stand-in GitHub, a
  protected branch already needs an approving review, an author cannot approve
  their own pull request, only a Workplace administrator can bypass protection,
  and the factory's merge tool never bypasses it. One gap: any account but the
  author counts as an approval, including another factory's. For a factory's
  own repository, require the approval to come from a person who is a member of
  the factory. On real GitHub, branch protection must be set the same way, with
  the factory's account outside the bypass list (setup).
- **Self-improvement containment.** For Confidential factories, refuse any path
  that publishes improvement output beyond the factory's own repository, and add
  a required check on improvement PRs that scans the diff with omniagents'
  content scanner (Presidio or regex) for personal data.

## 4. Egress control (L)

Exists: QuickSand network modes `none`, `mounts_only` and `full`; Docker sandbox
network modes. Nothing filters destinations.

Build one filtering proxy on the VM as the only route out for everything
factories run. Azure Firewall is not used.

- **The proxy** (omni-factory deploy): an allowlisting forward proxy for HTTP
  and HTTPS (by host name, through `CONNECT`), in its own container, with one
  allowlist for the deployment and a narrower one per Confidential factory. Each
  factory's traffic reaches it with that factory's credentials, so the proxy
  knows which list applies. Denials are logged and audited.
- **Forcing traffic through it**:
  - factory runtimes, CI jobs and preview containers run on internal Docker
    networks with no route out except the proxy;
  - the VM's firewall (nftables, in Docker's `DOCKER-USER` chain) drops any
    container traffic to the internet that does not come from the proxy, so a
    misconfigured container cannot bypass it;
  - QuickSand: a network mode where the guest reaches only the proxy. QEMU's
    user networking supports this (`restrict=on` with a `guestfwd` to the
    proxy); add it to quick-sandbox, or set it from omniagents'
    `core/sandbox/quicksand.py` if the package lets it pass QEMU options.
- **Outer limit** (omni-factory infrastructure, *Azure profile*): NSG rules allow
  the VM out only on HTTPS, and Azure services (Key Vault, Blob Storage, Log
  Analytics, the model endpoints) through private endpoints.
- **Workplace**: the deployment and per-factory allowlists as settings, audited,
  with every change audited.

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
  approvers (its administrators by default). Approvers are notified in the
  installation's Teams channel (workstream 2), and on 80/20 in Slack. A decision goes through the existing `tool_approval_response` and
  `mcp_approval_response` calls and is audited with the approver. A pending
  approval expires after seven days, rejecting the call.
- **Durable waiting.** A pending approval is an in-memory wait in the runtime
  (`request_tool_approval` awaits a future); a restart loses it and the run
  waiting on it. Approvals can wait days, so persist the paused run: when a run
  stops for a human approval, save its state (the SDK's `RunState.to_json`)
  with the pending approval, and on restart restore it (`RunState.from_json`),
  re-publish the approval and resume once it is decided.

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
- **Deletion cascades.** Deleting a conversation already removes its history,
  conversation records, replay journal, tool outputs and worker journal. It
  leaves behind the sessions of the workers it started, which have their own
  ids, and its traces, which are removed only by age. Delete both with the
  conversation.
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
  (Standard_D8s_v5, 8 vCPU and 32 GB, with nested virtualization) and its
  512 GB Premium SSD, App Service with VNet integration, Key Vault, Blob Storage
  with a private endpoint and the lifecycle rule, Log Analytics with six-year
  retention and alert rules, App Service's health-check alert, and Azure Update
  Manager (critical and high updates within 14 days, the rest within 30).
- **TLS on the private hop**: App Service reaches the VM over HTTPS too, with a
  certificate the VM's ingress serves, so data is encrypted inside the private
  network as well (UTS 165.1.5.4.2).
- **Image scanning** (omni-mono and omni-factory CI): scan every container image
  the deployment runs and fail the build on critical vulnerabilities.
- **Azure Compose overlay** (omni-factory): `deploy/azure.compose.yml`, the same
  stack with no public ports, `policy: strict`, keys from Key Vault and backups
  to Blob Storage.
- **Runbook**: out-of-band stops (disable the model deployment's keys, stop the
  VM), restore, and who holds which access.

Setup only: Entra user assignment to one Workplace-users group (AI training is
tracked outside Workplace), factory membership from Entra groups, Conditional
Access MFA.

## 8. Transparency and oversight (M)

Build, in omniagents-workplace unless noted:

- **AI attribution.** Linear posts as an app. Slack posts as a dedicated user
  account the factory owns, marked "Sent using <app>" but not as a bot, and
  GitHub uses a dedicated factory account. Name both accounts as AI (for
  example "Acme Factory (AI)") and say so in their profiles; Workplace sets this
  when it creates them. Commit authorship is not set anywhere today: set the
  sandboxes' git identity to the factory's account and add an
  `AI-Generated-By: <factory>/<agent>` trailer to agent commits. Add
  generated-by metadata to Shared documents.
- **Factory tab metrics.** Add error rate across runs, tool-failure rate and
  runtime uptime (record runtime up and down transitions).
- **Heightened-risk review: deferred.** The first factory is not
  heightened-risk. When one is, add the setting, sampling of 5% of its
  conversations (adjustable by its owner) into a review list on the Factory
  tab, reminders when the six-monthly reassessment is due, and flags for the
  material changes that need it.
- **Adversarial evaluations** (omni-factory): scenarios for prompt injection
  through fetched content, data sent to a destination not on the allowlist, and
  data crossing between factories; required in CI on every change to the
  factory's definition.
- **Incident alerts.** Kill-switch use, blocked egress from Confidential
  factories, repeated sign-in failures and policy-rejected changes are alert
  events, posted to the installation's Teams channel (workstream 2).

## 10. Later: Microsoft Teams and Azure DevOps (L each)

The Azure deployment starts on Workplace's stand-ins. Once it runs, replace them
with the institution's real services, each added to the allowed services:

- **Microsoft Teams**: a Workplace integration for chat and events, as Slack has
  today (incoming messages and mentions become events; the factory posts as its
  own app identity, named as AI). It also replaces the incoming webhook for
  notifications.
- **Azure DevOps**: repositories, pull requests, work items and pipelines, as
  GitHub and Linear have today; branch protection requires a person's approving
  review, and the factory's identity cannot bypass it. omni-desktop's
  `azure-repos.ts` is a starting point for the API.

## Verified

Checked in the code on 2026-11-04; the workstreams above reflect the results.

| Question | Answer |
| --- | --- |
| Can a configuration layer be locked against the project? | Yes: omniagents' managed layer. Its MCP allowlist matches names only (workstream 3). |
| Does branch protection stop the factory merging alone? | Yes in the stand-in; approval from another bot still counts (workstream 3). Real GitHub is setup. |
| Does a run paused on an approval survive a restart? | No: it is an in-memory wait. The SDK can save and restore run state (workstream 5). |
| Does deleting a conversation remove its journals and traces? | Partly: worker sessions and traces are left behind (workstream 6). |
| Does every integration post as an identifiable app? | Partly: Slack and GitHub use dedicated accounts not named as AI; commits have no set identity (workstream 8). |
| Can the kill switch suspend QuickSand VMs? | Yes, through graceful shutdown; a fast kill would cut off the disk flush (workstream 1). |
