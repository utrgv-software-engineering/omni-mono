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

**Actions** use pinned `nektos/act` to execute workflows at the run's commit.
Workplace records triggers, logs and checks; act handles actions, expressions,
matrices, containers and services. Docker mode uses a private Docker-in-Docker
controller; QuickSand mode runs act and Docker inside a VM. Factory runtime
jobs stay outside tool VMs through a restricted bridge. See [workflow execution](docs/actions.md).


An optional [Coolify deployment service](docs/coolify.md) runs in Docker or a
persistent QuickSand VM for local deployment practice.

## Install and run

The published `omniagents-workplace` Python package includes the compiled web UI,
MCP reference schemas, Canvas support code, and optional example seeds. Users do
not need Node/npm, a JavaScript build, or the omni-mono checkout to serve it.
Install from the package index configured for your organization:

```sh
uv tool install omniagents-workplace
```

Git is still required for repository operations. The factory runtime is a
separate `omniagents` installation; installing Workplace alone does not start an
agent. QuickSand workflow execution is available through the `[quicksand]` extra.


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

Azure deployments can optionally enable [Microsoft sign-in through App Service
Easy Auth](docs/azure-easy-auth.md). Local login remains the default; external
identities must be explicitly linked to existing accounts.
The proposed [Azure web/compute deployment plan](docs/azure-web-compute-plan.md)
describes future App Service, PostgreSQL, and private-VM support.

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

## Chat

The **Chat** tab (`/personal`) lists agents first: the project's
`agents.chat_entrypoint` (the default), the rest of `agents.chat_agents`, and any
other agent people have Side Chats with. An agent only automations use is not
listed; its automation conversations open from Automations ("Open chat"). Each agent row shows whether any
of its chats needs input, is working, or has unread activity. Selecting an agent
shows its chats:

- **Main Chat**: one per agent, shared by every administrator, pinned (it cannot
  be renamed, archived or deleted). The default agent's is the original
  `factory-personal` conversation, so its history carries over. Automations
  never post into it.
- **Side Chats**: started with the **+** beside **Side Chats**, shared with every
  administrator; the first message names one until it is renamed. Rename,
  archive/restore and delete are in each row's menu.
- **Automations**: one chat per automation conversation, grouped and collapsed.
  Routing decides which: each automation runs the agent its route names (an admin
  override in Factory → Automations, then the automation's `agent:`, then
  `agents.entrypoint`), in a conversation keyed by its source thread and agent;
  scheduled and Shared-file events start a new one unless the automation says
  `conversation: thread`. A chat is titled for its source (`PR #12 · Add
  pagination`, a Linear issue, a Slack message's text, or the automation's name)
  and archives itself once its pull request or issue closes (once: an
  administrator can restore it). Any administrator can reply in one to correct the
  agent where the work happened.
- **Archived**: collapsed, when there are any.

Every person's message shows their name and initials, and reaches the agent as
`[From <name> (@<username>)]` followed by the text, so the agent knows who is
speaking. A working chat says who it is working for. Unread is per
administrator, kept by Workplace (`POST …/chats/{id}/seen`). Deletion requires an
idle chat with an empty queue and preserves shared memory and Library content;
archive only hides a chat. Wide screens use a persistent sidebar, smaller
screens a drawer; direct chat links override the last remembered chat.

The gauge beside an agent's name opens its **readiness**
(`GET /workplace/api/personal/agents/{agent}/readiness`), week by week: messages
from people, lessons it learned (those attributed to one person in brackets),
its own automation runs and failures, and the judges' pass rates on every run
graded for it, including where another agent called it. Lessons that level off
while people keep working with it, and passing grades, are the evidence for
adding it to a pipeline; neither is a verdict.

The page embeds the shared `@omniagents/chat-ui` conversation, composer, tool
activity, questions, and approvals. Its authenticated WebSocket gateway binds
runtime requests to the selected registered chat session and keeps factory credentials
server-side. Idle submissions use the native run protocol; messages submitted
while busy use the runtime queue. Stop and approval IDs are verified against the
selected conversation before forwarding them. Policy changes are unavailable.

The runtime owns execution and conversation history. Browser refresh/close does
not stop work. Pending queued messages are recovered when the runtime starts;
interrupted active runs are not replayed. The resident sandbox lives for
the server process; save repository work in branches/PRs before restarting.

