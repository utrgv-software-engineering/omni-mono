# Azure deployment plan

Status: proposed; not implemented. Revised 2026-11-04 (supersedes the
2026-11-02 web/compute split).

This document records the agreed deployment direction. It does not authorize a
migration, resource purchase or change to an existing deployment. The optional
[Easy Auth adapter](azure-easy-auth.md) already exists and is part of this plan.

## Agreed direction

Workplace is one product deployed in three profiles. The 80/20 and Azure
deployments run the **same stack on one large VM**: Workplace, factory runtimes,
QuickSand sandboxes, Actions and Coolify. They differ in their front door,
providers and data-protection controls, not in architecture.

On Azure, the VM is private: no public IP and no inbound ports from the
internet. **Azure App Service is the authenticated front door.** It terminates
TLS, requires Microsoft sign-in through Easy Auth, and forwards requests over
VNet integration to Workplace on the VM. Workplace keeps its own account and
factory permissions.

Why not split web and compute or use serverless containers:

- **Cost and operations.** Per-sandbox serverless containers cost more and need
  start/stop management; one VM is simpler and cheaper for this workload.
- **The split is not what keeps the VM private.** A private VM behind App Service
  and Easy Auth already provides that, without moving the web tier.
- **Sensitive data lives on the VM either way.** Agent workspaces, sandbox disks, staged
  Shared files and previews are all compute-side, so moving the application
  database elsewhere does not shrink what must be protected.
- **Dogfooding.** With an identical stack, every problem the 80/20 businesses hit
  is one the Azure deployment would have hit. A separate architecture for Azure
  would leave its code paths untested by daily use.
