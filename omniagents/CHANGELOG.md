# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