The browser receives no factory credential and cannot access unregistered sessions or choose an agent,
role, or approval-policy override. All administrators can answer the shared
conversation's questions or stop its current run. Ordinary members have no access.
Existing authentication, session revocation, origin checks, and the read-only
Factory transcript proxy remain enforced. Tools, credentials, and memory are shared.

Every agent works on the factory's one shared machine, which survives restarts.
One factory runtime holds the factory's intake lock. Factory promotions wait for
chat work to finish.

Verification: `tests/test_personal.py` covers shared identity, admin access,
policy injection, pending controls, and WebSocket reconnection. Runtime
integration tests cover per-automation routing and event deduplication without
model calls. Live model comparisons remain deferred. Projects, artifacts, voice, and
desktop control remain future slices.


The Workplace shell uses a 76px app rail from 768px upward, with main tabs
centered between the brand and bottom controls and scrolling on short screens. Phones use three
primary destinations plus More; Chat leads for a factory's administrators. Remaining apps and Account are available through More. Connections lives in
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

## Editing agents and automations

With review off, everything about an agent and an automation is managed here,
saved as a version of the factory and live from the next run; nobody needs to
know the factory is a repository. With review on (Account → Factory changes),
the same edits become pull requests and go live when merged.

**An agent's page** (the pencil in Chat's header, `/agents/<agent>`;
administrators) has:

- **Instructions**, and a link to the template it builds on.
- **Settings** as fields: the name shown, what it does, its model (or the
  template's), what it can do (capabilities; those its template gives are
  marked), the agents it can hand work to (a hand-off is a run of that agent's
  own, in its conversation for where the request came from, so it shows under
  that agent in Chat; `agent_tools` `mode: conversation`), whether people can chat with it,
  whether Chat opens with it, and whether automations that name no agent run
  it. **Edit the settings file** shows its `agent.yml` for anything else (MCP
  servers, memory). **Delete agent** removes its folder and its place in Chat;
  it is refused while it is a default or another agent hands work to it.
- **Skills**: every skill it has, in the order the framework searches them
  (its own, its template's, the factory's), each saying who has it and which
  skill of the same name it replaces; edited as fields (when to use it, its
  steps), added for one agent or every agent, moved between the two, deleted
  with their folder.

**Add agent** (at the bottom of Chat's agent list, and beside an agent page's
agent menu) takes a name, what it does, its template
and whether people can chat with it. A **template** (a base agents extend) has
its own page under Templates in the agent menu: its instructions, settings
file and shared skills.

**Automations**: **New automation** on the Automations page, and **Edit** and
delete on each one's page, as fields: what it does, when (a schedule in words
or a cron, or an event with its repositories, labels, branches, channels or
teams; several triggers allowed), which agent does it, and what it's told.
Settings the form doesn't show are kept; an automation whose file has template
logic is edited as its file.

Repositories' own skills (`<repo>/.<product>/skills`, a mounted repository's
`skills/`) belong to those repositories and change by pull request there; a
run's page lists the skills it was offered and loaded and where each came from.
A factory never reads the installation's global skills folder, which is outside
version control.

