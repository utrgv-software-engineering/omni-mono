# omniagents-workplace

A workplace for building and evaluating software factories: the Slack,
GitHub and Linear a factory works in, self-hosted. It serves the protocols a
factory uses against the real services (their MCP servers, git, events and
webhooks), so a factory's configuration does not change: a few environment
variables point it here. People sign in to its UI and work in it as they
would in the real services; agents act as apps.

- **Slack.** Slack's MCP server (`/slack/mcp`), the tools and response formats
  of `https://mcp.slack.com/mcp` (messages, threads, reactions, search, file
  uploads), for the agents; events over Socket Mode (or signed HTTP
  requests) for the factory's server; and the Web API methods a Slack app
  uses (`/slack/api`).
- **GitHub.** GitHub's MCP server (`/github/mcp`), the tools of
  `https://api.githubcopilot.com/mcp/` (the `default` and `actions`
  toolsets) with their results, for the agents; the repositories over git
  (`/github/<owner>/<repo>.git`), pushed to with a token, for the sandboxes;
  issues, pull requests, reviews, branch protection and Actions workflows
  (run on this machine); and GitHub's webhooks, signed, to the factory's
  server.
- **Linear.** Linear's MCP server (`/linear/mcp`), the 60 tools of
  `https://mcp.linear.app/mcp` with their results: issues (sub-issues,
  relations, due dates, estimates, attachments), projects with milestones,
  updates and documents, cycles, labels, team workflows and each person's
  inbox; agent sessions (delegate an issue to an app, or @-mention it; stop
  one from the issue), sent to the app as Linear's signed
  `AgentSessionEvent` webhooks; and the part of Linear's API an agent
  reports through (`/linear/graphql`: `agentActivityCreate`,
  `agentSessionUpdate`).
