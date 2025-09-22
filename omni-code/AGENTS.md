# AGENTS.md — Omni Code Repository Guidelines for AI Agents

## Purpose
- Provide clear, concrete guidance for AI agents working in this repository.
- Capture repo conventions, tooling, and expectations so changes integrate cleanly and tests pass.

## Scope
- This file lives at the repo root and applies to the entire repository unless a more deeply nested AGENTS.md overrides some guidance.

## Precedence
- System/developer/user instructions in an active prompt take precedence over AGENTS.md.
- When multiple AGENTS.md files exist, the one closest to the file being changed takes precedence over parent directories.
- Follow existing code patterns and tests in this repo over generic preferences.

## Repository Overview
- Language: Python 3.10+
- Entry point: `main.py` (also exposes CLI via `project.scripts`)
- Packages/dirs:
  - `tools/` — Function tools used by the agent (filesystem, search, web, conversions, shell, slash commands)
  - `tests/` — Pytest-based unit tests for tools
  - `README.md` — Setup, install, and run instructions
  - `pyproject.toml` — Build metadata, dependencies, pytest config
- CLI commands (installed): `omni-code` and `omni` run the agent.

## How Agents Should Operate Here
- Prefer the provided workspace tools: `list_directory`, `glob_files`, `grep_files`, `read_file`, `apply_patch`, `write_file`, `execute_bash`.
- For any file edits or creation, use `apply_patch` (preferred) or `write_file`. Do not describe manual steps; make the change.
- Keep changes minimal, targeted, and consistent with existing code.
- Do not introduce unrelated refactors or additional dependencies.
- Never commit secrets. Keys are loaded from `.env` via `python-dotenv` in `main.py`.

## Code Style and Patterns
- Python style
  - Use snake_case for functions/variables and descriptive names, no single-letter identifiers.
  - Prefer type hints as seen in the codebase.
  - Keep functions cohesive and small; avoid unnecessary complexity.
  - Avoid adding inline comments unless explicitly requested in the prompt.
  - Do not add license/copyright headers.

- Tool functions (in `tools/`)
  - Decorate tools with `@rich_function_tool` (or `@function_tool` for async slash commands).
  - Return a `RichToolOutput(llm_output, ui_metadata)` where `ui_metadata` includes:
    - `value`, `display_type`, `summary`, `preview`, `truncated`, and `metadata` fields consistent with existing tools.
  - Where relevant, provide an approval metadata function via `@approval_metadata()` mirroring existing patterns.
  - Maintain and extend `tools/__init__.py` exports and keep names consistent.

- File/workspace tool defaults (important for tests)
  - `list_directory`: `recursive=False` if unset; `ignore_hidden=False`; `respect_gitignore=True`.
  - `glob_files`: default `ignore_hidden=False`, `respect_gitignore=True`.
  - `grep_files`: default `ignore_hidden=False`, `respect_gitignore=True`.
  - Preserve behavior that results are sorted by modification time (newest first) where implemented, and that hidden/gitignored handling respects provided flags.

- Apply patch format
  - Use the custom patch format implemented by `tools.file_tools.apply_patch`:
    - Wrap with `*** Begin Patch` and `*** End Patch`.
    - Use `*** Add File: path` with lines prefixed by `+` for new files.
    - Use `*** Update File: path` with `-`/`+`/space-prefixed context lines; include helpful `@@` headers where possible.
    - Use `*** Delete File: path` to remove files.

## Dependencies and Libraries
- Core dependencies (see `pyproject.toml`):
  - `omniagents[all]==0.3.5`, `agentio==0.1.1`
  - `openai` (or Azure OpenAI via env configuration), `python-dotenv`
  - `markitdown[all,docx,pdf]` for document/URL conversion to markdown
  - `requests`, `beautifulsoup4`
  - `pygit2` (requires system libgit2) used by workspace tools for `.gitignore` semantics
- External prerequisites the environment must provide:
  - Go 1.20+ on PATH (runner requirement in this project)
  - System libgit2 (see README for install instructions)

## Environment and Secrets
- Expected environment variables:
  - `OPENAI_PROVIDER` = `openai` (default) or `azure`
  - OpenAI: `OPENAI_API_KEY` (and optionally `OPENAI_BASE_URL`, `OPENAI_ORG`, `OPENAI_PROJECT`)
  - Azure OpenAI: `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_API_VERSION`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT`
  - Search tools: `SERPAPI_API_KEY`
  - Optional packaging index: `FURY_AUTH` (if using private Gemfury)
- `.env` at repo root is loaded by `dotenv` at runtime.

## Running and Testing
- Install (editable) for local development:
  - If using a private index for OmniAgents libs, set `FURY_AUTH` and pass `--extra-index-url` as shown in README.
  - Then: `pip install -e .` (or use `pipx`/`uv tool install` as documented in README).
- Run the CLI:
  - `omni-code` (installed script) or `python main.py`.
- Run tests (pytest config lives in `pyproject.toml`):
  - `pytest` or `pytest -q`
  - Tests monkeypatch OmniAgents decorators and framework classes; keep return shapes compatible with existing tools.

## Repo-Specific Conventions and Tips
- Tools folder
  - Place new tools in `tools/` with a focused module per tool or cohesive group.
  - Export new tools in `tools/__init__.py` and update `main.py` tool list if the agent should use them.
  - Follow existing `ui_metadata` schemas and naming (`display_type`, `summary`, etc.) for consistent UI.

- Workspace operations
  - Prefer `apply_patch` for multi-file edits or precise updates; include adequate context to help fuzzy matching.
  - Use `write_file` for simple new file writes when patching is unnecessary.
  - Avoid traversing hidden or gitignored paths unless explicitly requested; respect flags passed to the tools.

- Web/search/conversion tools
  - `web_search`, `youtube_search`, `scholar_search` require `SERPAPI_API_KEY` and must return a clear error when missing.
  - `convert_to_markdown` uses a shared `MarkItDown` instance; verify local file existence unless the input is a URL.
  - `web_fetch` should provide either extracted text (HTML) or raw content with correct content type metadata.

- Shell execution
  - `execute_bash` defaults to a 30s timeout if none is provided; return structured stdout/stderr/exit_code in metadata.

- Safety and approvals
  - The Runner may operate in safe mode; destructive tools will prompt for approval. Make small, reviewable changes.

- Output and verbosity
  - Keep assistant responses concise; this agent runs in a CLI/TUI. Provide summaries of what you will do before long operations.

- Git usage
  - Do not perform git operations (commits/branches) unless explicitly asked. The CI/tests do not require git commands.

## When In Doubt
- Imitate existing patterns in the nearest similar file.
- Check `README.md` for installation/runtime details that may affect tooling.
- Keep edits narrowly scoped and reversible.