How it works: the factory lists and checks every edit
(`GET /integrations/definition`, `POST /integrations/definition/check`); a
change to an agent's or a template's settings, an agent added or deleted, a
capability, or project.yml's `agents` section is checked by loading the project
with it. Saving is a commit on the factory repository's `main` as the person who
saved, and the Workplace asks the factory to move its checkout to it
(`POST /integrations/definition/advance`). Instructions, skills and automations
are read again on each run; settings, agents and the agents section reload the
running factory's agents (each running agent's next run is built from its new
settings; MCP servers change for new conversations). Code (`.py`),
dependencies, sandbox profiles and the rest of project.yml (connections,
tracing) still change by release. Runs record the commit they ran. A save over
a file someone else changed since it was opened is refused; **Version history**
restores old versions. The factory refuses to move while its checkout has
changes that aren't committed. The repository is the one the factory runs from
(workplace.yml's `factory.repository`, else the one its checkout came from),
whichever provider serves the product repositories.

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

## QuickSand workflow jobs

`--workflows quicksand` runs ordinary CI jobs in disposable VMs with their own
Docker Engine. Explicit factory runtime actions keep the agent process on the
host and route its tools into separate QuickSand VMs. The host Docker socket is
not shared. See [setup, runtime boundaries and tests](docs/quicksand-runner.md).

## Build a distributable package

From the omni-mono checkout, with Node/npm and Python available:

```sh
uv run --project omniagents-workplace --group dev python omniagents-workplace/scripts/build_package.py
```

This installs the locked npm dependencies, builds the current UI, replaces the
packaged assets, and produces a source archive and wheel in
`omniagents-workplace/dist`. Use `--skip-install` to reuse an existing npm install,
or `--assets-only` for frontend/backend development. `make build` delegates to
the same Python command. No publishing or version change happens during a build.

Python distribution builds fail if the UI is absent or its entry assets are
missing. The source archive includes the compiled UI, so building a wheel from
that archive does not require Node or sibling npm workspaces. For a release,
always use the build command above to refresh assets from the current source.

Verify the wheel in a fresh environment outside the checkout:

```sh
uv venv /tmp/workplace-package-check
uv pip install --python /tmp/workplace-package-check/bin/python omniagents-workplace/dist/omniagents_workplace-0.1.0-py3-none-any.whl
/tmp/workplace-package-check/bin/python -I omniagents-workplace/scripts/check_installed.py
```

On Windows, use a temporary directory and its `Scripts/python.exe` instead.
The check serves the installed UI and every asset, checks the CLI, and verifies
bundled schemas and seed workflows without installing the framework.

`make publish` retains the existing clean, synchronized `main` branch guard and
uploads the built distributions to Gemfury using `FURY_USER` and `FURY_TOKEN`.

## Multiple factories

`manage` runs one Workplace installation with shared sign-in and separate factories:

```sh
omniagents-workplace manage --data ~/.local/share/omni-factories --port 7300
```

Open the setup link printed in the terminal. In **Factories**, choose **New factory**,
enter a name and a project directory containing `project.yml`, and choose Personal
or Pipelined. Each factory needs its own non-overlapping project directory. Use
**Start** to launch it, **Open** to enter its Workplace, and **Access** to invite
people and assign factory roles. **Account → Factories** returns to the picker.
Two tabs can use different factories simultaneously.

To start agent runtimes, install `omniagents[sandbox-quicksand]` into the same
Python environment as Workplace. Local runtimes use the project's
`sandbox_profile_quicksand.yml` (or a QuickSand `sandbox_profile_default.yml`).
Each gets its own persistent VM, runtime home, provider sign-in, config, logs,
Library and Workplace database. Configure GitHub and model accounts inside each
factory. Nothing is imported from an existing single-factory installation.
Factory processes started through the UI restart with the installation; **Stop**
disables that factory's automatic startup. Process failures appear in the picker;
logs are in `DATA/factories/ID/runtime.log`.

Workflow execution is off unless a clean QuickSand runner image is supplied:

```sh
omniagents-workplace manage --data ~/.local/share/omni-factories \
  --workflow-quicksand-image /path/to/clean-runner
```

Use the [QuickSand workflow-runner instructions](../omniagents/docs/guide/quicksand.md)
to prepare the image. It may be shared; each job gets a disposable writable VM.
Trusted agent-runtime jobs use the credentials and tool profile of their factory.
There is no host-shell workflow fallback in managed mode.

For deployment, set `--host` and `--public-url` for your listener and HTTPS reverse
proxy. `--sandbox-url` defaults to `http://10.0.2.101:7300`; the generated profiles
forward that guest port to the installation's actual listener port. Run one
installation process per data directory. The installation lock rejects a second
process using the same data. Back up the entire data directory together with its
project directories while the installation is stopped.

See [the isolation design](docs/multiple-factories.md) for the boundaries. Existing
`serve` and `up` commands keep their single-factory behavior.

### Hosted factories with Docker

The installation host can instead supervise separate Docker runtime containers:

```sh
omniagents-workplace manage --data /data/installation \
  --public-url https://your-workplace.example \
  --internal-url http://workplace:7300 --host 0.0.0.0 \
  --docker-image your-factory-image --docker-network your-factory-network \
  --docker-host-data /host/path/to/installation \
  --workflow-image your-workflow-image --template /factory-template
```

The host needs the Docker CLI/socket. `--docker-host-data` is the Docker host's
path corresponding to `--data` inside the installation container. Each runtime
mounts only its own factory directory and gets its own credentials, runtime home,
project copy and persistent tool sandbox. Configure a separate sandbox-facing
port with `--sandbox-port` and `--sandbox-url` to expose only git/upload services
to tool containers. Workflow shell jobs use disposable Docker containers.

With `--template`, **New factory** creates a clean project copy; users need only
a name. Provider credentials and installation state are not
copied from the template.

For an existing deployment, `Installation.adopt` registers its stopped Workplace
data and Docker runtime without recreating either. It imports identities and
sessions into the installation registry and preserves factory-local IDs, provider
credentials, chat, Library and memory. Old machine endpoints remain aliases for
that original factory only. The registry and new factory directories under
`DATA/installation` are included in the existing Workplace backup operation.
Keep the deployment's installation Compose override on subsequent deployments.

Factory provider events pass through a [durable Workplace gateway](docs/event-gateway.md).
It persists accepted events in each factory's database and retries runtime delivery
across restarts. Provider stand-ins keep their own delivery semantics; Slack Socket
Mode ingestion lives in Workplace. This does not queue chat submissions or guarantee
delivery while Workplace itself is unavailable.

### Guest review links

Canvas and Library owners (and factory admins) can use **Share → Create link**
to let a stakeholder review an item without a Workplace account. Guests enter a
self-reported name when they first comment. Canvas supports pinned comments and
replies; Library pages and documents have comment threads too. Feedback is saved
with the item, and does **not** enqueue an agent run. Agents can read it on request
using Canvas `list_comments` or Library `artifact_comments`.

Share links show the current version of one item. **Share → Revoke** disables a
link, including its file URLs; deleting the item disables all its links. A link
is a bearer credential: anyone who receives it can read and comment on that item.
Guest names are attribution, not verified account identities. Guests cannot edit,
resolve, delete, browse version history, or access the rest of Workplace.

Workplace serves `robots.txt` with `Disallow: /`, sends `X-Robots-Tag: noindex,
nofollow, noimageindex`, and sets the equivalent HTML metadata. Share credentials
live in the URL fragment and are required by the review API; an ordinary crawler
request only receives the generic page shell. File responses are non-cacheable
and links use `no-referrer`. Robots directives guide cooperative crawlers; they
are not access control or a guarantee against someone copying shared content.

### Shared files and folder workflows

**Shared** is a file browser for the current factory. Upload files or folders,
including drag-and-drop; create, rename, move, delete, preview, and download them.
Folder downloads are ZIPs. **Use in chat** adds the sandbox path and current file
revision to the chat composer without sending it.

The files live in the factory's persistent `shared/` directory and appear at
`/workspace/Shared` in both Docker and QuickSand sandboxes. Agent-created files
appear in the same browser. Files, upload journals, workflow configuration, and
change records are included in Workplace backups. Each factory has its own data;
Shared requires a signed-in factory member and is separate from guest review links.
Managed installations configure the host path automatically. For a standalone
runtime, configure `SHARED_FILES_HOST_PATH` and make that host directory accessible
at the same path inside the runtime container, as with `LIBRARY_FILES_HOST_PATH`.

Uploads and sandbox edits record changes without starting agents by default.
Admins can open **Workflows** to select a folder, filename patterns, change types,
and instructions, then explicitly enable a workflow. One upload batch produces
one invocation per matching workflow. Folder creation alone does not run a file
workflow. Agent-originated changes are excluded unless explicitly enabled; keep
workflow outputs outside their watched input folders to avoid loops.

Events go through the durable Workplace gateway and normal factory automation
intake, including retry and delivery deduplication. Personal factories use their
shared generalist conversation; pipelined factories use the normal automation
entrypoint. Pending deliveries are cancelled if the workflow is disabled, deleted,
or edited before delivery. The workflow dialog shows recent changes and delivery
status; agent execution appears in the existing factory run history.

Events identify paths and revisions, not historical copies of file contents.
Agents must check the current file revision when acting on an older event. Direct
sandbox writes are observed after they stabilize; agents should publish completed
files with an atomic rename. Workflow folder paths are explicit: update a rule if
you move its watched folder.

Backups check available capacity before starting and retain disk headroom while
copying. A failed backup removes only its own incomplete output; completed backups
keep the configured retention policy.

## Model endpoint access

Model connections use the deployment and factory egress allowlists. Private
destinations must also be reachable under the deployment's network rules.
Models do not require a second endpoint approval in Allowed services.
Administrator access and, under strict policy, governance ownership are still
required to change model configuration.

Allowed services continues to govern integrations and MCP servers for Controlled
and Confidential factories. Legacy model entries are ignored and are no longer
shown in its editor. Workplace clears the obsolete model-endpoint restriction in
the environment it supplies to managed runtimes; running processes must restart
to receive that change. This does not modify network allowlists or firewall rules.
