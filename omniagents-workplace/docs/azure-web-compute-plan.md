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
| Backups | None required | Disk snapshots | Workplace backup to Blob Storage, tested restores |
| Integrations | Stand-ins | Stand-ins or real | Stand-ins now; real Teams and Azure DevOps later |
| Policy | Open | `strict`, except Azure-only parts | `strict` |
| Models | Student's own provider | Any configured provider | The tenant's Azure OpenAI deployments (BAA-covered) |
| Egress | Open | Open | Through the filtering proxy only |

Local installations must remain supported with SQLite, stand-ins and local
execution. Nothing Azure-specific becomes a requirement of the product.

The 80/20 deployment runs the same `strict` policy as Azure (classification,
allowed services, forced review, kill switch, audit), leaving out only what needs
Azure: Easy Auth, Key Vault, Blob Storage and the private network. Otherwise the
compliance code paths would never be dogfooded.

The Azure deployment starts with Workplace's stand-ins for chat, code and
issues. It moves later to the institution's real **Microsoft Teams** and
**Azure DevOps**, which need new Workplace integrations.

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
| Private VM | Workplace, factory runtimes, QuickSand sandboxes, Actions, Coolify and previews; the same Compose stack as 80/20. **Standard_D8s_v5** (8 vCPU, 32 GB), which supports nested virtualization |
| Managed disk | All durable state: Workplace and factory databases, Shared, workspaces, sandbox disks, caches. **512 GB Premium SSD** |
| Blob Storage | Workplace's backups of the data in [Backups](#backups), with a 30-day lifecycle rule |
| Key Vault | Workplace's encryption keys, kept apart from data backups |
| NSG rules | Inbound only from the App Service subnet; outbound limited to HTTPS |
| Filtering proxy (on the VM) | The only route out for factories, sandboxes, CI and previews; per-factory allowlists |
| Log Analytics | Entra sign-in logs, platform logs and Workplace audit events, outside the VM's write access; the health-check alert |

**Sizing.** The first users are one main factory used by three full-time staff,
plus about five more people. The 80/20 server runs several factories on 8 vCPU
and 16 GB, and its sandbox VM was killed for memory at 8 GB; the Azure VM
starts with the same CPU and twice the memory, so each person's sandbox VM,
CI and previews fit together. Resizing a VM within the family takes minutes
and keeps its disk, so grow it when the Factory tab's metrics or the VM's
memory show it is needed rather than up front.

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

## Governance

Health Affairs manages its own AI risk and governance, under authority it is
seeking delegated from the institution. UTRGV's AI policy supports this:
"Units remain responsible for the ethical and compliant use of AI within their
operations," and the AI Governance Committee "does not replace existing
governance structures." The principle throughout this plan:

- **Health Affairs decides**, inside Workplace, through its own roles.
- **Workplace records** every decision, with who made it and why.
- **ISO and AI governance review or audit** the evidence when they ask. No step
  in the system waits on anyone outside Health Affairs.

Legal and technical requirements stay whoever governs: the Texas kill switch,
HIPAA's safeguards and UTS 165's controls. Delegation changes who makes the
decisions, not whether the controls exist; having them built and operating is
part of the case for delegation.

### Roles

Health Affairs builds and runs its factories for its own needs and its
workforce: the first for software development, data analysis and business
intelligence, later ones possibly for clinical and operational work. The team
that builds them is their steward.

| Role | Held by | Does |
| --- | --- | --- |
| **Governance owner** | The AVP for Data and AI, Health Affairs | Decides technical governance directly: allowed services, material changes, lowering a factory's classification or risk. Performs the scheduled reviews |
| Installation administrator | The platform team | Runs the deployment: infrastructure, factories, people, the kill switch. Proposes decisions to the governance owner |
| Factory administrator | Each factory's stewards | Runs one factory; raises its classification or risk; approves its named tools |
| Member | Each factory's users | Works in the factory |

