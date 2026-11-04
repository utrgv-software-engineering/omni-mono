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
| Azure Backup | Scheduled, encrypted disk snapshots with defined retention |
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

Microsoft and the model providers in use have BAAs in place. PHI enters through
uploaded Shared files, data agents read, and services agents access. These
controls matter more than where the application tier runs:

1. **Model calls.** Prompts carry PHI. The Azure profile uses only model
   endpoints covered by a BAA; enforce this as a deployment policy that
   Workplace refuses to violate, not as a convention.
2. **Egress.** An agent that reads PHI and can reach arbitrary hosts is an
   exfiltration path through prompt injection. Deny outbound traffic by default
   at the VM's subnet and allowlist model endpoints, package registries and the
   integrations each factory needs.
3. **Traces and logs.** omniagents traces and worker journals hold full
   transcripts, which makes them PHI. Keep them on the encrypted disk, cover them
   with backups and retention, and restrict who can open Studio.
4. **Side channels.** Preview apps, Actions logs, CI artifacts and any real
   GitHub or Slack integration can carry PHI out of the VM. Integrations must be
   covered by a BAA or kept to non-PHI content.
5. **Stand-ins.** Stand-ins are allowed. They are Workplace's own services and
   keep their data on the VM, inside the protected boundary, under the same
   encryption, backup and factory permissions as everything else.
6. **Encryption and backups.** Managed disks are encrypted at rest; decide whether
   customer-managed keys are required. Backups are encrypted, retained for a
   defined period, and restored on a schedule to prove they work.
7. **Audit.** Entra sign-in logs and Workplace audit events go to a store that
   factory agents and sandboxes cannot modify.
8. **Isolation.** Credentials, files and execution resources stay isolated
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
- Azure Backup consistency for running SQLite databases and sandbox disks.

## Suggested implementation sequence

1. Resolve the investigations above, especially proxy WebSockets and preview
   authentication.
2. Write the VM's infrastructure-as-code and reuse the 80/20 Compose stack
   unchanged.
3. Adapt the Easy Auth adapter to trust headers only from the App Service proxy.
4. Add the Azure profile's policies: allowed model endpoints, egress allowlist,
   audit export.
5. Configure backups and exercise a full restore to a fresh VM.
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
