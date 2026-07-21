# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Builtin agents: the framework now ships stock sub-agent specs under `omniagents/builtin/agents/<key>/agent.yml`, merged into every project's agent registry after project agent discovery — mirroring builtin-capability precedence (a project `agents/<key>/` definition shadows the builtin by name; `agents.include`/`exclude` globs apply, so `exclude: ["explorer"]` opts out). First builtin agent: `explorer`, a read-only workspace exploration sub-agent (capabilities `filesystem_readonly` + `shell_readonly` + `notebook_readonly`, low reasoning effort, parallel tool calls, headless), lifted from omni-code and domain-neutralized. It declares no model or `model_config_resolver` — it inherits the orchestrator's resolved model — so products only shadow it when they want different model routing.
- Builtin `exploration` capability pack: exposes the builtin `explorer` as an `explore` agent tool (`max_turns: 50`, no approval) with delegation guidance — use `explore` for broad workspace questions, re-read key files after it returns. Products get the pack by listing `capabilities: [exploration]` on an agent; shadowing `explorer` re-points the pack's tool at the product's own definition.
- Builtin `workspace_info` capability pack (`fs_get_cwd`, `fs_get_workspace_root`, `fs_get_home`, `fs_list_dir`, `fs_get_sources`), lifted from omni-code. Fixes an inverted dependency: the web backend's workspace picker feature-detects these names (`App.tsx` / `WorkspacePicker.tsx`) but only omni-code defined them — the contract and implementation now live together. `fs_list_dir` is sandbox-aware (browses the container filesystem via `SandboxWorkspace`, clamped to the manifest root) when an embedder installs providers via the new `omniagents.core.runtime.workspace_info` seams: `set_workspace_sources_provider` (defaults to no sources), `set_manifest_root_provider`, and `set_sandbox_session_provider` — standalone servers fall back to the host filesystem.
- Builtin `terminal` capability pack (`terminal.create` / `terminal.close`), lifted from omni-code — the server-function half of the terminal contract the framework already shipped (`TerminalManager`, `/ws/terminal`, PTY backends). Dual binding modes: a sandboxing embedder installs a configured `TerminalManager` on the service (`service.terminal_manager`) whose defaults win; standalone servers lazily fall back to a host-pty manager with cwd resolved from the session context. `attach_terminal_manager` / `terminal_supported` helpers exported from `omniagents.builtin.server_functions.terminal`.
- Note: omni-code's `session_ensure` server function is intentionally NOT lifted — it only proxied the framework's own builtin `session.ensure` (registered by `AgentService`), which clients (including the web UI, which never calls `session_ensure`) should use directly. The omni-code shim dies downstream.
- Shared instruction fragments: agent instruction templates (Jinja2) now support `{% include %}`. The render environment gets a `ChoiceLoader` over ordered search paths — the instructions file's own directory, its `_shared/` subdirectory, the sibling `_shared/` directory (`agents/_shared/` in the strict layout), plus optional `paths.instruction_fragments` dirs from project.yml. Wired through `AgentSpec.instructions_search_paths` into the agent builder and the realtime (voice) session, so text and voice agents can share verbatim instruction blocks instead of drifting copies. Missing includes raise `InstructionIncludeNotFoundError` naming the searched directories; templates without includes render byte-identically to before (regression-tested).
- Generic pipx self-updater (`omniagents.core.updater`), lifted from omni-code: products describe themselves with an `UpdaterConfig` (simple-index URL, pipx package name, cache dir, env-var prefix, plus optional update-command hint and pip extra-index override) and reuse `check_for_update` (PEP-503 simple-index version scrape), `maybe_print_update_notice` (throttled `update.json`-cached startup notice with failure-retry backoff, honoring `<PREFIX>_UPDATE_CHECK` and `<PREFIX>_UPDATE_CHECK_INTERVAL_SECONDS`), and `run_update_command(argv, config=..., prog=...)` — the check/confirm/`pipx upgrade` CLI flow, parameterized by program name like `run_codex_auth_command` / `run_model_command`.


## [0.7.19] - 2026-07-18

