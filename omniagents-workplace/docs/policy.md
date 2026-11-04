# Deployment policy, audit log and notifications

## Policy

`WORKPLACE_POLICY` says what the deployment requires. Whoever deploys Workplace
sets it, for the installation and every factory in it; a factory cannot.

| | `open` (default) | `strict` |
| --- | --- | --- |
| For | Local installations, development | Deployments with sensitive data, and the 80/20 deployment that dogfoods them |
| Sessions | As configured | End after **30 minutes** without activity and **12 hours** in total |
| Password sign-in | On | Off when Azure Easy Auth is on: Microsoft sign-in only |
| Microsoft identity | Checked at sign-in | With Easy Auth, checked on every request: a session whose Microsoft identity is gone, or different, ends at once |
| Factory runtimes | As their project says | Keep the runtime's audit (`security.audit`), with no tool input or output in it, through a managed configuration overlay the project cannot override |

Later rules (data classification, allowed services, forced review) read the
same policy.

The managed overlay reaches runtimes the installation starts. An adopted
runtime container (one Compose runs) needs `OMNIAGENTS_PROJECT_OVERLAYS` set in
its own environment to the same overlay.

## Audit log

Security and settings events are kept in `audit_events`: in each factory's
`workplace.db`, and in the installation's `installation.db` for events about the
installation. The database refuses to update or delete them. Each event is also
logged to stdout as JSON, for the deployment's log store (Log Analytics on
Azure), where its retention is set: six years on Azure.

Events carry identifiers, outcomes and reasons; never passwords, cookies,
tokens, or conversation content. They include sign-ins and failures, password
and account changes, invitations, personal tokens, membership changes (before
and after), kill-switch use, and access-review exports.

## Access review

UTS 165 asks for access to be reviewed every 12 months. Installation
administrators export everyone with access, by factory, with each person's role
and when it was granted: `GET /workplace/api/installation/access-review`. A
standalone Workplace's administrators export its people:
`GET /workplace/api/access-review`. Each export is audited.

## Notifications

Workplace sends no email. Set `WORKPLACE_TEAMS_WEBHOOK_URL` to a Microsoft
Teams workflow's "when a webhook request is received" URL, and approval
requests and alerts post to that channel as Adaptive Cards. Without it they are
only logged. A failed post never blocks what it reports.
