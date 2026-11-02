# Multiple factories in one installation

An installation shares installed software, its HTTP listener, and login accounts.
Factories do not share collaboration data, credentials, configuration, memory,
conversations, artifacts, agent processes, or writable VM state.

## Boundaries

- The installation registry contains identities, factory records, and memberships.
  Installation administrators create factories and grant access. Membership has
  a factory-local admin/member role; it does not make someone an installation admin.
- Each factory has its own Workplace database and file tree. Local user records
  map shared identities to that factory's Slack/GitHub/Linear identities. Only
  members are mirrored. Removing access also disables that local identity.
- HTTP, SSE, MCP, git, and WebSockets carry an explicit factory path. There is no
  server-side active-factory cookie. Two tabs can safely use different factories.
- Browser navigation and storage are scoped to the factory. Switching factories
  reloads the application so chat clients, voice sessions, and caches are disposed.
- Each factory references a distinct project directory. Overlapping project
  directories are rejected. Agent processes use separate runtime homes, provider
  credential directories, profiles, ports, and logs. They do not inherit other
  factories' service credentials from the installation process.
- Personal and pipelined architectures are selected per factory. The choice is stored when
  creating the factory and does not change another factory's mode.
- Clean VM images can be shared; writable disks, Library mounts, and preview
  forwards cannot. Workflow jobs remain disposable VMs. Host workflow execution
  is not enabled by the installation host.

## Implementation order

1. Explicit per-Workplace configuration instead of process-global architecture,
   Library paths, GitHub connection settings, and evaluation archive paths.
2. Registry, shared authentication, factory memberships, and isolated child apps.
3. Factory-scoped routes, browser URLs/storage, and an installation management UI.
4. Per-factory agent start/stop and isolated environment/profile generation.
5. Concurrent isolation tests, revoked-access tests, browser verification, and
   regression coverage for the existing single-factory commands.

The existing `serve` and `up` commands retain their single-factory behavior.
The installation host is a separate command; it does not silently import or
rewrite an existing deployment's state. Existing deployments can be deliberately adopted while stopped, preserving
their data directories and Docker runtimes.

## Operational details

The registry is `installation.db`; each factory lives in `factories/<id>/`.
The project checkout stays at the explicitly registered location. The registry
stores memberships; the factory stores its local service identities. Passwords
and login sessions belong to the registry. Factory-local admin roles never grant
installation administration. Membership revocation closes existing browser
sockets and event streams as well as denying subsequent requests.

Installation administrators trust the registered project code, which executes
in host agent processes; tool commands execute in the factory's VM. Separate
factory databases and credentials are application isolation, not separate OS
user accounts for mutually untrusted project authors.

Managed configurations cannot be edited through the raw YAML editor. GitHub,
model selection/sign-in, repository selection, and self-improvement settings
remain configurable through their normal factory-specific UI. New factories
start without demo repositories or shared provider credentials.

Workflow execution requires the installation's clean runner image option. The
host does not silently run repository shell scripts when no runner is configured.
Both factory architectures use persistent tool environments; CI steps use
disposable environments. Local installations use QuickSand VMs; Docker-hosted
installations use containers.
A short shared launch lock prevents the QuickSand SDK from selecting the same
VNC display port for simultaneous launches. VM disks and application state are
still separate.


## Hosted activation

Docker installations supervise runtime containers in addition to the local
QuickSand process backend. A configured template supplies new project copies;
users do not need filesystem paths in the hosted creation form. Adoption copies
only login identities/sessions into the registry and maps them to the unchanged
factory-local identities. Existing service data, model credentials, runtime
sessions and writable tool containers stay in place.

The deployed server must run `manage`, not `up`, to serve the installation UI.
The deployment helper honors `deploy/installation.compose.yml` when present so
later image updates retain that choice. Each adopted runtime's start/stop/status
is controlled from the same factory picker as new runtimes.

## Tool credentials

Admins can save a SerpAPI key under **Account → Workspace settings → SerpAPI key**.
The setting is scoped to the selected factory and stored encrypted in its data
store. The API returns only whether a key is configured. Other factories and
member-owned apps do not inherit it. The same setting feeds the generated
factory environment for Compose deployments and Workplace-created runtimes.
Changes apply when the runtime restarts; the existing Docker/Compose environment
watchers apply changes when idle. A local process needs a restart from Factories.
Removing the key exports an empty value so an older container environment cannot
silently retain it. Production backups preserve the encrypted setting and its
key; staging restores clear them. Supported credentials are explicitly listed in
`factory_secrets.py`; arbitrary runtime environment overrides are not accepted.
