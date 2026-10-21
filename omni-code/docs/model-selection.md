# Workspace model selection

Omni Code uses one provider for the whole application workspace. A conversation
can choose any text model from that provider. Workers, exploration, approval
reviews, completion reviews, recaps, and context compaction inherit that choice.
Hosted voice uses a compatible realtime model from the same provider; local
speech remains available independently.

In Ink, open `/model`, press `p` for providers, select a provider with the arrow
keys and Enter, then press `y` to confirm (`n` or Escape cancels). Ink returns
to the refreshed model picker, where you can select another model from that
provider. This changes the workspace provider for all projects and agents.

You can also change the workspace provider in desktop AI Settings or with
`omni model default <provider/model>`. Both require confirmation that continuing
conversations sends their history to the new provider. For scripts, pass
`--confirm-provider-change` explicitly. Every CLI provider change requires its
own confirmation, including `model add --default` and setup.

Local desktop, local browser server, and CLI share the product's `models.json`
(normally `~/.config/omni_code/models.json`). Desktop settings seed a missing file
for migration but never overwrite an existing file at startup or on unrelated
settings saves. Both desktop and CLI replace the file atomically when saving.
Cloud retains its tenant-scoped settings storage and switching lifecycle.

Local active runs keep a snapshot of their starting model/provider configuration,
including helper calls and newly spawned child work. Subsequent runs resolve the
current configuration; existing conversations then use the new provider's default
model. A missing model or failed provider connection
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
