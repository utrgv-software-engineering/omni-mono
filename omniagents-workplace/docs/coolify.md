# Local Coolify deployment service

The optional launcher runs Coolify 4.5.0, PostgreSQL, Redis, and a private SSH
Docker target. It supports an existing local Docker Engine or a persistent
QuickSand VM containing its own Docker Engine. No host SSH configuration is
changed. The installation host provides per-factory management and shared Workplace
sign-in and automatic PR previews for configured applications.

## Managed factories

Inside a factory, an administrator opens **Coolify** from the navigation rail
(or **More** on mobile). If stopped, this destination offers **Start Coolify**;
once ready, it opens the native dashboard. Opening it signs in with the current Workplace account; there is
no second password. Each factory owns its deployment service, database, images,
volumes, encryption keys, and OAuth client. Access is checked against the current
Workplace session and factory administrator membership on every request, including
ongoing WebSocket connections. Signing out or losing access revokes access even
if Coolify still has its own session cookie. Public registration stays disabled.

The Docker installation backend gives each factory a dedicated Docker daemon
inside a privileged container, with persistent storage and no mounted host Docker
socket. This separates deployment resources, but privileged Docker containers are
not a security boundary against hostile administrators. The QuickSand backend
instead gives each factory a separate persistent VM with Docker inside it.
`manage --deployment-backend docker|quicksand` explicitly chooses the backend;
otherwise it follows the installation backend. Stopping Coolify retains its data.

Locally, each dashboard uses `coolify-<factory-id>.localhost` on the Workplace
port. Hosted installations use `coolify-<factory-id>.<workplace-host>`; DNS must
resolve those names to the installation. Route these hosts to Workplace, preserving
Host and forwarding the HTTPS scheme. Caddy can issue certificates dynamically
using this configuration (the permission endpoint accepts only enabled factory hosts):

```caddy
{
    on_demand_tls {
        ask http://workplace:7300/workplace/deployments/allow-host
    }
}
{$PUBLIC_HOST} {
    reverse_proxy workplace:7300
}
https:// {
    tls {
        on_demand
    }
    reverse_proxy workplace:7300
}
```

