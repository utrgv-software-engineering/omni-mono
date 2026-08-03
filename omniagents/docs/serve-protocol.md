# Serve Protocol

**Protocol version: 1** (`omniagents.product_serve.SERVE_PROTOCOL_VERSION`)

The wire contract between a desktop/cloud **launcher** and a product's
reserved `serve` / `sandbox-host` subcommands
(`omniagents.product_serve`, mounted by `omniagents.product_cli`). Any
product with a `product:` block in its `project.yml` speaks this
protocol from convention; this document is what launchers target.
Changing the spawn argv, the readiness payload, or the error protocol is
a protocol revision — bump `SERVE_PROTOCOL_VERSION`.

A launcher can verify compatibility before spawning with
[`<prog> describe`](#prog-describe).

## `<prog> serve`

### Spawn

The launcher spawns the product CLI with:

```
<prog> serve --output json
    --auth-token <client-secret>
    --agent-host-control-token <control-secret>
    [--source <json>]...
    [--credential <json>]...
    [--project <id>]
    --snapshot-dir <dir>
    [--session-id <id>]
    [--container-id <id>]
    [--profile <path>]
    --workspace <cwd>
```

There is no port flag: serve picks a free port and reports it in the
readiness payload. Flag semantics:

| Flag | Req. | Meaning |
| --- | --- | --- |
| `--output json` | yes | Emit the machine-readable readiness line (the only format launchers parse). |
| `--auth-token <client-secret>` | yes | Authenticate ordinary launcher/renderer RPC clients. This value is reported as `auth_token` in readiness. |
| `--agent-host-control-token <control-secret>` | yes | Authenticate the launcher main process for privileged AgentHost workspace, profile, environment, and binding operations. It must differ from the client secret and is never reported in readiness. |
| `--source <json>` | repeatable | One workspace-source descriptor per flag (see below). No flags = empty workspace. |
| `--credential <json>` | repeatable | One boot-time git/CLI credential descriptor per flag (see below). |
| `--project <id>` | no | Launcher project id; selects the per-project profile layer (`<config>/projects/<id>/sandbox.yml`). |
| `--snapshot-dir <dir>` | yes (launcher always passes it) | Directory for per-session snapshot tarballs (`<session-id>.tar`); enables resumable workspaces. |
| `--session-id <id>` | no | Stable id of a resumable workspace. Omitted on first start — serve generates one; the launcher captures it from the payload (`_debug` aside, snapshot naming) and passes it back to resume. |
| `--container-id <id>` | no | Docker container id from a previous payload; requests a warm reattach (`resume: "reused"`), silently falling back to fresh + snapshot rehydrate. |
| `--profile <path>` | no | Explicit profile YAML (launcher-written per-session profiles, e.g. cloud `host_bridge`); bypasses the user/project layers. Omitted = the product's packaged default profile chain. |
| `--workspace <cwd>` | yes | Host directory substituted for `${workspace_dir}` in the resolved profile; also the spawn cwd. |

Additional flags exist (`--host`, `--port`, `--agent-project`, `--agent`,
`--output human`) for tests, scripting and terminal users; launchers do not
pass them.

#### Source descriptor JSON

Each `--source` value is one JSON object:

```json
{"kind": "local" | "local-git" | "git-remote",
 "mountName": "<slug>",
 "path": "<host dir>",                       // local | local-git
 "repoUrl": "<url>",                         // git-remote
 "auth": {"tokenEnv": "<ENV>", "username": "<user>"},  // git-remote, optional
 "ref": "<branch/tag/sha>"}                  // local-git | git-remote, optional
```

Each source seeds one subdirectory of the sandbox workspace
(`/workspace/<mountName>` on mounting backends). Private-remote tokens
are passed by env-var *name* (`auth.tokenEnv`); the value rides in the
spawned process environment, never on argv.

#### Credential descriptor JSON

Each `--credential` value is one JSON object:

```json
{"url": "<remote url>", "username": "<user>", "tokenEnv": "<ENV>"}
```

Configures git + host CLIs (`gh`, `az devops`) inside the sandbox at
boot for every linked host the project uses — not just cloned remotes.

### Readiness

Serve prints **exactly one** stdout JSON line containing both
`sandbox_url` and `ui_url`; the launcher stops parsing stdout after it.
Shape (field-for-field, built by
`omniagents.product_serve.build_readiness_payload`):

```json
{"sandbox_url": "http://127.0.0.1:<port>",
 "ws_url": "ws://127.0.0.1:<port>/ws",
 "ui_url": "http://127.0.0.1:<port>",
 "auth_token": "<auth>" | null,               // send as "Authorization: Bearer" (or POST /auth/ws-ticket)
 "services": {"<name>": "<url>", ...},        // resolved profile-service endpoints
 "ports": {"ui": <port>},
 "container_id": "<docker id>" | null,        // docker backends only
 "resume": "reused" | "rehydrated" | "fresh" | null,
 "_debug": {"client": "<backend type>",
            "manifest_root": "<path>",
            "profile_sources": ["<layer path>", ...]}}
```

Consumers authenticate WebSocket connections with the `auth_token` value in an
`Authorization: Bearer` header (or exchange it at `POST /auth/ws-ticket` for a
one-time dial ticket). The historical `?token=` suffix on `ws_url` is emitted
only when `OMNIAGENTS_LEGACY_QUERY_AUTH=1` is set on the serve process, and
query-string tokens are only *accepted* under the same flag — see
`omniagents/rpc/protocol.md` (Authentication).

Optional fields a launcher must tolerate: `container_id`,
`container_name`, `resume`, `_debug` (the default implementation emits
`container_id`, `resume` and `_debug` always, `container_name` never).
The line is serialized with `json.dumps(..., default=str)` so it can
never fail to serialize.

### Launch failure

Structured failures (profile resolution, bad `--source`/`--credential`,
workspace seeding) exit **non-zero** after printing a JSON line to
**stderr**:

```json
{"error": "<message>"}
```

The launcher surfaces the last stderr JSON line containing `"error"`;
anything else falls back to the raw stderr tail.

### Post-ready surface

After the readiness line the launcher:

- HTTP-polls `ui_url` and connects `ws_url` (path `/ws`) — the
  framework server backend (`omniagents.backends.server.app`).
- Calls `server_call` functions over the WS: `sandbox.*` (state,
  switch, pause lifecycle, discard_snapshot), `session.ensure`,
  `terminal.create` — all framework builtins activated by the standard
  capability packs.

Shutdown is signal-driven (SIGTERM/SIGINT/SIGHUP): serve skips the
graceful drain, quiesces in-flight runs (bounded), and always reaches
teardown — which persists the workspace snapshot unless
`sandbox.discard_snapshot` was called.

## `<prog> sandbox-host`

The laptop half of computer-as-sandbox. Spawn:

```
<prog> sandbox-host --host 127.0.0.1 --port 0 --workspace <ws>
```

The **first stdout line** is the handshake:

```json
{"exec_port": <bound port>, "host": "127.0.0.1"}
```

The launcher registers `exec_port` with its relay; a cloud
`HostBridgeSandboxSession` then drives this machine's exec/file/PTY
surface over the WebSocket
(`omniagents.core.sandbox.host_bridge.serve_host_connection`). One
long-lived server serves many sessions (one per connection). `--port 0`
(the launcher's spawn) binds an ephemeral port.

## `<prog> describe`

Product introspection for launchers and tooling:

```
<prog> describe --json
```

prints one JSON object:

```json
{"name": "<distribution name>",
 "prog": "<cli program>",
 "label": "<display label>",
 "slug": "<platform slug>",
 "version": "<product package version>",     // importlib.metadata; framework version fallback
 "config_dir": "<resolved absolute path>",
 "env_prefix": "<ENV_PREFIX>",
 "update": {"index_url": "<pep503 page>", "command_hint": "<prog> update"} | null,
 "serve_protocol": 1}
```

`serve_protocol` is the version of this document the installation
implements.

## `<prog> --embedded` (non-serve embed)

The launcher's *other* spawn mode: running the product's ordinary chat
runtime embedded in the desktop app instead of the sandbox-session
`serve` server. `--embedded` is a fallthrough-run flag, not a
subcommand — it may appear anywhere in an invocation that falls through
to the agent run (it is ignored on subcommand dispatch), and the
framework CLI host (`omniagents.product_cli.product_main`) intercepts
it:

- `--embedded` is stripped from the argv passed to the run.
- `OMNI_WEB_AUTO_OPEN=false` is exported — web mode must not open a
  browser; the launcher owns the window.
- `<ENV_PREFIX>_UPDATE_CHECK=0` is exported — the launcher owns update
  UX, so the startup update notice is suppressed through the standard
  updater gate.
- With `--mode web` (or `-m web`) and no explicit `--output`,
  `--output json` is appended so the web runtime's readiness line is
  machine readable.
- If the product declares a model catalog and no models are configured,
  the interactive setup wizard is **not** started; instead one JSON
  line is printed to stdout and the process exits `1`:

  ```json
  {"error": "No model credentials configured"}
  ```

## Profile resolution (context)

`serve` resolves its sandbox profile through the conventional product
chain (`omniagents.core.sandbox.profiles.resolve_product_profile`), last
layer wins on key conflicts:

1. the product's packaged default — `paths.sandbox_profile` in
   `project.yml`, else a `sandbox_profile_default.yml` sibling of the
   manifest;
2. `<config>/sandbox/default.yml` (launcher-written global settings);
3. `<config>/projects/<projectId>/sandbox.yml` (launcher-written
   per-project);
4. `--profile <path>` (explicit; skips layers 2-3).

The same resolver is used for AgentHost environment provisioning, so product
layers apply consistently to replacement environments.
