# AGENTS.md — Omni Code AI-Agent Guidance

## Purpose
- Provide clear, repo-aware guardrails so automated edits land cleanly and keep the suite green.
- Capture the implicit expectations maintainers have for code, tooling, docs, and approval safety nets.

## Scope
- This root policy covers the whole repository unless a nearer directory ships its own `AGENTS.md`.
- Only add another `AGENTS.md` when a sub-package genuinely needs different rules; avoid copy-paste redundancy.
- When changes touch setup, release, or CLI behavior, cross-check the instructions in `README.md` and `docs/`.

## Precedence
1. System instructions
2. Developer instructions
3. User instructions
4. Nearest `AGENTS.md` (this file unless a closer one exists)
5. Other repo docs (README, `docs/`, code comments)
6. Inferred conventions from existing implementations and tests

If directives conflict, prefer the one earlier in the list. When editing files inside a directory with its own `AGENTS.md`, that nearer file overrides this root guidance.

## Code Style & Quality
- Python targets 3.10+. Follow `snake_case`, descriptive names, and mirror nearby formatting and import order.
- Prefer type hints and structured responses. Tool modules should emit `RichToolOutput` metadata consistent with peers.
- Keep edits tightly scoped to the requested task; no opportunistic refactors or wholesale formatter passes unless instructed.
- Use docstrings and precise naming to convey intent. Add inline comments only when behavior is non-obvious.
- Maintain ordering expectations—several tests assert deterministic file listings, metadata keys, and error strings.

## Tooling Expectations
- Operate through the provided workspace helpers: `list_directory`, `glob_files`, `grep_files`, `read_file`, `apply_patch`, `write_file`, etc. Invoke `execute_bash` only when a request explicitly calls for shell execution.
- Apply edits with the custom patch format handled by `tools.file_tools.apply_patch`:
  - Wrap changes with `*** Begin Patch` / `*** End Patch`.
  - Use `*** Update File: path` for modifications, `*** Add File:` when creating files, and `*** Delete File:` for removals.
- Keep dependency versions aligned with `pyproject.toml`. Published requirements currently include `openai==1.99.9`, `omniagents[all]==0.3.16`, `markitdown[all,docx,pdf]>=0.1.1`, `requests>=2.31.0`, `beautifulsoup4>=4.12.0`, and `pygit2>=1.13.0`.
- External prerequisites: Python 3.10+, Go 1.20+ on `PATH`, and `libgit2` (required by file tools). Assume credentials arrive via `.env`/`python-dotenv`; never hard-code or commit keys (`OPENAI_API_KEY`, `AZURE_OPENAI_*`, `SERPAPI_API_KEY`, etc.).

## Testing & Validation
- Install for development with `pip install -e .[dev] --extra-index-url https://pypi.fury.io/ericmichael/` while keeping PyPI as the default index.
- Primary entrypoints are the `omni` console script (`main:cli_main`) and `python main.py`.
- Run validations with `pytest -q`. Tests exercise tool metadata, deterministic ordering (especially in `tools/file_tools`), hidden-file defaults (`ignore_hidden=False`, `respect_gitignore=True`), and approval metadata wiring.
- When extending functionality, add or update tests under `tests/` to cover new behavior, including UI metadata and error cases. Keep fixtures deterministic.

## Repo-Specific Conventions
- **Project map:** Key areas include `tools/` (rich tool implementations), `oagents/` and `oagents/instructions/` (agent factories plus markdown instructions), `server_functions/` (terminal/AgentIO adapters), `omni_code/` (setup tooling, CLI helpers), `docs/`, `tests/`, and the top-level `main.py` entrypoint.
- **Tool authoring:** Implement one tool per module in `tools/`. Decorate callable tools with `@rich_function_tool` (or `@function_tool` for slash commands), return `RichToolOutput` with populated `ui_metadata` (`value`, `display_type`, `summary`, `preview`, `truncated`, `metadata`), and add `@approval_metadata` when similar tools require approval. Export new tools via `tools/__init__.py` and ensure packaging metadata (`pyproject.toml`, `main.py`) is updated.
- **File tooling contract:** Preserve semantics in `tools/file_tools.py`—binary detection, modification-time sorting, hidden/gitignore defaults, and error messaging are covered by tests and downstream consumers.
- **Server functions & CLI:** Follow the patterns in `main.py` and `server_functions/` when registering terminal helpers. Only add entries to `TERMINAL_SERVER_FUNCTIONS` (and similar registries) when implementations are production-ready.
- **Web/search/document tools:** Modules like `web_fetch.py`, `web_search.py`, `youtube_search.py`, `scholar_search.py`, `download_file.py`, and `conversion_tools.py` must validate inputs, surface actionable errors (especially when API keys are missing), and provide succinct summaries/previews.
- **Docs & instructions:** Keep `README.md`, `docs/`, release procedures (`docs/RELEASE.md`), and `oagents/instructions/*.md` aligned with behavioral changes. Update user-facing narratives when altering CLI flows or configuration paths.
- **Safety & approvals:** High-impact or destructive operations rely on approval metadata. Maintain conservative defaults and document rationale for actions that could alter user workspaces.
- **Secrets & git hygiene:** Never run git commands or manipulate credentials. Respect `.env` usage, avoid logging sensitive data, and do not bake secrets into code or docs.

## When in Doubt
- Mirror the nearest working pattern before inventing new abstractions.
- Re-read the relevant instructions or docs (README, `docs/`, existing agent markdown) before modifying setup, release, or CLI behavior.
- Keep changes minimal, reversible, and directly tied to the explicit request. If uncertainty persists, prefer the safest no-op.
