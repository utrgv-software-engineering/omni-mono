"""omni-code's CLI extensions, declared in project.yml (``cli.extensions``).

The framework's product CLI host (:mod:`omniagents.product_cli`) imports
this module and applies, in order: :data:`PREFLIGHT` (argv hooks run
before any dispatch), :data:`COMMANDS` (product subcommands and
overrides of reserved ones), then the framework's reserved commands,
then the fallthrough agent run.

Preflights self-gate with :func:`omniagents.product_cli.dispatches_to_command`
so wizard/embedded handling only fires on agent-run invocations — the
governance preflight runs unconditionally (it owns its own skip list)
and MUST stay first: if platform policy requires a sandbox it re-execs
or exits before anything else happens.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional


# omni-code's ModelConfigStore (catalog from omni_code/models.yml,
# platform governance, codex OAuth, credential resolver) is installed as
# the process-wide registry by the product bootstrap (`import
# omni_code`), which the framework's product CLI host runs before
# loading this module.


def _get_arg_value(args: List[str], *flags: str) -> Optional[str]:
    for i, arg in enumerate(args):
        if arg in flags and i + 1 < len(args):
            return args[i + 1]
    return None


def _is_agent_run(args: List[str]) -> bool:
    """Whether this invocation falls through to the chat runtime."""
    from omniagents.product_cli import dispatches_to_command

    return not dispatches_to_command(args, extra_commands=COMMANDS)


# ---------------------------------------------------------------------------
# PREFLIGHT — run in order before any dispatch
# ---------------------------------------------------------------------------


def governance_preflight(args: List[str]) -> None:
    """Governance: if platform policy requires sandbox, re-exec inside
    omni-sandbox.

    Must run before anything else — if it re-execs, this process is
    replaced. Skips for non-agent subcommands (auth, model, etc.)
    automatically. Fail-closed: if governance can't be enforced, this
    exits rather than silently running ungoverned.
    """
    from omni_code.governance import maybe_enforce_sandbox

    maybe_enforce_sandbox()
    return None


#: Set by the embedded preflight; read by the models preflight so a
#: missing-models condition surfaces as JSON for the Electron launcher
#: instead of an interactive wizard.
_embedded_mode = False


def embedded_preflight(args: List[str]) -> Optional[List[str]]:
    """Embedded (Electron launcher) mode: strip ``--embedded``, suppress
    browser auto-open and the startup update notice, and force JSON
    output for web mode unless --output was given explicitly."""
    global _embedded_mode
    if not _is_agent_run(args) or "--embedded" not in args:
        return None

    _embedded_mode = True
    args = [a for a in args if a != "--embedded"]
    os.environ["OMNI_WEB_AUTO_OPEN"] = "false"
    # The launcher owns update UX; the framework host prints the notice
    # on the fallthrough path unless the product env disables it.
    os.environ["OMNI_CODE_UPDATE_CHECK"] = "0"
    if (
        _get_arg_value(args, "--mode", "-m") == "web"
        and _get_arg_value(args, "--output") is None
    ):
        args = [*args, "--output", "json"]
    return args


def models_preflight(args: List[str]) -> None:
    """Load the model config (+ platform model governance) and export
    MODEL_NAME; when no models are configured, run the setup wizard
    (or fail with a JSON error in embedded mode)."""
    if not _is_agent_run(args):
        return None

    from omniagents.core.models import model_store

    store = model_store()
    store.load_config()

    # Apply platform model governance if authenticated
    try:
        from omni_code.platform_models import apply_platform_model_governance

        apply_platform_model_governance()
    except Exception:
        pass

    if "MODEL_NAME" not in os.environ:
        model_runtime = store.resolve_model_for_runtime()
        if model_runtime.get("model"):
            os.environ["MODEL_NAME"] = model_runtime["model"]

    if not store.has_models_configured():
        if _embedded_mode:
            import json

            print(json.dumps({"error": "No model credentials configured"}))
            sys.exit(1)
        print("No model credentials configured. Starting setup wizard...\n")
        from omniagents.core.models.cli import run_model_command

        run_model_command(["setup"], store=store)
        print()
    return None


PREFLIGHT = [governance_preflight, embedded_preflight, models_preflight]


# ---------------------------------------------------------------------------
# COMMANDS — product subcommands + overrides of reserved ones
# ---------------------------------------------------------------------------

# No overrides: the framework's reserved defaults are complete for
# omni-code.
#
# ``sessions`` is fully conventional — the reserved default pins the
# product slug as project and the manifest's entrypoint agent, the
# summarizer runs the conventionally-named
# ``omni_code/agents/session_summarizer`` agent (its instructions.md is
# the prompt), and the coding-flavored summary envelope is declared in
# project.yml (``sessions.summary_envelope``).
#
# ``serve`` and ``sandbox-host`` are launcher-complete framework
# defaults (omniagents.product_serve) — same argv surface, readiness
# payload, profile chain (packaged sandbox_profile_default.yml via the
# manifest-sibling convention), entrypoint spec, and codex registration
# (conventional on the catalog's openai-oauth provider).
COMMANDS: Dict[str, Any] = {}