- **Canvases.** Design boards people and agents make together, as the
  design canvas of Claude's artifacts has them: a canvas's index
  (`canvas.json`) and one `<Name>.dc.html` page per board. Agents use the
  canvas MCP server (`/canvas/mcp`): read a canvas, publish changes against
  the version they read (refused when someone published first), the version
  history, and comment threads. The **Canvas** tab lays the boards out live
  (each a sandboxed page, run by the workplace's own `canvas/support.js`),
  moves them, shows any version and restores it, and pins comments to them.
- **The wire.** Every exchange (an event delivered and acknowledged, an MCP
  tool call, a Web API call, a clone or push) as it happens, with its
  request and response, so you can see what a factory did and how.

**Actions** run a repository's workflow files (`.github/workflows/*.yml`)
on this machine: their steps run here, in a fresh checkout of the commit,
with this machine's tools. A job's containers and services are not used;
`actions/checkout`, `setup-python` and `setup-node` have local stand-ins,
and other actions are skipped (the log says so). `runs-on: self-hosted`
jobs run on the workplace's self-hosted runner, in the repositories an admin
allows (the repository's Settings → Runners).
Triggers, branch and path filters, matrices, `needs`, `if`, outputs,
re-runs, cancelling and `workflow_dispatch` work as on GitHub.

## Run it

```bash
omniagents-workplace serve            # http://127.0.0.1:7300
```

A workplace keeps its state in a data directory (`--data`, default
`$XDG_DATA_HOME/omniagents-workplace`): a SQLite file and the repositories.
Started again on the same directory, it carries on where it was.

A new workplace is empty. The server prints a setup link: open it to name
the organization (its Slack workspace, GitHub organization and Linear
workspace) and make the first account, an admin. From **People** an admin
adds people; each gets an invite link to choose a password with (a new link
resets a password). A person is in all three services under one username.

Password changes revoke all existing browser sessions and outstanding invite
links, then issue a new session to the person changing the password. Invite
redemption is single-use under concurrent requests. Login failures persist
across restarts. New passwords require 15 characters in staging and production
(8 for development fixtures), with a maximum of 4096. Existing password hashes
are upgraded to scrypt N=16384, r=8, p=5 on successful authentication; existing
short passwords from development cannot sign in under the production policy.
Use an admin-issued invite to reset them before migration.

Browser mutations must come from the configured `--public-url` origin. Set that
to the exact public HTTPS address, including any nonstandard port. Native clients
without Origin/Referer or cross-origin Fetch Metadata can still authenticate with
the session cookie. Raw service traffic (Wire) and factory run transcripts are
admin-only because they can contain private tool results. Memory's UI admin
access applies only to that request; personal MCP tokens retain their own ACLs.
The first upgrade removes legacy cross-store account grants that cannot be
distinguished from the former implicit admin grants. Personal/team grants and
app-owner grants remain; admins can explicitly restore intended access afterward.

For public deployments, use `--environment production --trust-proxy` with an
HTTPS `--public-url` and explicit `--proxy-ips` (comma-separated proxy IPs/CIDRs;
loopback by default; `*` is refused in production). Firewall the application port
so only that proxy can reach it. HTTPS responses include HSTS. The CLI holds a
per-directory server lock and explicitly runs one worker: account state is not
shared between processes. New data and backup directories are private (0700);
review permissions of existing directories separately.

Production/staging sessions expire after 12 hours; development sessions after
30 days. `WORKPLACE_SESSION_SECONDS` overrides this (300–2592000 seconds), and a
shorter policy also limits existing sessions. Role changes revoke sessions and
invites. Disabling an account permanently revokes its personal service tokens;
re-enabling it does not restore them. Password changes revoke browser sessions
and invites, but personal service tokens must be revoked separately.

The server caps authentication bodies at 16 KiB, other request bodies (including
uploads and git pushes) at 64 MiB, and WebSocket messages at 1 MiB. Authentication
endpoints permit 30 requests per minute per client address, in addition to the
persisted account failure limit. The source limiter is process-local and resets
on restart. Set matching proxy limits, connection limits and timeouts to reject
large or slow requests before they consume application resources.

Security events use the `omniagents_workplace.security` logger and contain event
names, account IDs, outcomes and service names—not passwords or tokens. The CLI
writes these events to standard error. Configure retention and access control in
the hosting platform. MFA/SSO still requires an identity-provider or access-gateway
integration; do not expose this installation publicly before that and the proxy,
firewall, secret storage and backup configuration are verified. Gateway policy
must also account for machine API/MCP clients rather than exposing an unprotected
alternate browser entrance.

The web build applies `scripts/harden-mcp-ui.cjs`: a pinned backport of Zod's early
collection-size checks to MCP UI 7.1.1's bundled copy, in addition to the patched
Zod dependency override. `scripts/test-mcp-ui-security.cjs` exercises the actual
bundled schemas. Both run in the workplace Docker build; regular npm installs
apply the backport through `postinstall`. Installs using `--ignore-scripts` must
run the hardening script explicitly. A new MCP UI version deliberately fails the
backport until reviewed; remove it once upstream ships a patched bundled Zod.

Options: `--port`, `--host`, `--public-url` (the address people and links
reach it at) and `--data`. A workplace has nothing in it but what its people
and apps put there: seed data is for evaluations (below).

Repositories' workflows run on this machine as you (`--workflows host`, the
default, for your own computer). A workplace other people push to runs each
job in a container of its own instead (`--workflows docker
--workflow-image <image>`), or not at all (`--workflows off`). On a server,
see `deploy/README.md` in omni-factory, the factory template, for the rest:

- `--trust-proxy`, behind a reverse proxy;
- `--internal-url`, where a factory beside it reaches it;
- `--sandbox-port` and `--sandbox-url`, a port that serves sandboxes git and
  uploads only;
- `omniagents-workplace backup`, which copies the database and repositories
  while the workplace runs;
- `--self-hosted-image`, `--self-hosted-network`, `--self-hosted-volume` and
  `--self-hosted-env`: the self-hosted runner. Its jobs run in containers of
  that image, with this machine's Docker and the volumes and variables given
  (a factory's model sign-in, for its evaluations), on that network. Each
  job is reachable at its container's name (`OMNIAGENTS_WORKPLACE_HOST`), so
  an evaluation's sandboxes reach the workplace it serves;
- `omniagents-workplace webhooks --to <url>`, with the workplace stopped:
  points every app's webhooks at another host, keeping their paths. A copy
  of a workplace (staging, or a replay in a check) then sends its events to
  its own factory, not to the one it was copied from.

## A factory's workplace: `up`

A factory says what it needs in a workplace in its `workplace.yml`: the
organization, its app (and where the app's events go), its repositories
(with their files and labels), its Slack channels, Linear teams and
canvases. From the
factory's directory:

```bash
omniagents-workplace up                # makes what workplace.yml names, then serves
```

It makes what is missing and leaves what is there (run it again after
editing the file to add what you added; it never deletes), writes the app's
credentials to the file `env_file` names (the factory's `.env`, which
`omniagents run` loads) and serves the workplace. A new workplace's first
admin is `admin:` (name, username, email, and the password they sign in
with); with none, `up` makes the organization and the first visit's setup
page (on this machine, or the link `up` prints) asks who the admin is, after
which `up` makes the rest. The channels it makes are
default channels: everyone joins them, and the app is in each. It takes the
same options as `serve`; a `--data` whose workplace is another
organization's is refused. The Acme factory's is `workplace.yml` in
omni-factory, the factory template.

A repository in it can say:
- `protected: true`: its default branch changes only through pull requests;
- `required_checks: [names]`: the checks (workflow job names) pull requests
  into that branch must pass to merge, as GitHub's required status checks;
- `self_hosted: true`: its jobs may use the self-hosted runner.

`factory.repository` is the factory's own repository. It is protected and
may use the self-hosted runner, and it can name its `required_checks`.

## Apps: what agents act as

An app is an account in all three services with its own credentials: a
Slack member and the app authorized by it (a user token, and an app-level
token for Socket Mode or a signing secret for a request URL), a GitHub
account with a token, and a Linear agent with an OAuth token. An admin
registers one from **Apps**, sets where its events go (Slack's delivery,
GitHub's and Linear's webhook URLs, whose secrets are made with them), and
copies the environment a factory connects with. Credentials are made by the
workplace and can be rotated; removing an app revokes them all.

```bash
eval "$(omniagents-workplace env --app <username>)"     # the same environment, from the data directory
export OMNIAGENTS_CODEX_HOME=~/.config/omni_code         # an omni-code sign-in
cd omni-factory                                         # the factory's repository
omniagents run --project project.yml --mode server --port 8765
```

People connect their own MCP clients as themselves with **Your tokens**: a
token for each service's MCP server.

## Sandboxes reaching the workplace

A factory's runs work in Docker containers, which reach the workplace at
`host.docker.internal` (an app's `GITHUB_GIT_URL`). With Docker Desktop
(macOS, Windows) that works as it is. On Linux, containers reach the host on
Docker's bridge network (`docker0`), where the workplace also listens; a
host firewall blocks that by default. Evaluations start a workplace per
scenario on a port in 7300-7399 (`OMNIAGENTS_WORKPLACE_PORTS`), so allow the
range. With `ufw`:

```bash
sudo ufw allow in on docker0 from 172.17.0.0/16 to any port 7300:7399 proto tcp
```

(`firewalld`: add `docker0` to a zone that allows the port.) A sandbox that
cannot reach the workplace fails its run with a clone error.

On the bridge the workplace serves only what a sandbox needs: git
(`/github/<owner>/<repo>.git/...`) and the upload URLs the Slack and Linear
tools hand out. The UI and its API and the MCP servers answer 403 there, so
an agent in a sandbox reaches the workplace only as its tools do. (Docker Desktop
forwards containers' connections to loopback, so there the boundary does not
apply.)

## Evaluations

`type: workplace` in an `evaluation.yml` gives every scenario a workplace of
its own, seeded from its fixtures, and the factory's sandbox with the
repository cloned from it (`omniagents_workplace.evaluation`):

```yaml
environment:
  type: workplace
  fixtures_dir: fixtures/workspaces   # repositories' content
  github_dir: fixtures/github         # issues and pull requests
  seed: acme                          # where each scenario starts: a seed file, or acme (bundled)
  # app: acme-factory                 # the app the agents act as (default: the seed's first)
```

Each scenario's workplace lives in a temporary data directory and goes when
the scenario ends; its agents act as the seed's app, with the credentials
that workplace made.

A seed is a workplace's starting content: its organization, people (accounts,
with the passwords in `workplace.passwords`; `workplace.admin` is the admin),
channels and messages, repositories, issues and pull requests, Linear teams,
and apps (whose tokens the workplace makes). The default is
`seeds/acme.yml`: Acme, the company of the Acme factory
(omni-factory),
with an app for its factory, Acme Factory (`acme-factory`).

## Develop

```bash
make assets     # build the UI (web/) into omniagents_workplace/static
uv run pytest   # tests
```

`web/` is a Vite app; `npm run dev -w omniagents-workplace-web` serves it
against a running workplace (`WORKPLACE_URL`, default `http://127.0.0.1:7300`).

## Memory

The **Memory** tab lets people search, create, edit and forget memories and
inspect their version history. Memory lives in `workplace.db`, covered by
the normal workplace backup. SQLite FTS5 searches text and tags; no embedding
service or model is required for storage and retrieval.

Stores are `personal:<account-id>`, `agent:<app-id>`, role stores below
`agent:<app-id>/<role>`, and `team`. Apps can read only their attached stores
and write only where granted. By default an app can write its own stores
and read the team store. People can write their personal and team stores;
admins can manage all stores through the Memory tab. Admins can grant access
with `POST /workplace/api/memory/grants` (`principal`, `store`, `access`).
Factory configuration can grant its app team write access:

```yaml
memory:
  grants:
    team: write
```

`omniagents-workplace env` exports `MEMORY_MCP_URL`, `MEMORY_MCP_TOKEN`,
`MEMORY_AGENT_STORE` and `MEMORY_WEBHOOK_SECRET`. Personal clients can create
and revoke a Memory token under **Your tokens**. `/memory/mcp` exposes:

- `search(query, stores?, limit?)`, `get(id)` (including history), `core(stores?)`.
- `remember(store, text, kind?, tags?, expires_at?, source?, date?)`.
- `update(id, text)`, `supersede(old_ids, text, source?)`, `forget(id, reason)`.

Kinds are `core` (at most 20 entries / 8000 characters per store), `fact`,
`log` (requires an ISO date), and `note` (expires after an hour by default).
`expires_at` is a Unix timestamp. Expired, forgotten and superseded entries
leave search but retain their history. Notes tagged `owner/repository#5`
expire when that GitHub issue or PR closes. Source metadata can include
`run_id`, `session_id` and `automation`; the UI links source runs.
Memory contents are excluded from the workplace-wide Wire feed.

Set `app.memory_webhook` in `workplace.yml` to receive `memory_created`,
`memory_updated` and `memory_forgotten` events. Events go only to apps with
access to the affected store. `Memory-Signature` is the hexadecimal HMAC-SHA256
of the raw body using `MEMORY_WEBHOOK_SECRET`; `Memory-Delivery` identifies
retries. The factory accepts these at `/integrations/memory/events` and
matches automations with `provider: memory`.

For agent configuration, see [the memory guide](../omniagents/docs/guide/memory.md).

## Personal generalist UI

The **Chat** tab (`/personal`) is available when both services start with
`FACTORY_ARCHITECTURE=personal`. All authorized administrators share one generalist
Main Chat per factory, including its questions, approvals, and stop control.
The chat picker creates additional shared side chats with independent histories,
text queues, and voice connections. They use the same generalist, tools, memory,
and Library. Side chats can be renamed, archived/restored, or deleted; Main Chat
stays pinned. The **+** beside **Side Chats** opens a conversation immediately;
its first message supplies its initial title. Row menus contain rename, archive,
and delete. Archived chats appear in a collapsible section only when present.
The list shows working, needs-input, and unread activity, refreshed every five
seconds while the page is visible. Wide screens use a persistent in-chat sidebar;
smaller screens use a drawer. Direct chat links override the last remembered chat.
Drafts and transcript reading positions survive switching chats.
Deletion requires an idle chat with an empty queue and preserves
shared memory and Library content. Archive only hides a chat from the active list;
it does not stop its work.
Messages carry their authenticated sender's name. Previous account conversations
are retained, but are not merged into this shared history.

`FACTORY_ARCHITECTURE=specialist` disables the Chat tab and its API. The factory
uses the foreman and the explicit agents in its automation definitions. In personal
mode, every matched automation—including nightly improvement—routes to Main Chat
instead. Main Chat and events use the same durable FIFO queue and runtime;
results appear in Main Chat and retain their originating event/thread for replies.
The generalist can use generic task workers. No separate dashboard service is needed.

The page embeds the shared `@omniagents/chat-ui` conversation, composer, tool
activity, questions, and approvals. Its authenticated WebSocket gateway binds
runtime requests to the selected registered chat session and keeps factory credentials
server-side. Idle submissions use the native run protocol; messages submitted
while busy use the runtime queue. Stop and approval IDs are verified against the
selected conversation before forwarding them. Policy changes are unavailable.

The runtime owns execution and conversation history. Browser refresh/close does
not stop work. Pending queued messages are checked when personal mode starts;
interrupted active runs are not blindly replayed. The resident sandbox lives for
the server process; save repository work in branches/PRs before restarting.

The browser receives no factory credential and cannot access unregistered sessions or choose an agent,
role, or approval-policy override. All administrators can answer the shared
conversation's questions or stop its current run. Ordinary members have no access.
Existing authentication, session revocation, origin checks, and the read-only
Factory transcript proxy remain enforced. Tools, credentials, and memory are shared.

Architecture changes require restarting both services with the same setting.
The runtime verifies its mode on Personal connections and refuses mode changes
with active automation work, live conversation leases, or queued messages. One
factory runtime holds the architecture lock. Stop intake and let work finish before
switching; historical conversations are retained and inactive agent endpoints are
disabled. Factory promotions also wait for Personal work to finish.

Verification: `tests/test_personal.py` covers shared identity, admin access,
mode mismatch, policy injection, pending controls, and WebSocket reconnection.
Runtime integration tests cover both routing modes, event deduplication, busy
switch rejection, and chat/event serialization on the real queue without model
calls. Live model comparisons remain deferred. Projects, artifacts, voice, and
desktop control remain future slices.


The Workplace shell uses a 76px app rail from 768px upward, with main tabs
centered between the brand and bottom controls and scrolling on short screens. Phones use three
primary destinations plus More; Personal leads in personal mode, Factory in
specialist mode. Remaining apps and Account are available through More. Connections lives in
the account menu; admins also have Developer tools → Event log there. Slack and Linear retain their own mobile navigation
drawers. The phone bar respects safe areas and hides while a soft keyboard
shrinks the viewport; the shell height follows that viewport to keep the composer
visible. Hardware-keyboard focus alone does not hide navigation.

Additional browser origins can be configured with the comma-separated
`WORKPLACE_ALLOWED_ORIGINS` environment variable. The public base URL is always
trusted; additional origins must specify the exact scheme, host, and port. This
allowlist applies to browser mutations and runtime WebSockets. For local access
alongside Tailscale, the local Compose override sets
`http://127.0.0.1:7395,http://localhost:7395`; public deployments should leave this
unset unless they deliberately serve the UI at additional origins.

Personal voice uses the shared chat's microphone control when the generalist
advertises `voice_backend: codex-live` and has a Codex sign-in. The gateway scopes
voice signalling to the shared session and attributes spoken requests to the
signed-in administrator. Only one browser can hold a call; another browser cannot
stop or take it over. Voice work uses the ordinary durable queue, including when
an automation is running. Closing voice stops capture but leaves accepted work
running. See [Codex Live](../omniagents/docs/guide/codex-live.md) for configuration
and transport details.

Personal enables the shared chat's optional `omniAvatar` header. It reuses the
Picture Frame canvas planet and reflects the conversation's connection, work,
approval/question and voice states. Microphone and received speaker tracks supply
WebRTC voice levels; the avatar never starts or controls work. Animation pauses
when hidden or outside the viewport and respects reduced-motion preferences.

## Repository connections and self-improvement

Administrators configure **Account → Repositories** in Workplace. Choose the
GitHub stand-in or GitHub; changing providers preserves the stand-in's data.
The factory uses its existing GitHub MCP and Git integration. No additional
GitHub tool server is required.

For GitHub, click **Sign in with GitHub**, open the displayed GitHub link, and
enter the code. The official GitHub CLI runs its device authorization flow on
the Workplace server; users do not create an app or paste credentials. After
approval, select repositories and save. Organization approval and SSO rules
still apply. No webhook setup is required.

The server needs `gh` installed (included in the Workplace Docker image). Each
attempt uses an isolated temporary CLI configuration, is bound to the initiating
admin session, and expires after 15 minutes. Cancellation, completion, expiry,
and server shutdown stop the process and remove its temporary credential files.
Only the verified access token is persisted, using the existing encrypted store;
it supplies repository discovery, the official GitHub MCP server, and Git.
The current connection stays active until a new sign-in succeeds. A successful
sign-in clears repository selection and leaves self-improvement off.

Alternatively, expand **Use an access token instead**, paste a token and click
**Connect GitHub**. Fine-grained PATs are limited to one resource owner; use device
sign-in for a shared connection across your authorized organizations.
Prefer a fine-grained token restricted to the intended repositories with Contents,
Issues and Pull requests read/write, and Actions and Checks read access. Add
Workflows write access only when editing workflow files. Tokens are validated
before replacing the connection, stored encrypted, and never returned by the
settings API. Replace the token when it expires or is revoked. Connecting or
replacing a token clears repository selection and leaves self-improvement off.

Alternatively, expand **Connect with a GitHub App instead** and enter your app's client ID,
client secret, and optionally a webhook secret. The form shows the callback and webhook URLs
to register in GitHub. Secrets are encrypted in the Workplace database; the
`github-connection.key` file in the data directory must be retained with backups.
Secret inputs are write-only: leaving one blank retains its current value.
Server variables `WORKPLACE_GITHUB_CLIENT_ID`, `WORKPLACE_GITHUB_CLIENT_SECRET`,
and `WORKPLACE_GITHUB_WEBHOOK_SECRET` remain optional defaults for managed setups.

Register a GitHub App with repository Contents, Issues, and Pull requests write
permissions, and Actions and Checks read permissions. Add workflow write access
only if the factory should edit workflow files. Subscribe to the repository
events used by the project's automations (issues, issue comments, pull requests,
reviews, check runs/suites, and pushes). Install the app on the repositories you
want to share, then use **Authorize GitHub App** and select the workspace repositories.
The token permissions or GitHub App installation determine what tools can access;
Workplace's repository selection determines which repositories are cloned and
which webhooks are forwarded. The shared factory uses the connecting admin's
GitHub identity. Signing in does not automatically enable self-improvement.

GitHub App tokens refresh server-side. The managed factory supervisor watches
the generated environment and reloads the installed factory version when idle;
standalone `omniagents run` processes need restarting after connection changes.
This reuses `GITHUB_MCP_URL`, `GITHUB_MCP_TOKEN`, and `GITHUB_GIT_URL` and adds
`GITHUB_REPOSITORIES` (a JSON list), `GITHUB_ACCOUNT`, and factory policy settings.
The selected repositories are also available to instruction templates through
`project.github.repositories`. Real mode lists selected repositories with links
to GitHub, and refuses stand-in UI/MCP requests rather than silently using them.
The local Git transport remains available for the deployment's bootstrap checkout.

Self-improvement is off by default, including existing installations with no
explicit setting. Select a **Factory repository**, then enable **Self-improvement**.
The repository must be selected, accessible, and writable. Linking alone does
not enable it. Mark improvement automations with `self_improvement: true` in
frontmatter; legacy `agent: improver` automations are also gated. Ordinary
repository work and chat remain available while the loop is off.

Schedules and dispatch recheck the policy; active or queued improvement turns
are cancelled when it is disabled or the target changes. The supervisor also
checks the policy before automatic promotion. An unavailable policy denies
improvement work and promotion. GitHub access loss disables the opt-in and
requires explicit re-enablement. Disabling cannot undo operations GitHub already
accepted, and cancellation is checked periodically during active turns. Repository
protections remain the authority for merge permissions; CI/CD gates are a separate
follow-up in the deployment plan.

Backups retain the encrypted connection data and its key. The `restore` command,
which is also used for staging replay, removes GitHub connection settings and
credentials and starts with self-improvement off: reconnect explicitly afterward.
Do not point a staging runtime directly at an unmodified production backup.
