# Workspace model selection

Omni Code uses one provider for the whole application workspace. A conversation
can choose any text model from that provider. Workers, exploration, approval
reviews, completion reviews, recaps, and context compaction inherit that choice.
Hosted voice uses a compatible realtime model from the same provider; local
speech remains available independently.

Change the workspace provider in the desktop AI Settings. The confirmation
explains that continuing conversations sends their history to the new provider.
Active runs stop before settings change. Existing conversations then use the
new provider's default model. A missing model or failed provider connection
never selects a different provider automatically.

Credentials, credential references, endpoints, API versions, and transport
options belong to provider entries. Model entries cannot override them. Old
model-level connection fields cause a validation error. Settings offers an
explicit migration to provider connections; review the destination first, or
configure a separate provider to retain a distinct account/endpoint. No secret
values appear in validation errors.

`single_provider: true` in `omni_code/models.yml` enables the framework contract.
The user config remains version 3. `provider_scope_version: 1` records the
explicit workspace-wide confirmation, allowing old conversations from another
provider to adopt the selected provider on their next run.

LiteLLM connections require a provider-level `routing_prefix` (for example,
`anthropic`). Claude setup supplies it automatically. For existing LiteLLM
connections, set Routing prefix under Advanced before continuing. Every model
must use that prefix; credentials and endpoints are passed directly to the
adapter rather than installed as process-wide environment overrides.

Release the matching omniagents changes first, update this package's pinned
omniagents dependency to that release, and then ship the desktop against the
updated Omni Code runtime. The desktop and runtime validation must ship together.
