# OmniAgents Workspace Overview

This repository packages the OmniAgents framework for building, running, and evaluating OpenAI Agents projects. Everything starts from a `project.yml` manifest that declares where agents, tools, guardrails, server functions, and evaluation assets live. The CLI (`omniagents`) reads that manifest to scaffold, run, compile, and optimize agents.

## `project.yml`
- `name`: Human-friendly project identifier.
- `paths`: Absolute or relative directories for `agents`, `tools`, `guardrails`, `server_functions`, `evaluations`, and `target`. The loader resolves each path relative to the manifest location.
- `agents`: Declares the `entrypoint` agent key and optional include/exclude globs. Agents must follow the strict layout `agents/<agent_key>/agent.yml` and the YAML `name` must match the folder key.
- `tracing`, `settings`, and optional UI keys (`theme_color`, `layout`, `icon_map`) provide project-wide defaults injected into every agent spec.
- Additional sections (e.g., `evaluation`) are passed through unchanged for downstream tooling.

## Runtime Flow
1. `omniagents run -P project.yml` calls `omniagents.core.project.runtime.load_project`, which reads the manifest, discovers assets, and returns a `ProjectContext` with resolved paths and registries.
2. `build_spec_from_project` loads the entrypoint agent via `load_agent_spec_from_yaml`, merging project defaults, guarding against invalid layouts, and wiring discovered tools, guardrails, and server functions.
3. `omniagents.core.runtime.runner.Runner` wraps the resulting `AgentSpec` and dispatches to a backend (`cli`, `gui`/Flet, `server`, or `bubbletea` TUI) through `load_backend`.
4. If tracing is configured, `Runner` initializes it before handing control to the backend.

## Agent Specs (`agents/<key>/agent.yml`)
- Point to instructions via `instructions_file` or inline `instructions` (Markdown is typical). Scaffolding creates `instructions.md` alongside each agent YAML.
- Reference tools by name; discovery (`core.tools.discovery`) scans `tools/` and agent-local `tools/` folders for functions decorated with `@omniagents.function_tool` or `@omniagents.rich_function_tool`.
- Configure safety (`use_safe_agent`, `safe_agent_options`), guardrails, structured outputs, MCP servers, handoffs (`handoffs:` with nested agent refs), and UI copy (`label`, `description`, `welcome_text`).
- Remote agents can be proxied with the `remote_agent` block (WebSocket URL plus optional auth token env key).

## Tools, Guardrails, and Server Functions
- `tools/`: Python callables decorated with `@omniagents.function_tool`. Discovery records schema metadata for approvals and rich tool output wrappers.
- `guardrails/`: Modules exposing `@omniagents.input_guardrail` or `@omniagents.output_guardrail` functions; the loader groups them by direction.
- `server_functions/`: Functions decorated with `@omniagents.server_function`; discovery builds registrars so backends can expose custom RPC endpoints.

## Evaluation Assets
- `evaluations/`: Optional ecosystem for scenarios, judges, metrics, and optimizers. `core.project.compiler.compile_project` aggregates these into `target/evaluation_compiled.yml`, pulling in agent config, linking instruction files, and symlinking relevant tool directories.
- Scenarios support include/exclude selectors; judges can reference external YAML fragments via `agent_ref`.
- CLI helpers (`omniagents eval ...`, `omniagents compile`, `omniagents agent optimize`, `omniagents judge optimize`) operate on this structure.

## Scaffold and Utilities
- `omniagents/core/project/scaffold.py` backs `omniagents new`, `generate agent/tool/guardrail/server-function/judge`, enforcing the strict directory layout.
- Examples in `examples/` showcase complete projects wired with `project.yml` manifests, agent specs, and accompanying assets.
- Environment variables are resolved via `.env` files located by walking up from each YAML file.

## Key Modules
- `omniagents/cli.py`: Argument parsing, `.env` loading, and command dispatch.
- `omniagents/core/project/runtime.py`: Manifest parsing, asset discovery, and `ProjectContext` construction.
- `omniagents/core/config/loader.py`: Agent YAML parser to `AgentSpec`, including env substitution, structured outputs, and handoff/remote support.
- `omniagents/core/runtime/runner.py`: Backend orchestration and tracing bootstrap.
- `omniagents/core/project/compiler.py`: Evaluation compilation into `target/evaluation_compiled.yml`.

## Typical Workflow
- Create or clone a project with a `project.yml` manifest and strict `agents/<key>/agent.yml` layout.
- Implement or update tools, guardrails, server functions, and instructions referenced by the agent spec.
- Run `omniagents run -P project.yml` (or `--mode server/gui/tui`) to execute the agent.
- Use `omniagents compile` and evaluation subcommands to generate and run scenario suites, then iterate on instructions or tools.
