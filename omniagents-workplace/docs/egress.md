# Egress

An agent that reads sensitive data and can reach any host can be led to send it
somewhere. Where the deployment runs the egress proxy, everything a factory runs
(its runtime, its sandboxes, CI jobs, previews) reaches the internet only
through it, and only where an allowlist says.

## The proxy

`omniagents-workplace egress-proxy --config <file> --denials <file> --port 3128`
is an allowlisting forward proxy: HTTPS through `CONNECT`, plain HTTP by
absolute URL. Each factory authenticates with its own credentials, so the proxy
applies that factory's list. It reads the configuration Workplace writes, and
rereads it when it changes. It records only destinations, never requests.

Set on Workplace (the installation):

| Variable | Is |
| --- | --- |
| `WORKPLACE_EGRESS_PROXY` | The proxy's URL as runtimes and sandboxes reach it, e.g. `http://egress:3128` |
| `WORKPLACE_EGRESS_CONFIG` | The configuration file Workplace writes and the proxy reads |
| `WORKPLACE_EGRESS_DENIALS` | The file the proxy appends denials to, for Workplace to audit |

Without them nothing changes (local installations, and 80/20 until it turns
them on).

## Allowlists

- **The deployment's** (Factories page → Allowed services → Egress allowlist):
  where every factory may connect: model endpoints, package registries, the
  integrations factories need.
- **A factory's own** (Factory settings → Egress allowlist): a Confidential
  factory's narrower list, within the deployment's.

An entry is a host, `*.domain` for any subdomain, or either with `:port`;
without a port, 80 and 443. Narrowing takes effect at once. Widening either list
is a material change: under the strict policy, the governance owner's decision.
Changes are audited (`egress.deployment_changed`, `egress.factory_changed`).

## What factories are given

- **Runtimes**: `HTTP_PROXY`/`HTTPS_PROXY` with the factory's credentials, and
  `NO_PROXY` for Workplace itself.
- **QuickSand sandboxes**: `network_mode: mounts_only`, which leaves the guest no
  route out, with the proxy forwarded in at `10.0.2.101:3128` and the proxy
  variables set for every command.
- **Docker sandboxes**: the proxy variables; their network must be internal.

## Denials

Every refused connection is audited on its factory (`egress.denied`, with the
destination), and a Confidential factory's is an alert, posted to the Teams
channel.

## What the deployment adds

The proxy decides where; the network makes it the only way out. The deployment
runs the proxy as its own container, puts factory runtimes, CI jobs and previews
on internal Docker networks, and drops any container traffic to the internet
that does not come from the proxy (the host firewall's `DOCKER-USER` chain). On
Azure, network security group rules allow the VM out only on HTTPS.