### Added
- Builtin `/compact` server function + `compact` capability pack, completing the recap symmetry: checkpoint compaction now ships a summarizer seam (`set_compaction_summarizer` / `compaction_summarizer` in `omniagents.core.runtime.compaction`) and a ready-made summarizer system prompt (`DEFAULT_CHECKPOINT_SUMMARY_INSTRUCTIONS`, lifted from omni-code's compactor agent and domain-neutralized). Products install a summarizer (their compactor agent + model resolution) and activate the pack instead of shipping their own `/compact`.
- Generic tool-usage guidance folded into the builtin capability packs from omni-code: `filesystem` now carries the `apply_patch`-exact-name and don't-re-read-after-successful-edit bullets; `shell` now carries the prefer-`rg`/`rg --files`-over-`cat`/`grep`/`find` discovery guidance (matching `shell_readonly`).
- `ContainerRoutingMixin` (`omniagents.core.eval.container_routing`): bolt "environment in container" onto any eval environment — the agent runs host-side while its shell/file tools exec inside a sandbox-profile-described container (SDK SandboxAgent path), with in-container `run_command`/verify primitives and host fallthrough for scenarios that don't request a container. Carries no default image, profile, or resolver: the composing product supplies `profile_resolver` (and optionally `default_profile_path`). Lifted from omni-code's devbox eval environment.
- project.yml overlays: `load_project` now layers overlay documents over the base manifest — `<product-config>/project.d/*.yml` (lexical order), then `OMNIAGENTS_PROJECT_OVERLAYS` (os.pathsep-separated paths), then an explicit `overlays=` parameter — with the framework's one layered-config merge rule (dicts merge, lists/scalars replace), now shared as `omniagents.core.utils.merge.deep_merge` between sandbox profiles and project overlays. Enables read-only packaged base manifests with operator/enterprise overlays instead of copy-the-file-and-edit.
- `ModelConfigStore` (`omniagents.core.models`): generic per-user models.json registry with layered merge (product defaults → governance → user config), ${VAR} expansion, ref parsing/CRUD, OAuth model discovery, and `resolve_model_for_runtime` shaped for `build_model_provider`. Products install their store process-wide with `set_model_store`.
- Builtin capability-gated server functions lifted from omni-code: `model_picker` pack (/models /model /reasoning /voice-models /voice-model over the installed model store), `feedback` pack (/good /bad /note /notes into Studio tracing), and `/artifact` on the `artifacts` pack. Session helpers `session_workspace_root` / `ensure_session_with_workspace` added to `core.server_functions.helpers`.
- `LocalWorkspaceEnvironment` (`omniagents.core.eval.local_workspace`): disposable temp-workspace eval environment — gitignore-respecting fixture copy, inline AGENTS.md/skills staging, git init, workspace process reaping, keep-tempdir diagnostics.
- Layered sandbox-profile resolver (`omniagents.core.sandbox.profiles`): packaged default ← user default ← per-project ← explicit merge with ${workspace_dir} substitution, network-allowlist / user-.env injection, and the init/services/terminal extension.
- Builtin `convert_to_markdown` tool + `markdown` capability pack (MarkItDown-backed rich-format → Markdown extraction, sandbox-aware host-bridge), lifted from omni-code. MarkItDown is an optional extra: `omniagents[markdown]` (included in `[all]`).
- Ripgrep fast path for the builtin discovery tools: `glob_files` and `grep_files` enumerate/search with a single `rg` exec when ripgrep is available in the active workspace (one workspace call instead of an RPC per directory/file on sandboxed backends), falling back to the pure walk otherwise. Products no longer need to exclude these tools for performance.
- `WorktreeIsolation` ships as the third builtin worker isolation strategy (`worktree`): a git worktree on a `worker/<parent>-worker-<id>` branch under the framework scratch area, stateless (cleanup driven by WorkerWorkspace metadata), lifted from omni-code. `is_git_repo` / `is_git_dirty` helpers exported from `omniagents.core.workers`.
- `make_auto_compact_token_hook` — the proactive half of `make_auto_compact_error_hook`: a `token` runtime hook that checkpoint-compacts when input-token utilization crosses a threshold (default 0.9), with the same state-key flags and opt-outs.
- `EvalEnvironment.run_command` (host-subprocess default) and a concrete filesystem `verify` (exists/contains/not_contains) parameterized by overridable `_path_exists`/`_read_file` primitives — container environments override just the primitives; measures stay environment-agnostic.
- New assertion primitives in `omniagents.core.evaluation`: `assert_tool_arg_matches` (pattern/regex/predicate scan over tool-call arguments, with `negate` for forbidden-action measures) and `assert_sql_rows` / `assert_sql_no_rows` (read-only sqlite checks against the environment-staged `db_path`).
- `run_codex_auth_command(command, prog=...)` — public codex auth CLI flow, parameterized by program name so products reuse it instead of copying the login/logout/status branches.
- Builtin `notebook` / `notebook_readonly` capability packs with `read_notebook` and `edit_notebook` tools (cell-level Jupyter editing with diff-based approval metadata), lifted from omni-code.
- Builtin `filesystem_readonly` and `shell_readonly` capability packs — read-only surfaces for research/explorer subagents.
- Builtin `slash` capability pack with a `dispatch` tool that executes commands from the new process-wide default `SlashCommandRegistry` (`register_slash_command` / `default_slash_registry` in `omniagents.core.tools`).
- Checkpoint compaction is a first-class concept in `omniagents.core.runtime.compaction`: `run_checkpoint_compaction` (window since last `context_summary` marker, shrink-on-context-error retry via `summarize_with_window_shrink`, resume-protocol summary item with a default envelope), plus `make_auto_compact_error_hook` for reactive on-context-error compaction. Products supply only the summarizer (their compactor agent + model resolution). The session manager's marker windowing now delegates here — the marker is the checkpoint contract, not a legacy artifact.
- `PlatformCredentialProvider` reworked around its real caller: sync core with async wrappers, `get_credential_by_ref_sync` resolving policy `{session_id}` refs, uniform `None`-on-failure contract (was ad-hoc error dicts), and `ensure_platform_session` for local (launcher-less) session bootstrap, agent-parameterized.
- `set_platform_credentials_loader`: platform bootstrap no longer hardcodes any product config path — products install a loader for their stored credentials; without one, detection is env-var-only.
- `omniagents.testing`: reusable pytest fixtures (`invoke_tool`, `test_event_loop`, autouse permissive workspace) for tool test suites — import them into a `conftest.py` to keep directory scoping.
- `instructions_append:` on `extends:` capability overrides — append product guidance to the base pack's instructions instead of copying the whole text to add a line (mutually exclusive with `instructions:`).
- `IdleTrigger` / `IdleTriggerScheduler` in `omniagents.core.autonomy.idle`: fire product actions (e.g. an auto-recap) when a session goes idle — idle-threshold, min-turns, and never-twice-in-a-row rules generalized from omni-code's recap watcher.
- Version management system with `_version.py`
- CLI `--version` flag support
- Automated versioning workflow with bump2version

## [## [Unreleased]] - 2025-10-22

### Fixed
- Ensure Studio dark mode uses shared tokens so text stays legible across components.
- Align the Logs page header layout with other Studio sections while preserving project controls.

## [## [Unreleased]] - 2025-10-15

### Fixed
- Align OmniAgents Studio websocket URLs, dev defaults, and CORS with GitHub Codespaces port forwarding.

## [0.3.14] - 2025-10-13

### Changed
- Bundle OmniAgents Studio production assets from `omniagents.studio.server` so they ship in wheels and sdists.

### Fixed
- Ensure `omniagents studio serve` serves the Studio UI without requiring the `--dev` flag.

## [## [Unreleased]] - 2025-10-13

### Fixed
- Fail evaluation suites when agent runs terminate with errors by surfacing them as `_agent_error` measures.

## [## [Unreleased]] - 2025-01-26

### Added
- Core refactoring of backends architecture
- Test organization improvements (unit/integration/e2e structure)
- Image artifact support
- Flet backend migration

### Changed
- Major code cleanup and refactoring
- Improved separation of CLI and GUI modes
- Better code organization and reduced duplication

### Fixed
- Various bug fixes and stability improvements

## [0.1.2] - 2025-01-20

### Added
- Multi-backend support (CLI, GUI, Server)
- Remote agent protocol via WebSocket JSON-RPC
- Safe agent mode with tool approval
- Session persistence in SQLite
- YAML-based agent configuration

### Changed
- Improved error handling
- Better documentation

## [0.1.1] - 2025-01-15

### Added
- Basic agent wrapper functionality
- Tool discovery system
- Python and YAML agent definitions

## [0.1.0] - 2025-01-10

### Added
- Initial release
- Basic OpenAI agents wrapper
- CLI interface
- Flet-based GUI

[Unreleased]: https://github.com/yourusername/omniagents/compare/v## [Unreleased]...HEAD
[## [Unreleased]]: https://github.com/yourusername/omniagents/compare/v## [Unreleased]...v## [Unreleased]
[## [Unreleased]]: https://github.com/yourusername/omniagents/compare/v0.3.14...v## [Unreleased]
[0.3.14]: https://github.com/yourusername/omniagents/compare/v0.3.13...v0.3.14
[## [Unreleased]]: https://github.com/yourusername/omniagents/compare/v0.1.2...v## [Unreleased]
[0.1.2]: https://github.com/yourusername/omniagents/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/yourusername/omniagents/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/yourusername/omniagents/releases/tag/v0.1.0
