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
- **PHI lives on the VM either way.** Agent workspaces, sandbox disks, staged
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

| | Local (students) | 80/20 | Azure (PHI) |
| --- | --- | --- | --- |
| Install | omni-desktop installer | Compose on one VM | Same Compose stack on one VM |
| Front door | localhost | Traefik, public | App Service + Easy Auth, VM private |
| State | SQLite, local files | SQLite, VM disk | SQLite, VM managed disk |
| Backups | None required | Disk snapshots | Azure Backup, tested restores |
| Integrations | Stand-ins | Stand-ins or real | Stand-ins or real, allowlisted |
| Models | Student's own provider | Any configured provider | Providers covered by a BAA |
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
| Log Analytics | Entra sign-in logs, platform logs and Workplace audit events, outside the VM's write access |

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

## Protected health information

Microsoft and the model providers in use have BAAs in place. Confirm the
decisions in this section with the institution's privacy and security office
and record them in the HIPAA risk analysis.

### The factory is not a system of record

Factories read and process PHI; they never own it. Uploads to Shared are
extractions from systems of record, and the data agents read and the services
they access belong to those systems. Durable results go back to the system of
record, not only into the factory. This is what keeps backups small: a lost
working copy is recovered by extracting it again from its source.

### Factories that handle PHI

Each factory is marked as **handling PHI** or not. The Azure profile requires
the setting to be chosen for every factory; there is no default. Only some
factories touch PHI, depending on their use case, and the setting applies the
rules below only to those.

| | Factory without PHI | Factory handling PHI |
| --- | --- | --- |
| Conversations, journals, traces | Kept, ordinary backups | Kept as a governed record (below) |
| Shared uploads, agent workspaces | Ordinary backups | Not backed up: working copies |
| Self-improvement output | May be shared beyond the factory | Stays within the factory |
| Sandbox egress | Deployment allowlist | The factory's own, narrower allowlist |

Changing a factory from handling PHI to not handling it does not reclassify
data it already holds.

### Conversations are a governed record

Conversations stay long-lived: users return to them and self-improvement learns
from them. Not every conversation contains PHI, but in a factory handling PHI
treat its conversations, worker journals and traces as PHI. That means:

1. **Who can read them.** Only the factory's members, as Workplace enforces
   already; sign-ins and access are audited.
2. **Where copies are.** The live disk and its backups in the Azure tenant,
   encrypted. No exports, CI logs, laptops or non-PHI factories.
3. **How long they are kept.** A written retention period for conversations,
   for example for the life of the factory.
4. **How they are destroyed.** Deleting a conversation or factory removes it
   from the live system at once; it then survives only in backups, until they
   expire. The backup history (for example, 30 days) bounds disposal, not the
   conversations' own retention.
5. **What learns from them.** Self-improvement output (skills, notes,
   factory-live changes) stays within the factory, and must not copy patient
   details into skills, prompts or configuration.

### Backups

| Data | PHI? | Backup |
| --- | --- | --- |
| Workplace database: accounts, memberships, settings | No | Yes |
| Factory configuration, factory-live, skills, stand-in repositories | No (code, not data) | Yes |
| Encryption keys | No; they unlock PHI | Separately, in Key Vault, never with data backups |
| Conversations, journals, traces | In factories handling PHI | Yes, encrypted, with a short backup history |
| Shared uploads, agent workspaces | In factories handling PHI | Only in factories without PHI |
| Previews, images, caches, source checkouts | Never (previews use seed data only) | No: rebuildable |

Backups go to Azure Backup in the same tenant, under the same access controls
as the VM, and expire automatically. Restore to a fresh VM on a schedule.

### Other controls

1. **Model calls.** Prompts carry PHI. The Azure profile uses only model
   endpoints covered by a BAA; enforce this as a deployment policy that
   Workplace refuses to violate, not as a convention.
2. **Egress.** An agent that reads PHI and can reach arbitrary hosts is an
   exfiltration path through prompt injection. Deny outbound traffic by default
   at the VM's subnet and allowlist model endpoints, package registries and the
   integrations factories need; factories handling PHI narrow it further.
3. **Side channels.** Preview apps, Actions logs, CI artifacts and any real
   GitHub or Slack integration can carry PHI out of the VM. Integrations used by
   factories handling PHI must be covered by a BAA or kept to non-PHI content.
4. **Stand-ins.** Stand-ins are allowed. They are Workplace's own services and
   keep their data on the VM, inside the protected boundary, under the same
   encryption, backup and factory permissions as everything else.
5. **Encryption.** Managed disks are encrypted at rest; decide whether
   customer-managed keys are required.
6. **Audit.** Entra sign-in logs and Workplace audit events go to a store that
   factory agents and sandboxes cannot modify.
7. **Isolation.** Credentials, files and execution resources stay isolated
   between factories on the shared VM. Resource limits keep CI from starving chat
   and agents.

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
  Shared and workspaces of factories handling PHI from it.

## Suggested implementation sequence

1. Resolve the investigations above, especially proxy WebSockets and preview
   authentication.
2. Write the VM's infrastructure-as-code and reuse the 80/20 Compose stack
   unchanged.
3. Adapt the Easy Auth adapter to trust headers only from the App Service proxy.
4. Add the factory PHI setting and the Azure profile's policies: allowed model
   endpoints, egress allowlists, self-improvement containment, audit export.
5. Configure backups by the table above, keys in Key Vault, and exercise a full
   restore to a fresh VM.
6. Prove the deployment with an isolated example factory and no PHI before
   onboarding real work.

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
- Every factory is marked as handling PHI or not. Factories handling PHI back up
  conversations but not Shared uploads or workspaces, and their
  self-improvement output stays within the factory.
- Encryption keys are not stored with data backups.
- A full restore from backup to a fresh VM has been performed.
- The 80/20 deployment runs the same stack, and local installations still work
  with SQLite and stand-ins and no Azure dependencies.

## Deferred: web/compute split

Running Workplace's web tier on App Service with Azure PostgreSQL, a durable job
queue, Blob Storage and separate compute workers remains possible later. Revisit
it only for a concrete need, such as more than one compute VM or a requirement
that the web interface stay available while the VM is down. Keeping
configuration, backing services and file identity behind URLs and interfaces
keeps that path open without building it now.
