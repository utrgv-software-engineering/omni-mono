## MCP Servers Migration Brief

This document records the plan and current status for migrating OmniAgents from the legacy single-server MCP configuration (`uses_mcp`, `mcp_transport`, `mcp_url_setting_key`, etc.) to the new multi-server architecture driven by `omniagents/core/agents/mcp.py`. Treat this file as the authoritative context snapshot—if work stops unexpectedly, re-read this document to resume the migration immediately.

### Current Situation (2025-10-23)

- `AgentSpec` now exposes an `mcp_servers: list[MCPServerConfig]` field. `MCPServerConfig` is defined in `core/agents/specs.py`, enabling `core/agents/mcp.py` to instantiate servers without import errors.
- `core/config/loader.py` parses the new `mcp_servers` YAML schema, validates entries, and materializes `MCPServerConfig` objects. Legacy flags (`uses_mcp`, setting-key overrides) have been removed from `AgentSpec` and exporters; YAML authors now declare any needed setting fields explicitly.
- `_default_build_agent` builds every configured MCP server (via `build_mcp_servers`) and attaches the full list to constructed agents. `rpc/agents/local.py` now forwards any pre-built server list downstream so locally hosted runs keep multi-server state intact.
- The CLI backend connects all configured servers using an `AsyncExitStack` before entering the chat loop, ensuring proper lifecycle management for stdio/SSE transports.
- AgentService (FastAPI backend) now instantiates and manages MCP servers per run using an `AsyncExitStack`, closing them after each session completes.
- `tests/unit/core/test_mcp.py` covers the new YAML schema, placeholder substitution, and validation errors.
- The Flet backend now relies on spec-provided MCP server definitions; the legacy MCP settings UI surface and connection shim have been removed.
- Documentation, examples, and scaffolding still describe the legacy single-server fields and need to be refreshed.

### Target Architecture Recap

- Agent YAML files declare an `mcp_servers` list. Each entry includes a unique `name`, a `type` (`stdio`, `sse`, `http`, `streamable_http`, etc.), a `params` mapping (supporting `{setting: KEY}` placeholders), and optional `options` (tool filtering, caching, custom labels, structured content toggles).
- The loader resolves environment variables and placeholders, yielding `MCPServerConfig` objects stored on `AgentSpec.mcp_servers`.
- Runtime builders/backends call `build_mcp_servers(spec, settings)` to obtain live server instances and deliver the complete list to agents.
- Configuration signatures, caching, and reconnection logic rely on the normalized server list instead of ad-hoc single-server fields.

### Migration Checklist

- [x] Define `MCPServerConfig` and add `mcp_servers` to `AgentSpec`.
- [x] Parse the new YAML schema in `core/config/loader.py`, validating entries and emitting `MCPServerConfig` objects.
- [x] Update agent construction paths (`core/agents/builder.py`, `rpc/agents/local.py`, `core/agents/service.py`) so agents receive all configured servers.
- [x] Update runtime backends to the new model (CLI, FastAPI/server, Bubble Tea now share the multi-server flow).
- [x] Update supporting tooling (`core/project/export.py` emits the new schema).
- [x] Refresh unit tests (`tests/unit/core/test_mcp.py`) for the new schema and validation rules.
- [x] Remove legacy `uses_mcp`/setting-key fields from `AgentSpec`, loaders, exporters, and related tests.
- [x] Update the Flet backend to rely on YAML-supplied MCP servers (drop the MCP settings UI surface).
- [ ] Update documentation/examples/scaffolding to explain `mcp_servers` and remove references to legacy keys.
- [ ] Document breaking changes and draft upgrade notes for existing YAML files.

### Constraints & Scope

- Further UI polish (including Flet-specific enhancements) should wait until documentation updates land, to avoid churn for users referencing older guides.
- Prioritize small, reviewable changes: data model updates → loader/parser → builders/backends → docs/tests.
- Avoid shims that preserve legacy behavior; the goal is a clean cut-over.

### Immediate Next Steps

1. Refresh public-facing docs and examples to demonstrate the new `mcp_servers` syntax, providing clear upgrade guidance.
2. Document breaking changes and draft upgrade notes for existing YAML files.

Keep this file updated after each milestone so future work can pick up without additional context.
