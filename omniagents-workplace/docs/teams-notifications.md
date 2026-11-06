# Factory notifications to Teams

This sends Adaptive Cards to one internal Teams channel through a Teams Workflow. It is separate from opening Workplace inside Teams: no Teams app upload, bot, Graph integration, or new Azure resource is needed.

## Configure the Workflow

1. In Teams Workflows, use **When a Teams webhook request is received** and configure an action to post the incoming Adaptive Card to the desired channel. A template for posting webhook alerts is suitable if it accepts the standard `type: message` envelope with an `attachments` array.
2. This sender supports the webhook URL's shared-secret authentication. A tenant-restricted trigger requiring an Entra bearer token is not supported by this integration. Treat an anonymous/shared-secret trigger URL as a credential; do not put it in chat, agent prompts, source files, or logs. Use the organization's permitted trigger settings.
3. Save the URL as `TEAMS_WEBHOOK_URL` in the existing private deployment environment stored in Key Vault (`azure-env`). The Azure Compose overlay already maps this to `WORKPLACE_TEAMS_WEBHOOK_URL`. Restart through the existing deployment procedure after changing it. No new secret editor is added to Workplace.
4. Allow the URL's exact hostname in deployment egress and, when restricted, the factory allowlist. The sender uses Workplace's existing authenticated factory proxy. Do not open direct sandbox egress or add broad Microsoft wildcard domains.
5. Deploy the updated Workplace and factory runtime together. The factory's base agent enables the `teams` capability. The runtime receives only a protected Workplace notification endpoint and its existing factory token, not the Teams webhook URL.

## Call from an automation

Existing automations remain agent instructions in `automations/<name>/automation.md`. Add an instruction to a chosen automation after its normal work, for example:

```text
After completing the requested review, call teams_notify once:
- event_id: the source event's stable ID, prefixed with this automation's name
- title: Review ready
- text: a short non-sensitive summary
- path: the actual Workplace page path for the reviewed item

Use a returned Workplace URL to obtain the page path; do not invent an item URL.
If no item link is available, use /automations/pr-opened and identify it as the
automation page. Reuse the identical event ID and content when checking/retrying.
Do not post confidential content or secrets into the Teams channel.
```

The `teams_notify(event_id, title, text, path)` tool calls `POST /factory/notifications/teams`. This endpoint accepts only the configured factory app's bearer token; ordinary browser sessions and other apps' tokens cannot publish. The tool remains subject to the factory's normal approval policy. It does not accept arbitrary webhook URLs or card JSON. No new schedules or event triggers are enabled automatically.

`path` is a Workplace-relative page path such as `/automations/pr-opened`, `/library/<id>`, or the real `/apps/...` page for an event. External URLs, API/auth paths, path traversal, and credential query parameters are rejected. The sender builds an **Open in Workplace** button using the configured public Workplace URL. Opening the link still requires ordinary Workplace authorization.

## Delivery behavior

- `accepted`: the Workflow returned a successful HTTP response. This is not proof that its downstream channel-posting action succeeded; check Workflow run history if the card is missing.
- `failed`: an explicit rejection or connection failure. The same event ID and identical payload may be retried after correcting the problem.
- `unknown`: the request may have reached the Workflow, but its result was not received. Check Workflow history before resending. The same ID does not automatically resend.
- `sending`: another request reserved this event, or a process stopped during delivery. It is not automatically resent. Check the Workflow and the persisted delivery record before intervening.

Successful and uncertain event records persist in the existing Workplace database, keyed by factory, webhook destination, and event ID. Concurrent calls reserve the event atomically. Reusing an ID with changed content returns HTTP 409. No message bodies or webhook credentials are persisted in delivery records; audit entries contain a hashed notification identifier and status.

The sender attempts at most three deliveries for connection-establishment errors, HTTP 429, or gateway/service-unavailable responses. It honors short numeric `Retry-After` values; longer or date-form values stop the automatic retry loop. Read/write timeout outcomes are treated as uncertain rather than automatically retried. Redirects are not followed. There is no durable retry worker or exactly-once delivery guarantee: a gateway failure can still mask a successful downstream delivery. Do not bypass duplicate protection by generating a fresh ID for the same event.

## Verification

Automated tests mock the Workflow and cover factory authentication, payload/link validation, proxy selection, card buttons, bounded retries, secret-safe logs, concurrent reservations and persisted duplicate records. Before enabling an automation, send one non-sensitive test event to the real Workflow and confirm the card and browser link work. Workflow/channel permissions are controlled by Microsoft 365, not Workplace.
