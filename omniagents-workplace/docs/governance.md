# Classification and allowed services

## Data classification

Each factory has a data classification, using UTRGV's classes, with its purpose
and owner: **Factory settings → Data classification**. Classify by the data the
factory touches, not its purpose: a business-intelligence factory that queries
clinical data is Confidential.

| | Published | Controlled | Confidential |
| --- | --- | --- | --- |
| Models | Allowed services only (strict policy) | Allowed services only | Allowed services only |
| Integrations, MCP servers | Any configured | Allowed services only | Allowed services only |
| Definition changes | As configured | Always a reviewed pull request | Always a reviewed pull request |
| Pull requests on the factory's repository | A person's approval | A person's approval | A person's approval and a clean `personal-data` check |

Under the strict policy (`WORKPLACE_POLICY=strict`) a factory does not start
until it is classified. Every change is audited (`factory.profile_changed`) with
the profile before and after.

## Allowed services

The models, integrations and MCP servers the deployment allows. An
installation's administrators keep one list for all of its factories (**Allowed
services** on the Factories page); a standalone Workplace's administrators keep
its own. Every change is audited (`allowed_services.changed`).

| Kind | Provider or type | Endpoint |
| --- | --- | --- |
| Model | `azure`, `openai`, `openai-compatible`, `openai-oauth`, `litellm` | URL prefix; empty for the provider's own (OpenAI's API, ChatGPT sign-in) |
| Integration | `slack`, `github`, `linear`, … | Optional URL prefix of its API |
| MCP server | — | Optional URL prefix, or `command:<executable>` |

The runtime enforces the list, from what Workplace gives it when it starts:

- **Models** (`OMNIAGENTS_ALLOWED_MODEL_ENDPOINTS`): every model a run resolves
  passes through the model store, which refuses one not on the list. The Factory
  model page refuses them too. Under the strict policy an empty list allows no
  model, so add the deployment's models before turning the policy on.
- **Integrations** (`OMNIAGENTS_ALLOWED_INTEGRATIONS`, Controlled and
  Confidential factories): an integration not on the list does not start.
- **MCP servers** (Controlled and Confidential): the managed configuration
  overlay sets `security.mcp_require_allowlist` and `mcp_allowed_servers`. An
  entry with an endpoint pins the server's address, so a factory cannot give an
  outside server the name of an allowed one.

Changes reach a factory when its runtime next starts.

## The factory's own repository

On the repository a factory runs from, and the one self-improvement opens pull
requests on:

- **Only a person's approval counts.** Another agent's approving review does
  not satisfy branch protection; a person with a Workplace account must approve.
- **Confidential factories: the `personal-data` check.** A required check scans
  the lines a pull request adds for identifiers and credentials (SSNs, MRNs, API
  keys, bearer tokens) and fails if it finds any, so self-improvement cannot copy
  personal details into skills, prompts or configuration. Findings are audited
  (`factory.personal_data_found`) with their kinds, never their values.

Both apply to Workplace's stand-in GitHub. On real GitHub, set branch protection
to require a review and keep the factory's account out of the bypass list.

## Governance owner and decisions

Health Affairs governs its own AI risk. Installation administrators name the
**governance owner** (Factories page → Governance). The governance owner decides
technical governance directly, with a written reasoning, and Workplace records
every decision: what changed, who proposed it and why, who decided, their
reasoning, and when. Records are never edited once decided.

| Change | Under the strict policy |
| --- | --- |
| Raising a classification, or marking a factory heightened-risk | Immediate, by a factory administrator; recorded |
| Lowering either, or choosing anything below Confidential and heightened-risk at first | The governance owner's decision; anyone else's proposal waits for them |
| Allowed services | The governance owner's decision |
| A factory's models (the Factory model page) | The governance owner only |
| A pull request on the factory's repository that changes its models, the `security`, `integrations`, `approvals`, `mcp_servers` or `paths` sections of `project.yml`, or an agent's model, MCP or approval settings | Merges only once the governance owner approves it: a required `governance` check |

Under the open policy nothing waits, but decisions are still recorded. Every
factory needs an explicit classification and risk level before it starts under
the strict policy; neither has a default.

Each factory also records its **sponsor**, the clinical or business leader it is
built for, and its **intended use**: the data it touches, who is affected, and
what the AI does and does not decide.

## Showing the work

- **System card** (Factory settings → Governance): generated from the factory's
  settings, decisions and configuration; copy it into the AI Inventory.
- **AI Inventory export** (Factories page): every factory's card.
- **Scheduled reviews**: restore and kill-switch drills every quarter
  (administrators); the access review and each factory's risk review every year,
  the inventory update every 24 months, and heightened-risk reassessments every
  six months (governance owner). Overdue reviews are flagged and posted to the
  Teams channel once a day.
- **Quarterly report**: plain language, for Health Affairs leadership: decisions
  and their reasoning (lowered classifications called out), material changes,
  factories with their sponsors, kill-switch use, alerts, and reviews done and
  overdue.
- **Evidence export**: one JSON for a date range with the inventory, decisions,
  quarterly reports, change history, kill-switch events, reviews and the audit
  log. Metadata only, never conversation content or files. Generating a report
  or export is itself audited.
