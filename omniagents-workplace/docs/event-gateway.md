# Factory event gateway

A configured factory's Workplace owns event ingestion separately from its agent
runtime. GitHub, Linear, Slack, and memory stand-ins keep their provider behavior;
the factory app's webhook destinations point to `/factory/events/{provider}`.
Real GitHub continues using `/github/events`.

The gateway validates the provider signature, stores the original event in the
factory's `workplace.db`, commits it, then acknowledges acceptance. A background
worker sends pending events to the current runtime address, retrying failures
with capped exponential backoff. Delivery IDs are stable across retries; the
runtime's durable intake deduplicates them. An acknowledgment lost after runtime
acceptance can therefore cause another delivery without another intake record.
There is no new broker or shared cross-factory event store.

Slack Socket Mode connects from Workplace. Its envelope is acknowledged only
after the event is committed. The runtime accepts Slack over signed HTTP instead
of maintaining a competing Socket Mode connection. Signing timestamps are renewed
for retries; original event IDs and event timestamps remain unchanged. Linear's
delivery timestamp is also renewed, retaining its delivery ID and event content.

## Configuration and upgrades

The gateway is enabled by default for a configured factory app. Existing webhook
destinations are repointed when Workplace applies the factory configuration.
Other registered apps retain their own destinations and delivery behavior.
`event_gateway: {enabled: false}` disables forwarding and retains pending events.
The runtime environment includes `OMNIAGENTS_EVENT_GATEWAY=1`, a Slack signing
secret, and the source token used for channel-name resolution. This does not
change agent tools' Slack MCP credentials.

Upgrade the framework alongside Workplace. Workplace takes over Socket Mode only
when `/integrations/architecture` reports `event_gateway: true`; it remembers that
handoff across Workplace restarts and runtime outages. A reachable older runtime
reporting no gateway support releases that ownership. Do not roll a gateway-enabled
installation back to a framework that lacks this protocol and assume continuous
Slack ingestion during the transition.

For external Slack or Linear ingestion, factory configuration can name
operator-provided environment variables (values stay outside this file):

```yaml
event_gateway:
  slack:
    app_token_env: MY_SLACK_APP_TOKEN
    signing_secret_env: MY_SLACK_SIGNING_SECRET
    token_env: MY_SLACK_USER_TOKEN
    api_url: https://slack.com/api
  linear:
    signing_secret_env: MY_LINEAR_WEBHOOK_SECRET
    token_env: MY_LINEAR_ACCESS_TOKEN
    api_url: https://api.linear.app/graphql
```

Slack HTTP event delivery can use the gateway URL instead of Socket Mode; omit
`app_token_env` in that case. Configure the external provider to send to the
factory-scoped public gateway URL. Source configuration handles event ingestion;
agent MCP tool connections must still be configured for the intended services.
GitHub uses the existing connection settings and selected repositories.

## Failure handling and inspection

`GET /workplace/api/factory/events` is administrator-only and returns the pending
count and the first 100 pending records' IDs, attempt counts, and last errors.
It does not expose payloads or credentials. The SQLite `factory_events` table
retains delivery history. There is no automatic deletion of failed events.

A provider or source change, removed repository selection, revoked memory access,
or disabled gateway retains affected events without forwarding them to the new
source. Restoring matching configuration permits delivery again. Secret rotation
does not change an event's identity: the gateway signs each delivery with the
current credential. Factory state backups include the queue. A staging restore
removes the queue and remembered runtime handoff so it cannot replay production
work; production restore retains them.

The guarantee begins **after Workplace accepts the event**. If Workplace itself
is unavailable, delivery depends on the provider's retry/redelivery behavior.
GitHub does not automatically redeliver failed webhooks. The gateway does not
provide exactly-once external side effects or transactional ordering across
providers. Chat submissions and scheduled agent runs are separate from this
provider-event queue; chat may still briefly disconnect during runtime replacement.
