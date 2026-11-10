# Workplace MCP Apps

Existing factory data and service APIs remain authoritative.

## Architecture

Workplace hosts factory-scoped installed apps. MCP tools advertise `ui://` resources;
OpenAI `openai/ui.entrypoints` global entries populate the existing navigation rail.
One server may expose multiple apps. Opening an app does not start an agent run.

Built-in Slack, GitHub, and Linear entrypoints reuse their current React views and
backend behavior. The built-in app server mediates UI requests as the signed-in
person, restricted to that app's routes. It never gives the iframe session cookies
or factory credentials. External servers use separately configured credentials.

The host uses the official MCP Apps AppBridge. HTML runs on an isolated origin,
with HTTP-header CSP and explicitly granted browser permissions. Installation,
credentials, permissions, and enabled state belong to one factory. Disabling an
app removes its entrypoints and rejects subsequent bridge requests without deleting
the underlying service data.

Recorder is an example app: capture audio, durably upload it to Shared, then commit.
Only the committed upload emits an event. Existing configured Shared workflows and
the durable gateway route that event to the factory. No new event bus, automatic
agent run on app opening, or audio encoded in conversation messages is needed.
Recording must survive navigation; pending audio must not be silently discarded.

## Implementation checklist

- [x] Factory-scoped registry, discovery, configuration, authorization and tests
- [x] Official AppBridge host, isolated sandbox, resource CSP and permissions
- [x] Built-in MCP server with multiple global entrypoints and scoped API access
- [x] Stand-in UI bundles and navigation migration, preserving existing deep links
- [x] Recorder capture, resumable upload, finalization and workflow configuration
- [x] Browser validation, security boundaries and compatibility tests
- [x] Production rollout

## Source references

Inspected local clones of `openai/mcp-extensions` (9e6d418) and
`modelcontextprotocol/ext-apps` (cf599cc). The official `transcript-server` example
shows microphone permissions and browser speech recognition, but does not implement
durable recording uploads or factory workflow delivery.

## Using apps

Open **Account → Apps** to enable built-ins or install a remote Streamable HTTP MCP
server. A remote server must advertise at least one global entrypoint. Its optional
bearer token stays on the backend; that connection is available only to the account
that installed it, within that factory. Installation and permission changes require
an administrator. Removing or disabling an app does not remove its service data.
The built-in server is available at `/workplace/api/extensions/mcp` under the current
factory prefix and uses the existing Workplace session authorization.

Slack, stand-in GitHub and Linear retain their navigation links and deep links.
Official GitHub's repository configuration remains a native Workplace view.
App tool calls are human UI interactions: they do not enter an agent transcript or
start a model run. The existing stand-in actions continue to emit their normal
service events, which configured factory workflows can consume.

Recorder saves acknowledged audio chunks on disk. Navigating to another tab keeps
capture running. Reloading or closing the browser stops microphone capture; already
acknowledged chunks remain available to finish or discard. Unsaved browser buffers
cannot survive a browser crash. Finalization atomically publishes one recording
through Shared and emits its normal completed-upload event once. Configure automations separately to process completed files; recording alone does
not run an agent. Recorder itself contains no workflow configuration. Transcription uses whatever tools and credentials the factory has configured.

## Sandbox origins and deployment

Every factory/app pair receives a distinct origin, preventing one app from reading
another app's iframe or browser storage. Localhost installations use
`<app-hash>.localhost` on the Workplace port. HTTPS deployments use
`<app-hash>.apps.<workplace-host>` by default. `WORKPLACE_APP_ORIGIN` can override
the sandbox base origin; it must be separate from the Workplace origin.

Route that base hostname and its app subdomains to Workplace. The installation's
existing Caddy on-demand TLS authorization endpoint approves only registered app
hostnames. DNS must resolve those names; sslip.io handles this for IP-based demos.
For a custom domain, configure wildcard DNS and preferably a wildcard certificate.
Standalone deployments must configure their proxy/certificates for these origins.
Sandbox hosts serve only the sandbox and robots.txt; authenticated APIs return 404.
CSP is delivered as an HTTP header from the resource's declared domains. Browser
permissions require both the resource declaration and a host grant.

## Compatibility evidence

Validated using the cloned OpenAI Bits & Bolts server: global navigation discovery,
HTML resource loading and interactive 3D previews. The host uses the official
AppBridge, not a replacement protocol. Larger bundled UI resources are supported by
setting the MCP SSE transport budget to 64 MiB instead of the SDK's 1 MiB default.

This implements global apps and their tools/resources, permissions, links and host
context. It does not claim every optional OpenAI extension capability (such as
file search or thread mentions). Remote server OAuth enrollment is not implemented;
connections currently accept an endpoint and optional bearer token.

Validation: 140 targeted backend/MCP checks, 34 isolation/deployment checks and
32 UI tests passed. Playwright exercised the stand-ins, Slack message and image
upload, persistent recording across navigation, and the official Bits & Bolts app.
A browser cross-frame access attempt from the external app to Recorder was denied.
The DigitalOcean rollout passed public health, TLS/CSP/noindex/API-isolation and
primary-runtime readiness checks. No business application deployment was changed.

Built-in app bundles now load from their own isolated sandbox origin under
`/assets/mcp-apps-v1/`, including relative module imports and stylesheets. Compiled
font and lazy-load URLs under `/assets/` are served there too. This removes CORS
and Workplace cookie dependencies from app startup. Only files inside the packaged
static assets directory are served; missing files and authenticated APIs return 404.
The dedicated namespace avoids reusing legacy cached response headers.

Regression coverage opens an app under a factory prefix, drops the session, and
verifies its module loads from the sandbox while account APIs stay inaccessible.
Playwright verified Recorder and Slack, including modules, styles and fonts.

Recorder presents a large Record/Pause/Resume control and an End button. Its timer
counts active capture only. Ending stops the microphone and flushes captured audio;
the user then accepts or edits a title defaulting to the local weekday and start
time (for example, `Friday, 7:58AM`). Saving applies that title to the session and
Shared filename before emitting the completed-upload event. Unfinished captures
remain recoverable after reloading. No model prompt is part of this flow.

## Launcher and personal navigation

The permanent Apps destination lists every enabled app entrypoint in a searchable
grid. Each tile opens the app and provides a Show/Hide in navigation menu. Hiding
an app only removes its rail entry; its launcher tile and deep links still work.
Admins can also change Show in navigation in Account → Apps, independently of
Enabled. Navigation preferences are persisted per account in each factory's store;
ordinary members may update their own preferences, never another person's. Enabled
state and app installation remain factory administration settings.

The dock and launcher also include available native destinations: Chat, Factory,
Shared, Library, Desktop, Coolify, and Memory. Their existing role, architecture,
and availability rules remain authoritative. Native destinations cannot be disabled
as services; Show in navigation only controls the current user's dock. All members
can open Manage apps to arrange their dock; administration controls remain admin-only.
Apps itself stays pinned, and Account remains a separate account utility.
