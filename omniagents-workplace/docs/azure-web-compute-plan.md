# Proposed Azure web and compute architecture

Status: proposed; not implemented. Recorded 2026-11-02.

This document preserves the agreed deployment direction for future implementation.
It does not authorize a migration or change the existing deployment. The optional
[Easy Auth adapter](azure-easy-auth.md) exists separately; it does not provide the
web/worker split described here.

## Agreed direction

Run Workplace's actual web application on Azure App Service, backed by Azure
Database for PostgreSQL. Run execution on **one private Azure VM**, shared by
factory runtimes, agent sandboxes, Actions jobs, and Coolify. The VM has no public
IP or public inbound application ports.

App Service is not merely a proxy to a complete Workplace installation on the
VM. It owns the web API, authentication, authorization, incoming events, and user
experience. The compute deployment owns execution. Do not substitute a thin
App Service proxy for this separation simply because it needs fewer code changes.

QuickSand guests consume the compute VM's resources; they are not separate Azure
VM resources or separately billed instances. Agent processes stay outside their
tool VMs. Preserve persistent agent workspaces and disposable CI environments.

Local installations must remain supported with SQLite and local execution. Azure
services must be optional deployment backends, not requirements for the template.

## Service responsibilities

| Component | Responsibility |
| --- | --- |
| App Service | Workplace UI/API, Easy Auth, account and factory permissions, conversations, settings, incoming events, job submission, browser updates |
| Azure PostgreSQL | Durable application state, accounts, memberships, factory configuration, conversations, metadata, jobs, leases, results and event records |
| One private compute VM | Factory agent processes, persistent QuickSand sandboxes, disposable Actions execution, Coolify and preview applications |
| Azure Blob Storage (proposed) | Shared files, recordings, artifact content and backup objects |
| VM persistent disk | Active sandbox disks, working checkouts, runner images and execution caches |

Blob Storage and a PostgreSQL-backed job queue are proposed implementation choices,
not existing integrations. Prefer starting with PostgreSQL-backed jobs rather than
adding a separate message broker unless a concrete requirement justifies one.

## Execution and events

Example chat flow:

1. App Service authenticates and authorizes the user, saves the message, and
   records a durable request for the appropriate factory/conversation.
2. A compute worker claims the request and delivers it to the appropriate agent.
3. The agent runs outside its tool sandbox and invokes tools inside that sandbox.
4. The worker records public messages, activity, results and execution status.
5. The web service streams updates to authorized browser clients. Reconnecting
   clients resume from durable event positions rather than losing updates.

Workers initiate their control connection over private networking; no public
worker listener is required. Define whether workers access PostgreSQL directly
or use an authenticated internal web API during implementation. Keep job and
identity authorization explicit in either case.

Preserve the existing event gateway's delivery semantics and the distinction
between internal agent activity and explicitly published conversation messages.
Do not automatically publish agent final responses. Existing comments on shared
artifacts/canvases must not begin triggering agent runs as a side effect of this
refactor.

## Required code work

### 1. Durable state and PostgreSQL

- Inventory persistence across Workplace, the installation host, factory runtimes,
  and supporting services; this is not just a change to one SQLite connection.
- Introduce a supported PostgreSQL storage backend and versioned migrations while
  retaining SQLite for local use.
- Remove assumptions that one process owns the authoritative state in memory.
  Define transaction boundaries, concurrent updates, cache invalidation, session
  revocation and factory isolation across web and worker processes.
- Move credentials through the existing encrypted-secret mechanisms; do not
  expose database or provider credentials to ordinary workflow/tool guests.
- Provide an explicit, validated data migration path when a real deployment is
  ready to move; do not automatically migrate existing installations.

### 2. Web/compute boundary

- Introduce separate web and worker entry points/configurations.
- Extract direct process, sandbox, Actions and managed-deployment lifecycle work
  from web handlers into compute-side operations.
- Define operations for execution, cancellation, status, logs, file exchange,
  sandbox lifecycle, desktop sessions and deployment management.
- Preserve factory-specific credentials, memberships, execution ownership and
  isolation on the shared compute host.
- Keep the local deployment convenient through an in-process/local adapter or
  equivalent local orchestration using the same operation contract.

### 3. Durable jobs and live updates

- Implement atomic claiming, leases, heartbeats, cancellation and bounded retries.
- Define ordering for a conversation and resource ownership for persistent agents
  and sandboxes; multiple workers must not drive the same resource concurrently.