- **Cost of the split itself.** PostgreSQL alongside SQLite, distributed job
  leases, object-storage staging and cross-boundary routing are a large refactor
  that buys horizontal scale and web uptime during VM maintenance. Neither is
  needed now. See [Deferred: web/compute split](#deferred-webcompute-split).

## Deployment profiles

| | Local (students) | 80/20 | Azure (sensitive data) |
| --- | --- | --- | --- |
| Install | omni-desktop installer | Compose on one VM | Same Compose stack on one VM |
| Front door | localhost | Traefik, public | App Service + Easy Auth, VM private |
| State | SQLite, local files | SQLite, VM disk | SQLite, VM managed disk |
| Backups | None required | Disk snapshots | Azure Backup, tested restores |
| Integrations | Stand-ins | Stand-ins or real | Stand-ins or real, allowlisted |
| Models | Student's own provider | Any configured provider | Approved list (BAA-covered) |
| Egress | Open | Open | Denied by default, allowlisted |

Local installations must remain supported with SQLite, stand-ins and local
execution. Nothing Azure-specific becomes a requirement of the product.

Follow the useful parts of twelve-factor: configuration from the environment,
backing services addressed by URL (so SQLite → PostgreSQL or local files → Blob
Storage remain configuration changes if ever needed), logs to stdout, explicit
dependencies, and parity between profiles. Do not adopt process ephemerality:
the workload is persistent by design. The VM is long-lived but **rebuildable**:
provisioned from infrastructure-as-code, with state on a managed disk that is
snapshotted and restored on a schedule.

## Azure topology

| Component | Responsibility |
| --- | --- |
| App Service | Public HTTPS hostnames, Easy Auth (Microsoft Entra), reverse proxy to the VM over VNet integration, WebSocket forwarding |
| Private VM | Workplace, factory runtimes, QuickSand sandboxes, Actions, Coolify and previews; the same Compose stack as 80/20 |
| Managed disk | All durable state: Workplace and factory databases, Shared, workspaces, sandbox disks, caches |
| Azure Backup | Scheduled, encrypted backups of the data in [Backups](#backups), with automatic expiry |
| Key Vault | Workplace's encryption keys, kept apart from data backups |
| Azure Firewall or NSG rules | Inbound only from the App Service subnet; outbound allowlist |
| Log Analytics | Entra sign-in logs, platform logs and Workplace audit events, outside the VM's write access; the health-check alert |

### Front door

- App Service runs only a reverse proxy. It holds no application state, so
  restarting or redeploying it does not affect running work.
- The VM accepts application traffic **only from the App Service integration
  subnet**. Workplace trusts Easy Auth identity headers only on that path. Neither
  sandboxes nor preview containers may reach Workplace with forged identity
  headers; block direct listener access from them.
- Workplace currently reads Easy Auth headers as if it ran on App Service itself.
  Adapt the adapter to accept them from the trusted proxy, and refuse them from
  any other source.
- Machine-authenticated routes (agent APIs, webhooks, guest share links) still
  pass through App Service with anonymous requests allowed, as described in the
  [Easy Auth adapter](azure-easy-auth.md); Workplace keeps enforcing their own
  authentication.
- Previews always require Microsoft sign-in **and** factory access. They stay on
  an origin separate from Workplace, and preview code must not see Workplace
  cookies or trusted identity headers. Refuse unknown or expired preview
  hostnames before forwarding.

## Sensitive data

Microsoft and the model providers in use have BAAs in place. Confirm the
decisions in this section with the institution's privacy and security office,
and record them in the HIPAA risk analysis and UTRGV's AI risk and impact
assessments.

### The factory is not a system of record

Factories read and process sensitive data; they never own it. Uploads to Shared
are extractions from systems of record, and the data agents read and the
services they access belong to those systems. Durable results go back to the
system of record, not only into the factory. This is what keeps backups small:
a lost working copy is recovered by extracting it again from its source.

### Data classification

Each factory has a **data classification**, using UTRGV's classes: Published,
Controlled or Confidential. The Azure profile requires one for every factory;
there is no default. PHI is Confidential, and so is other personal data under
UTRGV's definition (student records, identification numbers, biometrics and the
rest covered by the Texas Data Privacy and Security Act). Only some factories
touch such data, depending on their use case, and the classification applies the
rules below only to those.

| | Published | Controlled | Confidential |
| --- | --- | --- | --- |
| Models | [Approved list](#approved-list) only | Approved list only | Approved list only |
| Integrations, MCP servers | Any configured | Approved list only | Approved list only |
| Changes to the factory definition | As configured | Always reviewed in a PR | Always reviewed in a PR |
| Conversations, journals, traces | Kept, ordinary backups | Kept, ordinary backups | Kept as a governed record (below) |
| Shared uploads, agent workspaces | Ordinary backups | Ordinary backups | Not backed up: working copies |
| Self-improvement output | May be shared beyond the factory | May be shared beyond the factory | Stays within the factory |
| Sandbox egress | Deployment allowlist | Deployment allowlist | The factory's own, narrower allowlist |

Lowering a factory's classification does not reclassify data it already holds,
and is a [material change](#change-management).

### Approved list

UTRGV allows Controlled and Confidential data only with AI tools that IT and the
Information Security Office have assessed. Installation administrators keep an
**approved list** of model endpoints, integrations and MCP servers, recording
each one's assessment. Every factory reaches models only through the list,
since any conversation may carry pasted data; Workplace refuses to connect a
Controlled or Confidential factory to an integration or MCP server not on it. On Azure, every model endpoint on the list is
covered by a BAA.

### Conversations are a governed record

Conversations stay long-lived: users return to them and self-improvement learns
from them. Not every conversation contains sensitive data, but in a Confidential
factory treat its conversations, worker journals and traces as Confidential.
That means:

1. **Who can read them.** Only the factory's members, as Workplace enforces
   already; sign-ins and access are audited.
2. **Where copies are.** The live disk and its backups in the Azure tenant,
   encrypted. No exports, CI logs, laptops or other factories.
3. **How long they are kept.** For the life of the factory. Conversations,
   worker journals and traces share one rule, so self-improvement and users
   always see the same history. Nothing expires on a timer; a member deletes a
   conversation, or an administrator deletes the factory.
4. **How they are destroyed.** Deleting a conversation or factory removes it
   from the live system at once; it then survives only in backups, which expire
   after **30 days**. That bound is what disposal means here: deleted data is
   gone everywhere within 30 days.
5. **What learns from them.** Self-improvement output (skills, notes,
   factory-live changes) stays within the factory, and must not copy personal
   details into skills, prompts or configuration.

### Backups

| Data | Sensitive? | Backup |
| --- | --- | --- |
| Workplace database: accounts, memberships, settings | No | Yes |
| Factory configuration, factory-live, skills, stand-in repositories | No (code, not data) | Yes |
| Encryption keys | No; they unlock it | Separately, in Key Vault, never with data backups |
| Conversations, journals, traces | In Confidential factories | Yes, encrypted, 30-day backup history |
| Shared uploads, agent workspaces | In Confidential factories | Only in Published and Controlled factories |
| Previews, images, caches, source checkouts | Never (previews use seed data only) | No: rebuildable |

Backups go to Azure Backup in the same tenant, under the same access controls
as the VM. They run **daily and are kept for 30 days**, with no longer-term
copies, so deleted data is fully disposed of within 30 days. Restore to a fresh
VM **every quarter** and record the result.

### Other controls

1. **Model calls.** Prompts carry sensitive data. Factories reach models only
   through the approved list; enforce this as a deployment policy that Workplace
   refuses to violate, not as a convention.
2. **Egress.** An agent that reads sensitive data and can reach arbitrary hosts
   is an exfiltration path through prompt injection. Deny outbound traffic by
   default at the VM's subnet and allowlist model endpoints, package registries
   and the integrations factories need; Confidential factories narrow it further.
3. **Side channels.** Preview apps, Actions logs, CI artifacts and any real
   GitHub or Slack integration can carry data out of the VM. Integrations used by
   Confidential factories must be on the approved list and covered by a BAA, or
   kept to non-sensitive content.
4. **Stand-ins.** Stand-ins are allowed. They are Workplace's own services and
   keep their data on the VM, inside the protected boundary, under the same
   encryption, backup and factory permissions as everything else.
5. **Encryption.** Managed disks, backups and Key Vault use Azure's
   platform-managed encryption at rest. HIPAA does not require customer-managed
   keys; they add key rotation and a way to lock yourself out of your own data.
   Adopt them only if institutional policy requires it.
6. **Audit.** Entra sign-in logs and Workplace audit events go to a store that
   factory agents and sandboxes cannot modify, and are kept for **six years**,
   matching HIPAA's retention period for compliance documentation. Audit events
   record who did what, not conversation content, so they are not themselves a
   store of sensitive data.
7. **Isolation.** Credentials, files and execution resources stay isolated
   between factories on the shared VM. Resource limits keep CI from starving chat
   and agents.

## Kill switch

Texas requires it. DIR's AI code of ethics (1 TAC §219.11(c)(2)(C), adopted
under SB 1964, effective March 18, 2026, and applicable to institutions of
higher education) says state agencies "must ensure AI systems can be paused,
restricted, or disabled until harmful or inaccurate decision making can be
remedied." The switch is a product feature in every profile, not only on Azure.

Workplace can already stop one factory's runtime (installation administrators
only, `POST /workplace/api/factories/{id}/stop`). That is the building block;
the kill switch adds the rest below.

### Scopes and who can use them

| Switch | Who can stop | Who can resume |
| --- | --- | --- |
| One factory | That factory's administrators, installation administrators | Same, but a stop made by an installation administrator needs one to resume |
| All factories | Installation administrators | Installation administrators |

Stopping all factories does not change each factory's own switch; resuming
all returns every factory to its own state. Every stop and resume requires a
written reason and is recorded in the audit log.

### What stopped means

- **Immediately:** in-flight turns, workers and automation runs are cancelled,
  the factory runtime and the commands it runs in sandboxes are terminated, and
  anything still running after 10 seconds is killed.
- **While stopped:** no model calls and no tool calls. Chat shows that the
  factory is stopped, by whom and why, and refuses new messages. Incoming events
  are recorded as skipped; scheduled automations are skipped.
- **On resume:** nothing replays. Skipped events stay skipped and automations
  continue from their next scheduled time, so stale work cannot act on its own
  after a stop. A person decides what to rerun.
- **Kept:** conversations, workspaces, configuration and files. A stop is a
  pause, not a deletion; deleting a factory remains a separate action.
- **Not stopped:** preview applications and Actions runs are ordinary software,
  not AI; they keep running, and administrators stop them separately.

### Enforcement

- The stop is durable: it survives Workplace, runtime and VM restarts, and a
  stopped factory never autostarts.
- Workplace checks it before starting a runtime and before delivering any chat
  message, event or automation to one. The runtime also checks it before every
  model call, so a loop that escaped cancellation still halts at its next step.
- It fails closed: if the switch state cannot be read, the factory is treated as
  stopped.

### Restrict

"Restricted" is covered by each factory's existing controls rather than a third
switch: turning automations off, disconnecting integrations, and narrowing its
egress allowlist.

### Out-of-band stops

If Workplace itself is unavailable or misbehaving, two Azure-level stops halt
every factory without it. Document both in the runbook, with who holds the
access:

1. **Disable the model deployment's keys** in Azure. Every agent loses its model
   immediately; nothing on the VM needs to cooperate.
2. **Stop the VM** in the Azure portal. Everything on it stops.

Drill the kill switch every quarter, alongside the restore test: stop one
factory, stop all, resume, and confirm nothing replayed.

## UTRGV AI policy

UTRGV's [AI policy](https://www.utrgv.edu/ai/policy/index.htm) applies to the
Azure deployment. Most of it is process: risk and impact assessments, approval
by the AI Governance Committee and the AI Risk Officer, vendor contracts and
annual training. The parts below are product and setup work; the kill switch
and the approved list above cover its stop/override and approved-tools rules.
UTS 165 and UTRGV's "Implementation Steps and Procedures for AI Technologies"
have not been reviewed here; the Information Security Office may add to this.

### Change management

The policy requires a new risk assessment after material changes, and UTS 165
requires change management. Self-improvement already satisfies most of this:
it opens a PR that a reviewer merges, changes that need a release (agent
settings, dependencies) also need operator approval, and git history records
who proposed and approved each change. Three additions:

1. **Review is always on for Controlled and Confidential factories.** Saving a
   definition file in the UI commits directly to main unless
   `factory.definition.require_review` is on, and it is off by default. The Azure
   profile forces it on for those factories, so every change arrives by PR.
2. **The factory cannot merge its own changes.** Branch protection requires an
   approving review, and the factory's own credentials can neither approve nor
   merge its pull requests.
3. **Settings outside the repository are audited.** Data classification,
   integrations, egress allowlists, the approved list and the kill switch are
   Workplace settings, not files. Every change to them goes in the audit log.
   A **material change** is any change of model, a new integration or MCP
   server, a widened allowlist, a lowered classification, or a change to risk
   level; Workplace marks it and flags the factory for reassessment.

### Inventory and system cards

Every AI system must be registered in the Information Security Office's AI
Inventory before pilot or deployment, and public-facing systems need a system
card. Workplace generates a **system card** per factory from data it already
holds: purpose and owner, data classification, risk level, models, integrations
and MCP servers, egress allowlist, retention, and the date of the last material
change. Registering a factory, or updating its registration after a material
change, is copying the card.

### Never present AI as human

Anything an agent posts outside Workplace (Slack, email, GitHub, Linear, Shared
documents) carries an AI attribution naming the factory and agent. Commits and
generated files carry provenance metadata, such as commit trailers and document
metadata, which covers labeling synthetic content.

### Heightened-risk factories

Systems that may materially affect rights, opportunities, safety or access to
services are heightened-risk. Most factories, such as software and operations
work, are not. A factory marked heightened-risk:

- requires human approval for every action that leaves the factory;
- samples conversations for quality review, at a rate its owner sets;
- reminds its owners when its six-monthly reassessment is due.

### Operational metrics

The policy's operational metrics belong on each factory's **Factory** tab,
which already shows accuracy (judges' pass rate on live runs), latency (run
duration and time to ship) and cost. Add the rest there: error rate across runs,
tool-failure rate and runtime uptime. Rollback uses factory releases. A
dashboard cannot report that Workplace itself is down, so App Service's health
check alerts on that instead.

### Adversarial tests

Add a red-team evaluation suite to the existing evaluations, covering prompt
injection through fetched content, sensitive data reaching a destination that
is not allowlisted, and data crossing between factories. Run it before
deployment and on every material change.

### Incidents

Alert the Information Security Office's contacts on kill-switch use, blocked
outbound connections from Confidential factories, repeated sign-in failures and
self-improvement changes rejected for policy reasons. Forward audit logs to the
Information Security Office's security monitoring if it runs one.

### Setup without code

- **Access requires training.** Workplace's Entra app requires user assignment,
  limited to a group of people who have completed the annual AI training. A
  lapsed training removes access.
- **Membership follows offboarding.** Factory membership maps to Entra groups,
  so people who leave lose access through normal offboarding.
- **MFA** is enforced by Entra Conditional Access.
- **Patching.** Azure Update Manager patches the VM on a schedule.

## Investigations before implementation

Verify these against current Azure capabilities rather than assume them:

- App Service VNet integration to a private VM: subnet layout, private DNS, and
  the inbound rule that admits only the integration subnet.
- WebSocket support, idle timeouts and streaming behavior for chat, live updates
  and desktop sessions through the proxy.
- Wildcard custom domains and certificates for preview hostnames, and whether
  Easy Auth cookies and callbacks work across them without sharing Workplace's
  origin. Settle this before committing to a hostname design.
- Entra Private Access (formerly Application Proxy) as an alternative front door,
  compared on WebSockets, wildcard hostnames and cost.
- A VM SKU that exposes nested virtualization (KVM) for accelerated QuickSand.
- Egress controls: Azure Firewall versus NSG service tags, and the allowlist each
  factory needs.
- Azure Backup consistency for running SQLite databases, and how to exclude
  Shared and workspaces of Confidential factories from it.

## Suggested implementation sequence

1. Resolve the investigations above, especially proxy WebSockets and preview
   authentication.
2. Write the VM's infrastructure-as-code and reuse the 80/20 Compose stack
   unchanged.
3. Adapt the Easy Auth adapter to trust headers only from the App Service proxy.
4. Add data classification, the approved list and the Azure profile's policies:
   egress allowlists, forced review, self-improvement containment, audit export.
5. Configure backups by the table above, keys in Key Vault, and exercise a full
   restore to a fresh VM.
6. Add the kill switch for one factory and for all factories, and write the
   out-of-band runbook.
7. Add the UTRGV policy work: audited settings and material changes, system
   cards, AI attribution, heightened-risk factories, Factory tab metrics, adversarial tests
   and incident alerts. Configure the Entra setup.
8. Prove the deployment with an isolated example factory and no sensitive data,
   and register it in the AI Inventory, before onboarding real work.

## Acceptance criteria

- The VM has no public IP; all browser and webhook traffic arrives through App
  Service.
- Chat, automations, persistent sandboxes, Actions, Shared, Library, desktop
  control and previews work through the front door, including WebSockets.
- Previews require Microsoft sign-in and factory access; nothing bypasses them.
- Forged identity headers from any path other than the App Service proxy are
  refused.
- Agents can reach only allowlisted destinations; model calls go only to
  BAA-covered endpoints.
- Every factory has a data classification. Confidential factories back up
  conversations but not Shared uploads or workspaces, and their
  self-improvement output stays within the factory.
- Controlled and Confidential factories use only the approved list, and every
  change to their definition arrives by a reviewed PR the factory cannot merge.
- Every settings change is audited, and material changes flag the factory for
  reassessment.
- Each factory's system card is generated from its configuration.
- Everything agents post outside Workplace is attributed to AI.
- Encryption keys are not stored with data backups.
- A full restore from backup to a fresh VM has been performed.
- One factory and all factories can be stopped from the UI. A stop takes effect
  within 10 seconds, survives restarts and fails closed, and resuming replays
  nothing. Every stop and resume is audited with its reason.
- Disabling the model deployment's keys halts every agent without Workplace.
- The 80/20 deployment runs the same stack, and local installations still work
  with SQLite and stand-ins and no Azure dependencies.

## Deferred: web/compute split

Running Workplace's web tier on App Service with Azure PostgreSQL, a durable job
queue, Blob Storage and separate compute workers remains possible later. Revisit
it only for a concrete need, such as more than one compute VM or a requirement
that the web interface stay available while the VM is down. Keeping
configuration, backing services and file identity behind URLs and interfaces
keeps that path open without building it now.
