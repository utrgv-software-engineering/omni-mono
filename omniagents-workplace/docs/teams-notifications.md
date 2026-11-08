# Factory notifications to Teams

This sends Adaptive Cards to one internal Teams channel through a Teams Workflow. It is separate from opening Workplace inside Teams: no Teams app upload, bot, Graph integration, or new Azure resource is needed.

## Configure the Workflow

1. In Teams Workflows, use **When a Teams webhook request is received** and configure an action to post the incoming Adaptive Card to the desired channel. A template for posting webhook alerts is suitable if it accepts the standard `type: message` envelope with an `attachments` array.
2. This sender supports the webhook URL's shared-secret authentication. A tenant-restricted trigger requiring an Entra bearer token is not supported by this integration. Treat an anonymous/shared-secret trigger URL as a credential; do not put it in chat, agent prompts, source files, or logs. Use the organization's permitted trigger settings.
3. Save the URL as `TEAMS_WEBHOOK_URL` in the existing private deployment environment stored in Key Vault (`azure-env`). The Azure Compose overlay already maps this to `WORKPLACE_TEAMS_WEBHOOK_URL`. Restart through the existing deployment procedure after changing it. No new secret editor is added to Workplace.
4. Allow the URL's exact hostname in deployment egress and, when restricted, the factory allowlist. The sender uses Workplace's existing authenticated factory proxy. Do not open direct sandbox egress or add broad Microsoft wildcard domains.
5. Deploy the updated Workplace and factory runtime together. The factory's base agent enables the `teams` capability. The runtime receives only a protected Workplace notification endpoint and its existing factory token, not the Teams webhook URL.

## Automatic attention notifications

With a webhook configured, Workplace checks the private runtime every 15 seconds for:

- Pending human tool approvals, linked to `/factory#approvals`.
- Pending `escalate` and `ask_user`/elicitation requests in shared automation runs, linked to the run and its question's session.
- Failed shared automation runs, linked to `/factory/runs/<id>`. Successful, intentionally stopped, and interrupted runs are not announced. Private chats, Slack DMs and standalone private handoffs are excluded from this new run/question feed.

Failure events are stored in completion order, so a run that started before the last check and fails later is not missed. The first enabled poll establishes a baseline rather than announcing historical failures. Subsequent events survive runtime and Workplace restarts. Pending questions are checked against live waiters; resolved, expired, and cancelled questions are not newly announced.

Automatic cards contain generic attention text and a Workplace link, not question contents, raw exceptions, or conversation transcripts. Existing security and stop/resume notifications remain unchanged.

## Answer inside Workplace

The run viewer keeps its transcript read-only. An administrator can answer an outstanding escalation using a dedicated answer field, or respond to an existing elicitation card. These use `client_response` and `elicitation_response`; they do not enable a general message composer or transcript editing.

The run-scoped WebSocket requires an authenticated administrator and a trusted origin. Answers are intercepted by Workplace, then checked by the private runtime against the live question's run, agent, session and request ID using the control token. Other mutation RPCs remain blocked. Resolved/expired questions cannot be answered again. The existing runtime consumes the response and continues the waiting tool. Workplace audits the administrator and run without logging the answer.

## Call from an automation

Existing automations remain agent instructions in `automations/<name>/automation.md`. Add an instruction to a chosen automation after its normal work, for example:

```text
After completing the requested review, call teams_notify once:
- event_id: the source event's stable ID, prefixed with this automation's name
- title: Review ready
- text: a short non-sensitive summary

No internal page path is needed. Reuse the identical event ID and content
when checking/retrying.
Do not post confidential content or secrets into the Teams channel.
```

The `teams_notify(event_id, title, text)` tool calls `POST /factory/notifications/teams`. This endpoint accepts only the configured factory app's bearer token; ordinary browser sessions and other apps' tokens cannot publish. The tool remains subject to the factory's normal approval policy. It does not accept internal paths, arbitrary webhook URLs or card JSON. No new automation schedules are created.

Use explicit calls for meaningful review handoffs (including nightly improvement proposals) and questions posted to a shared originating thread instead of held by a waiting client tool. Do not infer review readiness from a successful run or a newly created PR. Do not mirror every Slack message or send "nothing changed" summaries. Do not duplicate automatic approval, pending-question, or failure notifications. Use an item/revision-based ID, for example `review:org/repo:42:<head-sha>`.

Agent tool calls send cards without an **Open in Workplace** button. Application-generated event notifications can supply their known `path`, such as `/factory/runs/9` or `/org/repo/pull/42`; agents do not invent these routes. This API field is optional. When supplied, it is validated: external URLs, API/auth paths, path traversal, and credential query parameters are rejected. The sender builds the button using the configured public Workplace URL. Opening the link still requires ordinary Workplace authorization.

## Delivery behavior

- `accepted`: the Workflow returned a successful HTTP response. This is not proof that its downstream channel-posting action succeeded; check Workflow run history if the card is missing.
- `failed`: an explicit rejection or connection failure. The same event ID and identical payload may be retried after correcting the problem.
- `unknown`: the request may have reached the Workflow, but its result was not received. Check Workflow history before resending. The same ID does not automatically resend.
- `sending`: another request reserved this event, or a process stopped during delivery. It is not automatically resent. Check the Workflow and the persisted delivery record before intervening.

Successful and uncertain event records persist in the existing Workplace database, keyed by factory, webhook destination, and event ID. Concurrent calls reserve the event atomically. Reusing an ID with changed content returns HTTP 409. No message bodies or webhook credentials are persisted in delivery records; audit entries contain a hashed notification identifier and status.

The sender attempts at most three deliveries for connection-establishment errors, HTTP 429, or gateway/service-unavailable responses. It honors short numeric `Retry-After` values; longer or date-form values stop the automatic retry loop. Read/write timeout outcomes are treated as uncertain rather than automatically retried. Redirects are not followed. Explicit tool calls require a caller retry; the automatic attention worker retries failed deliveries on later polls. There is no exactly-once delivery guarantee: a gateway failure can still mask a successful downstream delivery. Do not bypass duplicate protection by generating a fresh ID for the same event.

## Verification

Built-in approval and attention notifications use the same persistent delivery ledger as explicit calls. Failed automatic notifications are retried on later polls; accepted and uncertain outcomes are not resent. Failed approval sends are not marked announced. Retrying after an ambiguous downstream error still cannot guarantee exactly-once channel delivery. No webhook secret is sent to the runtime or browser.

Automated tests mock the Workflow and cover factory authentication, payload/link validation, proxy selection, card buttons, bounded retries, secret-safe logs, concurrent reservations and persisted duplicate records. Before enabling an automation, send one non-sensitive test event to the real Workflow and confirm the card and browser link work. Workflow/channel permissions are controlled by Microsoft 365, not Workplace.