- Design for at-least-once delivery: use idempotency keys and reconciliation for
  external side effects. An expired lease is not proof that an operation stopped.
- Persist results/events before acknowledging completion. Use durable cursors for
  browser reconnects; notification mechanisms alone are not durable delivery.
- Apply resource limits and concurrency budgets so CI cannot starve chat, agents
  or other factories. Queue work when the single VM is at capacity.

### 4. Files and storage

- Add shared object-storage support for uploads, recordings, Shared, artifacts and
  other content consumed across web and compute deployments.
- Keep application-level file identity separate from a machine-local path. Define
  staging/synchronization into sandboxes and publication of outputs back to storage.
- Preserve per-factory access checks and guest-share token rules. Avoid publicly
  accessible object containers as a shortcut.
- Retain active VM disks on persistent VM storage; object storage is for transfer
  and backups, not a filesystem for live VM disks or SQLite databases.
- Define backup/restore coverage and consistency for PostgreSQL, content and
  persistent execution state.

### 5. Microsoft authentication and private browser services

Workplace uses Easy Auth at App Service and retains its own account/factory
permissions. Preview applications **always require Microsoft sign-in**. Microsoft
sign-in alone does not grant access to every factory's previews.

- Route browser desktop and preview traffic through an authenticated public web
  endpoint to the private VM; only these VM-hosted services need this routing.
- Support streaming, WebSocket upgrades, uploads and application assets.
- Register preview ownership and lifecycle durably. Refuse unknown, expired or
  unauthorized preview hostnames before forwarding traffic.
- Use wildcard DNS/TLS for preview names where supported. DNS supplies names; it
  does not provide authentication or access to a private VM.
- Keep previews on a separate origin boundary from Workplace. Prevent preview
  code from accessing Workplace cookies or trusted identity headers; review
  sibling-preview isolation and cookie scopes as well.
- Ensure no direct route to Coolify or preview containers bypasses authorization.
- Preserve intentionally guest-accessible artifact links and independently
  authenticated machine/webhook routes. The mandatory Microsoft-login policy
  here applies to previews, not a blanket removal of existing guest sharing.

## Azure-specific investigations before implementation

Verify these against current Azure capabilities rather than assume support:

- App Service private connectivity to the VM and PostgreSQL, subnet layout,
  firewall rules and private DNS.
- Wildcard custom domains, certificates, Easy Auth cookies and login/logout
  callbacks across preview hosts. Establish a working authentication flow before
  committing to a hostname design.
- WebSocket/streaming timeouts and reconnect behavior for chat and desktop access.
- A compute VM SKU/security configuration that exposes the virtualization features
  required for accelerated QuickSand, including actual KVM availability. Software
  emulation is a separate performance choice, not an assumed substitute.
- Blob Storage integration, PostgreSQL compatibility, connection limits, migrations
  and secret delivery for both deployments.
- Safe web and worker updates: drain/reconcile active work and avoid two owners of
  the same sandbox. Do not assume deployment slots or scaling are safe merely
  because state moved to PostgreSQL.

## Suggested implementation sequence

1. Inventory current state/process/file coupling and define storage and worker
   contracts. Resolve Azure networking and preview-auth feasibility questions.
2. Implement PostgreSQL persistence and concurrency correctness alongside SQLite.
3. Implement worker entry points, durable jobs, recovery and live-event delivery.
4. Implement shared file storage and sandbox transfer semantics.
5. Implement authenticated desktop/preview routing and Microsoft login integration.
6. Add reproducible Azure deployment configuration and an operations runbook.
7. Prove the architecture on an isolated example factory before proposing migration
   of existing applications or production state.

## Acceptance criteria

- The UI and API genuinely execute on App Service; compute stays on one private VM.
- Chat, automations, persistent agent sandboxes, Actions, Shared, Library, desktop
  control and previews work across the deployment boundary.
- Web restarts do not terminate compute jobs or lose accepted events. Worker
  failures lead to explicit recovery/reconciliation rather than silent job loss
  or uncontrolled duplicate external effects.
- Preview pages, assets and WebSockets require Microsoft authentication and
  appropriate factory access; there is no public backend bypass.
- Credentials, files and execution resources remain isolated between factories.
- Resource contention cannot make an unrestricted number of builds consume the VM.
- PostgreSQL/object/disk backup and restore procedures are exercised.
- Existing local SQLite deployments continue to work without Azure dependencies.

No cutover, resource purchase, implementation, commit or push is implied by saving
this plan. Scope and deployment approval should be revisited when implementation
is requested.