There is no second approver for technical decisions: the governance owner is
the person who can evaluate them. Oversight comes after the fact, through the
[quarterly report](#quarterly-report) to Health Affairs leadership and the
evidence export for ISO and AI governance. Stopping a factory never waits for
anyone: whoever may use the kill switch can stop.

### Decision records

Every governance decision is recorded: what changed, who decided, when, the
reasoning, and links to supporting material. When someone other than the
governance owner proposes one, it waits for the governance owner and takes
effect on approval; the governance owner's own decisions take effect at once.
Decisions cover:

- adding or removing an [allowed service](#allowed-services);
- lowering a factory's data classification or risk level, or choosing a lower
  one when it is created;
- material changes (below).

**Raising is never gated.** Classifying a factory Confidential or marking it
heightened-risk only adds controls, so a factory administrator or the
governance owner does it immediately; it is recorded, without approval. Only
the direction that removes controls needs the governance owner and a written
reason, and each such decision is called out in the quarterly report. Every
factory gets an explicit classification and risk level when it is created;
neither has a default, so nothing is under-labeled by accident.

### Sponsor and intended use

Each factory records its **sponsor**, the clinical or business leader it is
built for (for the first factory, Health Affairs' data and AI team itself),
and its **intended use**: its purpose, the data it touches, who is affected,
and what the AI does and does not decide. Both appear on its system card. This
is the record of stakeholder involvement the AI policy's principles ask for,
not an approval step.

### Material changes

A **material change** is a change of model, a new integration or MCP server, a
widened egress allowlist, a lowered classification, a lowered risk level, or a
change to the tools that require a person's approval. Made as a Workplace
setting, it is a decision record. Made in the factory's repository (its models,
`project.yml` security and MCP sections, or its approval-required tools), the
pull request carries a required **governance** check that passes when the
governance owner approves it, in addition to the ordinary review. Every
material change is marked in the change history.

### System cards and the AI Inventory

Workplace generates a **system card** for each factory and an **inventory
export** for the whole deployment, from data it already holds: purpose and
owner, sponsor and intended use, data classification and its decision, risk
level and its reasoning,
models, integrations and MCP servers, egress allowlist, retention,
approval-required tools, and the last material change. Registering the system
in the institution's AI Inventory, and updating the registration after a
material change and at least every 24 months (UTS 165), is submitting the
export.

### Scheduled reviews

Workplace keeps the governance calendar and records each review's completion,
who performed it and its result:

| Review | Cadence | Performed by |
| --- | --- | --- |
| Restore drill and kill-switch drill | Quarterly | Installation administrators |
| Access review (members and roles) | Annually | Governance owner |
| Risk review of each factory | Annually, and after material changes | Governance owner |
| Inventory update | Every 24 months, and after material changes | Governance owner |
| Heightened-risk reassessment | Every six months, when a factory is heightened-risk | Governance owner |
| Quarterly report | Quarterly | Generated; sent by the governance owner |

Overdue reviews show on the Governance page and post to the Teams channel.

### Quarterly report

Each quarter Workplace generates a governance report for Health Affairs
leadership (the EVP for Health Affairs and whoever else the governance owner
chooses), in plain language: every governance decision and its reasoning, with
lowered classifications and risk levels called out; material changes; new and
retired factories with their sponsors and intended uses; kill-switch events
and drills; incident alerts; scheduled reviews done and overdue. It needs no
technical background to read, and it is part of the evidence export.

### Evidence export

One export answers a review or audit request for a date range: the inventory
and system cards; decision records; quarterly reports; the change history (pull requests with
their reviewers, releases with their approvals, settings changes, with material
changes marked); kill-switch events and drills; approvals of named tools;
backup and restore results; access reviews; scheduled reviews; incident alerts;
and the audit log. It is a readable report plus the underlying records as JSON.
It contains metadata only, never conversation content or files, so it can be
handed over without becoming a store of sensitive data. Producing an export is
itself audited.

## Sensitive data

Microsoft and the model providers in use have BAAs in place, and the Azure
infrastructure, models included, is already approved for clinical and
operational use; that approval is the documented approval UTS 165.1.5.4.6 asks
for. The controls in this section are run by Health Affairs
([Governance](#governance)); none adds an approval step outside it. Record them in the HIPAA risk
analysis.

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
rules below only to those. Classification follows the data a factory touches,
not its purpose: the first factory is for software, analysis and BI, but if it
queries clinical data it is Confidential. Raising a classification is
immediate; lowering it is the governance owner's decision
([Decision records](#decision-records)).

| | Published | Controlled | Confidential |
| --- | --- | --- | --- |
| Models | [Allowed services](#allowed-services) only | Allowed services only | Allowed services only |
| Integrations, MCP servers | Any configured | Allowed services only | Allowed services only |
| Changes to the factory definition | As configured | Always reviewed in a PR | Always reviewed in a PR |
| Conversations, journals, traces | Kept, ordinary backups | Kept, ordinary backups | Kept as a governed record (below) |
| Shared uploads, agent workspaces | Ordinary backups | Ordinary backups | Not backed up: working copies |
| Self-improvement output | May be shared beyond the factory | May be shared beyond the factory | Stays within the factory |
| Sandbox egress | Deployment allowlist | Deployment allowlist | The factory's own, narrower allowlist |

Lowering a factory's classification does not reclassify data it already holds.

### Allowed services

A technical control, not an approval process: it keeps data inside the
approved, BAA-covered boundary and stops a factory from connecting somewhere
else, or giving an outside server the name of an allowed one. Installation
the governance owner decides the deployment's list of **allowed services**: model endpoints, integrations and MCP servers, each
with its [decision record](#decision-records). Every factory reaches models only
through the list, since any conversation may carry pasted data; on Azure those
are the tenant's own Azure OpenAI deployments. Workplace refuses to connect a
Controlled or Confidential factory to an integration or MCP server not on it.
Adding or removing a service is a decision record.

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

Backups are Workplace's own (`omniagents-workplace backup`), written to Blob
Storage in the same tenant, under the same access controls as the VM. Disk
snapshots cannot leave out Confidential factories' uploads and workspaces; the
application backup can, and it copies running SQLite databases consistently.
They run **daily and are kept for 30 days** by a lifecycle rule, with no
longer-term copies, so deleted data is fully disposed of within 30 days. Restore to a fresh
VM **every quarter** and record the result.

### Other controls

1. **Model calls.** Prompts carry sensitive data. Factories reach models only
   through the allowed services; enforce this as a deployment policy that Workplace
   refuses to violate, not as a convention.
2. **Egress.** An agent that reads sensitive data and can reach arbitrary hosts
   is an exfiltration path through prompt injection. Everything a factory runs
   (its runtime, sandboxes, CI jobs and previews) reaches the internet only
   through a filtering proxy on the VM, enforced by the VM's own firewall and
   internal Docker networks; the proxy allows model endpoints, package
   registries and the integrations factories need, and Confidential factories
   narrow it further. NSG rules only add an outer limit (HTTPS out). Azure
   Firewall is not used: it costs hundreds of dollars a month, cannot tell one
   factory's traffic from another's on a shared VM, and the proxy is needed
   for per-factory allowlists anyway.
3. **Side channels.** Preview apps, Actions logs, CI artifacts and any real
   GitHub or Slack integration can carry data out of the VM. Integrations used by
   Confidential factories must be allowed services covered by a BAA, or
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

- **Immediately:** within 10 seconds, in-flight turns, workers and automation
  runs are cancelled and every command running in a sandbox is terminated.
  The runtime then shuts down and its sandbox VMs are suspended with their disks
  intact; anything still running after 60 seconds is killed.
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
Azure deployment. What it requires of this system:

- **Approved AI tools for Controlled and Confidential data** ("approved AI tools
  that have been evaluated by IT and the Information Security Office"): the
  platform, Azure with its models, is already approved. Factories, models,
  integrations and MCP servers within it need no separate approval.
- **Technology Assessment before purchase**: done for Azure; nothing here is
  purchased.
- **Registration in the AI Inventory before pilot or deployment**: submit
  Workplace's inventory export before the pilot, and again after material
  changes.
- **Risk and impact assessments, reassessment after material changes, and AI
  Governance Committee approval**: these sit under the policy's
  "Heightened-Risk" heading and apply only to heightened-risk systems. The
  first factory is not one (see [Consequential decisions](#consequential-decisions)).
  Health Affairs still performs an annual risk review of every factory and
  records it ([Scheduled reviews](#scheduled-reviews)), so the evidence exists
  if the committee asks.

The parts below are product and setup work. UTRGV's internal "Implementation
Steps and Procedures for AI Technologies" is not public and is not reflected
here.

### Change management

UTS 165 requires changes to be tracked, approved, tested and logged.
Self-improvement already satisfies most of this:
it opens a PR that a reviewer merges, changes that need a release (agent
settings, dependencies) also need operator approval, and git history records
who proposed and approved each change. Four additions:

1. **Review is always on for Controlled and Confidential factories.** Saving a
   definition file in the UI commits directly to main unless
   `factory.definition.require_review` is on, and it is off by default. The Azure
   profile forces it on for those factories, so every change arrives by PR.
2. **The factory cannot merge its own changes.** Branch protection requires an
   approving review, and the factory's own credentials can neither approve nor
   merge its pull requests.
3. **Settings outside the repository are audited.** Data classification,
   integrations, egress allowlists, allowed services and the kill switch are
   Workplace settings, not files. Every change to them goes in the audit log,
   with who made it and when.
4. **Material changes need the governance owner** ([Material changes](#material-changes)).

### Never present AI as human

Anything an agent posts outside Workplace (Slack, email, GitHub, Linear, Shared
documents) carries an AI attribution naming the factory and agent. Commits and
generated files carry provenance metadata, such as commit trailers and document
metadata, which covers labeling synthetic content.

### Consequential decisions

Factories exist to automate and integrate, so oversight targets **decisions
about people**, not traffic leaving the factory. SB 1964's heightened-scrutiny
systems are those intended to make, or be a controlling factor in, a
consequential decision; it excludes narrow procedural tasks, preparatory work
for an assessment, improving completed human work and detecting decision
patterns. UTRGV's heightened-risk systems are those that may materially affect
rights, opportunities, safety or access to services. Posting updates, syncing
data, opening tickets and PRs, drafting and reporting stay fully automated.

Two mechanisms enforce it, both configured per factory:

1. **Credential scoping.** Each integration's credentials grant only what the
   factory's work needs. Where a decision belongs to a system of record, the
   factory's account there can prepare it (a draft, a pending item, a queue
   entry) but not finalize it; a person decides in that system's own workflow.
   This is the preferred design: the factory is then preparatory, not a
   controlling factor in the decision.
2. **Human approval for named tools.** When the factory itself must carry out a
   decision, its definition names the tools or integrations that need approval.
   Only those pause; everything else runs unattended. Approval uses omniagents'
   existing tool approvals, but for these tools it is always a person's:
   autopilot's allow-all pattern, "always allow", the Guardian reviewer and
   contained-sandbox auto-approval never cover them. The list lives in the
   factory's repository, so changing it is a reviewed PR.

The first factory is **not heightened-risk**, the governance owner's decision
recorded with its reasoning: it prepares work, and people make the decisions. The heightened-risk setting and its review features are built
when a factory first needs them: it would sample conversations for quality
review (5% by default, adjustable by its owner) and remind its owners when its
six-monthly reassessment is due. Until then, the Factory tab metrics, the audit
log, the approval-required tools and the kill switch are the human oversight
plan.

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
deployment and in CI on every change to the factory's definition.

### Incidents

Alert on kill-switch use, blocked outbound connections from Confidential
factories, repeated sign-in failures and self-improvement changes rejected for
policy reasons. Workplace does not send email; alerts, like approval requests,
go to a **Microsoft Teams channel through an incoming webhook**, configured once
for the installation. There is no institutional log-forwarding system, so audit
logs stay in Log Analytics, where the alert rules also run.

### Setup without code

- **Who can sign in.** Workplace's Entra app requires user assignment, to one
  group of Workplace users that administrators maintain. Annual AI training is
  tracked outside Workplace; administrators add people once they have completed
  it.
- **Membership follows offboarding.** Factory membership maps to Entra groups,
  so people who leave lose access through normal offboarding.
- **MFA** is enforced by Entra Conditional Access.
- **Patching.** Azure Update Manager patches the VM on a schedule.

## UT System security standards

UTS 165's public standards add these requirements; UTRGV's internal
"Implementation Steps and Procedures for AI Technologies" is not public and is
not reflected here.

| Standard | Requirement | How it is met |
| --- | --- | --- |
| 165.1.5.4.6 | Confidential data in AI systems only with heightened security and documented approval | The existing approval of the Azure infrastructure for clinical and operational use; the Confidential rules in [Sensitive data](#sensitive-data) |
| 165.2.1.6.3 | MFA for systems with Confidential or Controlled data | Entra Conditional Access. Under the strict policy Workplace's own password sign-in is off; only Entra sign-in and machine tokens work |
| 165.2.1.2.3, 165.1.6.2.5 | Session lock, risk-based; screens lock after 15 minutes idle | Workplace sessions end after **30 minutes** without activity and **12 hours** in total, under the strict policy. Device screen locks are the institution's device policy |
| 165.2.1.2.2 | Disable accounts immediately | Workplace checks the Easy Auth identity on every request and ends its own session when it is missing or different, so removing someone in Entra takes effect at once |
| 165.2.1.7.1 | Lock accounts after repeated failed sign-ins | Entra, since password sign-in is off |
| 165.2.1.1.2 | Review access at least every 12 months | Workplace exports every factory's members and roles for the annual review |
| 165.2.3.6.4–5, 165.2.3.9.1 | Track, approve, test and log changes | Reviewed PRs, CI evaluations, release approval and audited settings ([Change management](#change-management)) |
| 165.2.3.3.5–6 | Install security updates within a defined period | Azure Update Manager: critical and high updates within **14 days**, the rest within **30** |
| 165.2.5.6.1 | Scan for vulnerabilities | Container images are scanned in CI and fail on critical findings; Update Manager assesses the VM |
| 165.1.5.4.2 | Protect data in transit by classification | TLS to App Service, and TLS again from App Service to the VM, inside the private network |
| 165.2.4.4 | Back up and restore critical systems | [Backups](#backups) and the quarterly restore |
| 165.2.5.1 | Keep audit records for an institution-defined period | Six years in Log Analytics |

## Decisions to present

The governance model and the controls Health Affairs runs, for the Information
Security Office, the privacy office and AI governance to review:

0. Health Affairs governs its own AI risk. Its AVP for Data and AI is the
   governance owner and decides technical governance in Workplace; every
   decision is recorded with its reasoning, reported quarterly to Health Affairs
   leadership, and available as an evidence export for review or audit at any
   time.

1. Factories are not systems of record; extracted data is recovered from its source.
2. Factory data classification uses UTRGV's classes and is required per factory.
3. Conversations, worker journals and traces in Confidential factories are kept
   for the life of the factory.
4. Backups are daily, kept 30 days with no longer-term copies; deleted data is
   gone everywhere within 30 days. Confidential factories' uploads and
   workspaces are not backed up. Restores are tested quarterly.
5. Encryption at rest uses Azure's platform-managed keys; Workplace's own keys
   live in Key Vault, apart from backups.
6. Audit records are kept six years and contain no conversation content.
7. Models, integrations and MCP servers are limited to the deployment's allowed
   services, kept by its administrators; models are the tenant's BAA-covered
   Azure OpenAI deployments.
8. The first factory is not heightened-risk: it prepares work and people decide;
   decisions in systems of record are protected by scoped credentials and,
   where needed, tools that always require a person's approval.
9. Self-improvement changes are reviewed PRs the factory cannot merge.
10. The kill switch: one factory or all, by administrators, with reasons;
    out-of-band through the model keys and the VM.
11. All outbound traffic goes through a filtering proxy.
12. Sessions end after 30 minutes idle or 12 hours; MFA and sign-in through Entra
    only; security updates within 14 or 30 days.
13. AI training is tracked outside Workplace; Workplace access is an Entra group.
14. Raising a factory's classification or risk is immediate; lowering it,
    allowed services and material changes are the governance owner's recorded
    decisions. Each factory records its sponsor and intended use.
15. Scheduled reviews (drills, access, risk, inventory) are tracked and
    recorded in Workplace.

## Investigations before implementation

Verify these against current Azure capabilities rather than assume them:

- App Service VNet integration to a private VM: subnet layout, private DNS, and
  the inbound rule that admits only the integration subnet.
- WebSocket support, idle timeouts and streaming behavior for chat, live updates
  and desktop sessions through the proxy.
- Wildcard custom domains and certificates for preview hostnames, and whether
  Easy Auth cookies and callbacks work across them without sharing Workplace's
  origin. App Service is the front door either way; a problem here changes the
  preview hostname design, not the architecture.
- That QuickSand gets KVM on Standard_D8s_v5 (nested virtualization).
- The allowlist each factory needs.
- Blob Storage access from the private VM (private endpoint) and lifecycle
  deletion for backups.

## Suggested implementation sequence

1. Resolve the investigations above, especially proxy WebSockets and preview
   authentication.
2. Write the VM's infrastructure-as-code and reuse the 80/20 Compose stack
   unchanged.
3. Adapt the Easy Auth adapter to trust headers only from the App Service proxy.
4. Add data classification, allowed services and the Azure profile's policies:
   egress allowlists, forced review, self-improvement containment, audit export.
5. Configure backups by the table above, keys in Key Vault, and exercise a full
   restore to a fresh VM.
6. Add the kill switch for one factory and for all factories, and write the
   out-of-band runbook.
7. Add governance: the governance owner role, decision records, sponsor and
   intended use, material changes, system cards and the inventory export,
   scheduled reviews, the quarterly report and the evidence export.
8. Add the UTRGV policy work: audited settings, AI attribution,
   approval-required tools, Factory tab metrics, adversarial tests and incident
   alerts. Configure the Entra setup.
9. Prove the deployment with an isolated example factory and no sensitive data,
   and register it in the AI Inventory with the inventory export, before
   onboarding real work.

## Acceptance criteria

- The VM has no public IP; all browser and webhook traffic arrives through App
  Service.
- Chat, automations, persistent sandboxes, Actions, Shared, Library, desktop
  control and previews work through the front door, including WebSockets.
- Previews require Microsoft sign-in and factory access; nothing bypasses them.
- Forged identity headers from any path other than the App Service proxy are
  refused.
- Everything factories run reaches the internet only through the filtering
  proxy, and only allowlisted destinations; model calls go only to BAA-covered
  endpoints.
- Sign-in is through Entra with MFA only; sessions end after 30 minutes idle or
  12 hours; removing someone in Entra ends their Workplace session at once.
- Every factory has a data classification. Confidential factories back up
  conversations but not Shared uploads or workspaces, and their
  self-improvement output stays within the factory.
- Controlled and Confidential factories use only allowed services, and every
  change to their definition arrives by a reviewed PR the factory cannot merge.
- Every settings change is audited.
- Raising a classification or risk level takes effect at once. Lowering one,
  allowed services and material changes take effect only on the governance
  owner's recorded decision, in Workplace or, for repository changes, through
  the governance check.
- Every factory has a sponsor and intended use on its system card.
- The quarterly report is generated from Workplace's records.
- One evidence export for a date range covers every record listed in
  [Evidence export](#evidence-export), without conversation content.
- Scheduled reviews are tracked, and overdue ones are flagged.
- Everything agents post outside Workplace is attributed to AI.
- A tool a factory names as needing approval waits for a person, whatever the
  session's approval settings, autopilot or reviewer.
- Encryption keys are not stored with data backups.
- A full restore from backup to a fresh VM has been performed.
- One factory and all factories can be stopped from the UI. A stop ends all AI
  activity within 10 seconds, survives restarts and fails closed, and resuming replays
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
