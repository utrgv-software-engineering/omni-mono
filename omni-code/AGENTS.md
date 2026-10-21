# AGENTS.md — Omni Code agent guidance

## What this repo is

omni-code is a full-stack coding agent shipped as a *product* of the
omniagents framework (pinned in `pyproject.toml`, currently
`omniagents[all]==0.7.22`; local reference checkout at
`../omniagents`, read-only). The product is YAML-first: almost
everything installable is declarative data inside the `omni_code/`
package — the framework supplies the runtime, the CLI, and the builtin
capability packs. This root file covers the whole repo; a nearer
`AGENTS.md` (e.g. `evaluations/AGENTS.md`) overrides it for its
directory.

## Branches and releases

Work on `dev`; `main` holds released code only. `make publish` refuses
to run off a clean `main` that is in sync with `origin/main`. Releases go
omniagents → omni-code → launcher; the steps are in the launcher's
CLAUDE.md ("Branches and releases").

## Repo map

- `omni_code/project.yml` — the manifest. Product identity follows the
  one-name convention: `name: omni-code` seeds the slug (`omni_code`),
  label, env prefix, and config dirs; only deviations are declared
  (`prog: omni`, the self-update index). Also declares the agent
  entrypoints (explicit on purpose), the sessions summary envelope
  (mirrored by the session_summarizer schema — a test enforces this),
  repo-only `paths` (`../evaluations`, `../target`), and declarative
  slash→agent bindings (`/init`).
- `omni_code/agents/<name>/` — agent specs (`agent.yml`) + Jinja
  markdown instructions. Agents: `omni` (entrypoint), `voice_agent`,
  `worker`, `explorer`, `init_agent`, `session_summarizer`, `recapper`,
  `compactor`. Shared prose lives in `agents/_shared/*.md` and is pulled
  in via `{% include %}`; keep shared sections verbatim-identical across
  omni/voice (tests assert it). A few agents carry agent-owned Python
  (`context.py`, `omni/runtime_hooks/`) referenced from their spec —
  that is wiring, not product logic.
- `omni_code/capabilities/` — the only capability overrides:
  `workers.yml` (extends the builtin `workers` pack with the coding
  delta) and `exploration.yml` (exposes the `explorer` agent as the
  `explore` tool). Every other entry in `omni/agent.yml`'s
  `capabilities:` list is a framework builtin pack.
- `omni_code/models.yml` — the declared model catalog (conventional
  sibling of project.yml; schema in `omniagents.core.models.catalog`).
  The per-user store stays `~/.config/omni_code/models.json` and merges
  over it.
- `omni_code/prompts/` — `goal_initial.md`, `goal_tick.md`, `loop.md`
  (conventional prompt templates).
- `omni_code/sandbox_profile_default.yml` — packaged base of the
  layered sandbox-profile chain `omni serve` resolves.
- `omni_code/__init__.py` + `_version.py` — the **entire** top-level
  Python surface of the package. Keep it that way.
- Repo-only (not packaged): `tests/`, `evaluations/`, `docs/`.

## CLI

There is no product-side dispatcher. The `omni` console script is the
framework's generic entry (`omniagents.product_cli:main`); identity is
discovered through the `omniagents.products` entry point
(`omni-code = "omni_code"`). Reserved subcommands mount from
convention: `model`, `provider`, `auth`, `update`, `sessions`, `serve`,
`describe`. Anything else falls through to the agent
run (`omniagents run --project <manifest> --mode ink ...`, so `--mode
web|server`, `--host`, `--port`, `--output json` etc. pass through);
`omni -- <words>` forces prompt mode even for reserved words.

## Checks

All four gates run in CI (`.github/workflows/omni-code-quality.yml` at the repository root) and must
pass locally before pushing; the tools are in the `dev` dependency
group and configured in `pyproject.toml`:

```bash
.venv/bin/black --check .        # format (fix with: black .)
.venv/bin/ruff check .           # lint (autofix with: ruff check --fix .)
.venv/bin/mypy                   # typecheck (targets come from pyproject)
PYTHONPATH=$PWD .venv/bin/pytest tests/
```

