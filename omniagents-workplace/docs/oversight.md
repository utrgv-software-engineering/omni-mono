# Transparency and oversight

## AI is never presented as human

- **Accounts.** The Slack and GitHub accounts Workplace creates for a factory
  are named as AI ("Acme Factory (AI)"), and the Slack profile's title says "AI
  agent". Linear posts as an app.
- **Commits.** Every commit made in a factory's sandboxes is by the factory's
  account under that name (`GIT_AUTHOR_*` and `GIT_COMMITTER_*`, from
  `FACTORY_AI_NAME`). Workplace does not add a commit trailer: that would mean
  replacing the repositories' own git hooks.

## Metrics

The Factory tab shows, beside spend, cost and time to ship:

| Figure | Is |
| --- | --- |
| Error rate | Runs that failed, of the runs that finished |
| Tool failures | Tool calls that failed (the agents SDK's tool-error result), of all tool calls |
| Uptime | How often the runtime answered Workplace's checks, every few seconds; a stopped factory's runtime is not counted |

Accuracy is the judges' pass rate on live runs, already on the tab. That
Workplace itself is down cannot be shown here: on Azure, App Service's health
check alerts on it.

## Alerts

Incidents are audit events (`alert.<kind>`, with their cause) and are posted to
the Teams channel:

| Kind | When |
| --- | --- |
| `kill_switch` | A factory, or every factory, is stopped |
| `egress` | A Confidential factory's runtime or sandbox tried to reach a destination not on its allowlist |
| `sign_in_failures` | An account or a source is locked out after repeated failed sign-ins |
| `personal_data` | A pull request on a Confidential factory's repository adds personal data or credentials |

The quarterly report and the evidence export list them.

## Adversarial tests

omni-factory's `scenarios/adversarial.yml` tests prompt injection hidden in an
issue, a request to copy personal data into the repository, and a request to
send data to an outside host. They run on every pull request to the factory, as
required checks.