This uses [Caddy's on-demand TLS permission check](https://caddyserver.com/docs/caddyfile/options#on-demand-tls).
Only trust the installation reverse proxy for forwarded headers. The private
OAuth backchannel is reachable inside the deployment network; its client secret
and single-use codes are factory-scoped. The integration uses Coolify's built-in
Authentik-compatible OAuth client without requiring an Authentik installation.
It prelinks the Workplace account ID, rather than copying passwords or trusting
email matching.

## PR previews

In Workplace's repository settings, save the repository selection, click **PR previews**,
then inspect the repository. Workplace detects `compose.preview.yml` or a standard
Compose filename; a repository-relative path can be entered instead. Confirm which
services should be public and their internal HTTP ports. Enter missing preview values,
or leave generated password/secret fields blank. **Enable PR previews** starts Coolify
if needed and configures its application, repository access, Compose file, BuildKit
secrets, service domains, and preview toggle. Existing open PRs are then reconciled.
The UI says enabled, not ready: the PR comment reports the actual build result.

Setup requires factory administrator access. Selected repositories are checked again
on submission, and a changed Compose file must be inspected again. Generated values
persist across retries. An existing enabled application is reused without replacing
its settings; an unrelated disabled application must be configured in Coolify.
Development Compose files with published host ports, privileged/host networking,
bind mounts, fixed container names, or shared external volumes need adaptation first.
The template's `factory-previews` skill guides agents through that preparation.

No separate Workplace webhook or GitHub App is required. Managed GitHub clone
credentials refresh with the existing GitHub connection. These applications use
direct authenticated Git URLs; setup removes Coolify's automatically assigned
anonymous GitHub source so private repositories keep working after token refresh.
The launcher preserves
API-configured preview domains on pinned Coolify 4.5.0.

## Agent access to Coolify

The factory template declares the optional native `coolify` MCP server. Managed
Workplace installations supply its URL and factory credential automatically. The
transport authenticates the configured factory app, routes only to that factory's
Coolify instance, and uses a private team-scoped native token with `read`,
`read:sensitive`, and `deploy` abilities. Native tokens stay in Workplace; agents
receive neither root nor configuration-write authority. The UI setup uses the
backend API while agents inspect deployment state/log summaries and perform
supported deployment/lifecycle operations through native MCP. Existing sessions
refresh their MCP connections after Coolify starts without interrupting active runs.


Workplace reconciles PRs every 30 seconds using its existing GitHub connection,
including PAT and device login. Verified GitHub webhooks and stand-in PR changes
wake it sooner. It reads current PR state rather than trusting event order.
Pagination is followed, and a tracked PR disappearing from an open-PR list is
checked individually before cleanup. Same-repository PRs targeting the application's
base branch are deployed, including drafts; external forks are not executed
automatically with the factory's build credentials.

Opening or reopening a PR creates a native Coolify preview; a new head commit
updates it. Repeated events and restarts retain the last dispatched commit.
Closing or merging the PR removes its route and asks Coolify to cancel builds
and remove the preview. Deselecting the repository, switching Git providers, or
disabling previews also removes bridge-owned previews. Existing previews created
separately in Coolify are left alone. Failed API operations remain recorded and
are retried; failed application builds are shown and can be inspected/redeployed
in Coolify, or retriggered by pushing a new commit.

Workplace displays preview links and build status. Workplace also maintains
one PR comment per factory, grouping all applications for that PR. It updates the
comment as builds queue, run, succeed, fail, or are removed; ready previews include
the public links. Real GitHub comments use the connected account and require write
access to PR comments. Stand-ins use a Workplace previews bot. Comment failures
retry independently of builds, and a provider switch never writes to the other
provider’s PR. Preview URLs are public,
on separate `preview-<id>.<workplace-host>` origins; do not put production data or
production secrets into preview applications. HTTPS Workplace and Coolify login cookies use the browser-enforced `__Host-`
prefix; preview responses also lose parent-domain cookie attributes. Existing
HTTPS installations require one new sign-in when upgrading to this protection. Only registered
active previews receive HTTPS certificates. HTTP and WebSocket traffic goes to
the factory's own Coolify proxy. Hosted installations use the Caddy configuration
above; local URLs use `.localhost` and the installation port.

Docker runs its official nested-daemon initialization, including cgroup setup,
with only its Unix socket enabled. Existing owned launcher containers are replaced
when their entrypoint or port forwarding needs updating; their bind-mounted
Docker data and application storage are retained. QuickSand forwards the VM's
proxy port through a private host port and retains it with the VM configuration.

## Standalone Docker

With Workplace installed and Docker Compose available:

```sh
python -m omniagents_workplace.deployments.coolify up --backend docker
python -m omniagents_workplace.deployments.coolify status --backend docker
python -m omniagents_workplace.deployments.coolify stop --backend docker
```

Open `http://127.0.0.1:7400`. The generated admin login is in
`~/.local/share/omniagents/coolify/docker/access.json`. The directory is private.
The deployment SSH key and application encryption key are generated once and
retained on restart. Docker volumes hold PostgreSQL, Redis, and SSH host keys.
Stopping does not delete volumes or deployed applications.

The SSH target container can control the local Docker daemon, as required by
Coolify. Its SSH port and Docker socket are not published on the host network.
The Coolify UI, realtime connection, and terminal bind to loopback ports
7400–7402. Treat its admin login as administrative access to that Docker host.

## Standalone QuickSand

Use an environment containing Workplace, `omniagents[sandbox-quicksand]`, and
the QuickSand QEMU/Ubuntu extras. No host Docker Engine is used by this mode.

```sh
python -m omniagents_workplace.deployments.coolify up --backend quicksand
python -m omniagents_workplace.deployments.coolify status --backend quicksand
python -m omniagents_workplace.deployments.coolify stop --backend quicksand
```

Open `http://127.0.0.1:7500` after startup completes. Progress is recorded in
`~/.local/share/omniagents/coolify/quicksand/vm.log`; credentials are in the
adjacent `access.json`. The VM has 2 CPUs, 4 GB RAM, and a 24 GB virtual disk.
Actual host disk use grows with installed images and application data.

The first launch installs Docker and Compose in the Ubuntu guest and provisions
the same Coolify stack. A private disk image is saved after provisioning. The
background process keeps the VM running. `stop` requests orderly shutdown:
Coolify's containers stop, then the framework saves the guest disk before
terminating QEMU. The next `up` restores that image and restarts the services.
The image contains this installation's credentials and must not be distributed
as a public base image.

Guest ports 7500–7502 are forwarded to host loopback. All guest Docker images,
volumes, deployment credentials, and application files remain inside the VM.
Root inside the guest does not require host root. Hardware acceleration is
used when available; software emulation is slower. Abrupt host termination
retains the last completed disk save; it is not equivalent to an orderly stop.

## Separate instances and deployment scope

`--data PATH --port PORT` selects a separate instance. Keep using the same
arguments for subsequent commands. An existing directory cannot be silently
reassigned to another backend or port. Each instance has unique Docker project,
network, credentials, and volumes. Back up the data directory together with the
Docker volumes, or the complete QuickSand data directory for VM mode.

Automatic Coolify upgrades and public registration are disabled. The generated
server uses the SSH target and has its automatic reverse proxy disabled, so
initial startup does not claim host ports 80/443. The managed installation adds preview routing and PR lifecycle integration as
described above. Standalone launchers leave application routing to their operator.