(Plain `pytest` also works — pyproject sets `pythonpath = ["."]`.)
`evaluations/fixtures/` is deliberately excluded from format, lint,
and typecheck: those trees are eval fixture workspaces — intentionally
imperfect code the agent operates on during scenarios — so never
"clean them up" to satisfy a tool.

Write targeted tests for the behavior you change; do not restructure
the suite. Several tests assert deterministic orderings and exact
strings — keep fixtures deterministic.

## Evaluations

`evaluations/` is the eval suite: scenarios in
`evaluations/scenarios/*.yml`, measures in `measures.py`, judges and
sandbox profiles alongside. The environment is declared in
`evaluations/evaluation.yml` and built by the framework
(`omniagents.core.eval.environment_config`) — container routing goes
through the same layered profile resolver `omni serve` uses. Run and
inspect per `evaluations/AGENTS.md` (that file is the operator guide),
e.g.:

```bash
python -m omniagents.cli eval suite run -P omni_code/project.yml \
  --select 'name:<scenario_name>' --reporter pretty
```

## Guardrails

- **Don't reintroduce Python for prompts, config, or wiring.** New
  behavior should be YAML/markdown declared in the manifest, an agent
  spec, or a capability pack. If it genuinely needs code, it almost
  certainly belongs in the framework, not here.
- **Keep entrypoints explicit.** `agents.entrypoint` /
  `voice_entrypoint` stay declared even though convention would resolve
  the same values — which agent runs is too load-bearing to be implicit.
- **No platform code.** The management-plane/platform/governance
  integration has been removed from the stack entirely. Do not
  resurrect `platform*`, `governance`, or overlay modules.
- **Scope edits tightly.** No opportunistic refactors or formatter
  passes; mirror the nearest working pattern.
- Python 3.10+, `snake_case`, type hints; match nearby style.
- Secrets arrive via env/`.env`; never commit keys or bake them into
  YAML/docs.

## E2E smoke (manual)

Use an isolated config dir so runs never touch your real
`~/.config/omni_code`. The user store (`models.json`, version 3) merges
over the shipped `models.yml` catalog, so seeding an
OpenAI-compatible provider is just:

```bash
rm -rf /tmp/omni-code-e2e && mkdir -p /tmp/omni-code-e2e/omni_code
cat > /tmp/omni-code-e2e/omni_code/models.json <<'JSON'
{
  "version": 3,
  "default": "sandbox/gpt-5.2",
  "voice_default": null,
  "providers": {
    "sandbox": {
      "type": "openai-compatible",
      "base_url": "${OPENAI_BASE_URL}",
      "api_key": "${OPENAI_API_KEY}",
      "models": { "gpt-5.2": { "model": "gpt-5.2" } }
    }
  }
}
JSON
export XDG_CONFIG_HOME=/tmp/omni-code-e2e
omni model list --json   # verify resolution before launching a UI
```

- **Web**: `OMNI_WEB_AUTO_OPEN=false omni --mode web --host 127.0.0.1
  --port 8091 --output json` (or pass `--embedded`, which implies both
  and emits the machine-readable readiness line). Wait for the port,
  then drive the page; the chat textarea placeholder is
  `Type a message…` — do not target the terminal textarea
  (`aria-label="Terminal input"`).
- **Ink**: must run under a real PTY (`tmux` preferred, `pexpect`
  fallback). With pexpect send `'\r'` for Enter — `sendline()` may
  render text without submitting. A clean proof shows the prompt,
  `Thinking...`, then the assistant line.
- **Server**: `omni --mode server --host 127.0.0.1 --port 8092` exposes
  the JSON-RPC WebSocket API at `/ws`. `start_run` requires a workspace
  root (`variables.workspace_root`, or `server_call` →
  `session.ensure` first); expect `run_started` → `message_output` →
  `run_end` with `end_reason: completed`.
- **Sandboxed serve**: `omni serve` starts a targetless AgentHost. A launcher
  registers Workspace/profile definitions and materializes environments through
  the control plane; see `docs/sandbox-switch-rpc.md` for lifecycle pointers.
